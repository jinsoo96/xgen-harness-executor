"""
S05 Policy — Policy Gate (v0.17.0)

## 책임
선언형 Guard 체인을 4 개 훅 포인트에 집행한다:
  PRE_MAIN       — 본문 LLM 호출 직전 (입력/Plan 정책)
  PRE_TOOL       — 도구 호출 직전 (pending_tool_calls 각 항목)
  POST_RESPONSE  — LLM 응답 직후 (출력 검증)
  LOOP_BOUNDARY  — 루프 경계 (예산/반복/집계 정책)

## 호출 규약
Pipeline 이 `stage.role == "policy_gate"` 인스턴스를 찾아 `invoke_hook(state, hook)`
를 직접 호출한다 — 일반 loop 순서에서는 bypass. (orchestrator_planner 와 동일 패턴.)

## stage_params 계약
```
stage_params["s05_policy"] = {
    "guards": [
        {"name": "iteration"},
        {"name": "cost_budget", "params": {"cost_budget_usd": 5.0}},
        {"name": "tool_precondition", "params": {"rules": [...]}},
    ]
}
```
`guards` 항목 각각은 `{"name": <guard name>, "params": <dict>}`. Guard name 은
`xgen_harness.guards` entry_points 그룹에 등록된 이름 (내장 5 종 + 외부 갤러리).
"""

from __future__ import annotations

import logging
from typing import Any

from ...core.stage import Stage, StrategyInfo
from ...core.state import PipelineState

logger = logging.getLogger("harness.stage.policy")


class PolicyGateStage(Stage):
    """정책 게이트 — 도구 호출·LLM 호출·응답·루프 경계 4 훅 시점에 Guard 체인을 집행하는 특수 Stage. 일반 loop 순번에는 끼지 않고 (자동 bypass), guards 파라미터가 선언됐을 때만 Pipeline 이 훅별로 개별 호출. 감사·예산·콘텐츠 정책이 필요할 때 활성화.

    UI 실행 요약의 "Skipped (condition unmet)" 은 "guards 가 비어있어서 훅이 아무 검사도 하지 않았다" 는 뜻 — 오류 아님. guards 를 선언하려면 s05_policy.guards stage_param 에 Guard 이름 리스트를 넘긴다.
    """

    # Machine meta — LLM(planner) 이 Auto 모드에서 읽는 선택 근거.
    when_to_use = "도구 호출 순서 정책·콘텐츠 정책·예산/반복 제한 등 규제·감사 요구가 있을 때"
    when_to_skip = "단순 Q&A 또는 내부 프로토타이핑"
    cost_hint = "low"

    @property
    def stage_id(self) -> str:
        return "s05_policy"

    @property
    def role(self) -> str:
        # Pipeline 이 이 role 로 찾아 `invoke_hook(state, hook)` 를 여러 시점에 직접 호출.
        return "policy_gate"

    @property
    def order(self) -> int:
        # order=5 는 s05_strategy 와 겹치지만 Pipeline 이 role 로 찾아 호출하므로 문제 없음.
        # loop_stages 순회에서는 should_bypass 가 True 라 skip 된다.
        return 5

    @classmethod
    def param_schema(cls) -> list:
        """LLM 이 파라미터 조립 시 참조하는 스키마 + UI 동적 폼 렌더 기반.

        자연어 리터럴(label/description/placeholder) 없음 — UI 는 id 자동 렌더.
        `options_source` 만 남겨 외부 Guard 자동 합류 보장.
        """
        from ..strategies.guard import FieldSchema
        return [
            FieldSchema(
                id="guards",
                type="guard_list",
                options_source="guards_available",
                default=[],
            ),
        ]

    def should_bypass(self, state: PipelineState) -> bool:
        # 일반 loop 순서에서는 항상 bypass — Pipeline 이 role 로 3 훅에 별도 호출.
        return True

    async def execute(self, state: PipelineState) -> dict:
        # 방어적 폴백 — bypass 가 안 된 경우에만 도달. 실질 로직은 invoke_hook.
        return {"bypassed": True, "reason": "Policy Gate 는 Pipeline 훅 경로로만 호출"}

    async def invoke_hook(self, state: PipelineState, hook_name: str) -> dict[str, Any]:
        """Pipeline 이 훅 시점에 호출하는 진입점.

        반환값은 진단용. 실제 효과는 state 변이:
          - PRE_TOOL 차단: state.pending_tool_calls 에서 해당 tc 제거 +
                           state.add_tool_result(is_error=True) 로 가짜 결과 주입
          - PRE_MAIN / POST_RESPONSE / LOOP_BOUNDARY 차단:
            state.policy_block_reason / state.policy_block_guard 설정,
            LOOP_BOUNDARY 는 state.loop_decision = "complete" 도 설정.
        """
        from ..strategies.guard import HookPoint, build_guard_chain

        try:
            hook = HookPoint(hook_name)
        except ValueError:
            logger.warning("[PolicyGate] 알 수 없는 훅: %s", hook_name)
            return {"error": f"unknown hook: {hook_name}"}

        guard_configs = self.get_param("guards", state, []) or []
        if not guard_configs:
            return {"hook": hook.value, "guards": 0}

        chain = build_guard_chain(guard_configs)
        if not chain.guards:
            return {"hook": hook.value, "guards": 0}

        # PRE_TOOL 은 도구 호출별로 개별 검사 (v0.24.0 — HITL 대기 지원 async 경로)
        if hook == HookPoint.PRE_TOOL:
            return await self._check_pre_tool(state, chain)

        # 그 외 단일 검사
        results = chain.invoke(hook, state)
        blocked = next((r for r in results if not r.passed and r.severity == "block"), None)
        if blocked:
            state.policy_block_reason = blocked.reason
            state.policy_block_guard = blocked.guard_name
            logger.warning(
                "[PolicyGate] %s 차단 @ %s — %s",
                blocked.guard_name, hook.value, blocked.reason,
            )
            if hook == HookPoint.LOOP_BOUNDARY:
                state.loop_decision = "complete"
        return {
            "hook": hook.value,
            "guards_checked": len(chain.guards),
            "blocked": bool(blocked),
            "blocked_guard": blocked.guard_name if blocked else "",
        }

    async def _check_pre_tool(self, state: PipelineState, chain) -> dict[str, Any]:
        """pending_tool_calls 각 항목에 대해 Guard 체인 실행.

        v0.24.0 — invoke_async 사용. HITL 같은 await 필요 Guard 지원.
        기존 sync Guard 는 Guard.check_async 기본 구현이 check() 래핑이라 무영향.

        차단된 tc 는 pending 에서 제거하고 가짜 tool_result (is_error=True) 주입.
        LLM 은 다음 턴에 에러 결과를 보고 스스로 교정 시도.
        """
        from ..strategies.guard import HookPoint

        pending = list(state.pending_tool_calls or [])
        if not pending:
            return {"hook": HookPoint.PRE_TOOL.value, "checked": 0}

        blocked_ids: set[str] = set()
        checked = 0
        for tc in pending:
            checked += 1
            results = await chain.invoke_async(HookPoint.PRE_TOOL, state, pending_tool_call=tc)
            blocked = next((r for r in results if not r.passed and r.severity == "block"), None)
            if not blocked:
                continue

            tool_use_id = tc.get("tool_use_id", "")
            tool_name = tc.get("tool_name", "?")
            msg = blocked.tool_error_message or blocked.reason or "Blocked by policy"
            content = f"[BLOCKED by {blocked.guard_name}] {msg}"

            state.add_tool_result(tool_use_id, content, is_error=True)
            blocked_ids.add(tool_use_id)
            logger.warning(
                "[PolicyGate] pre_tool 차단: %s (tool=%s) — %s",
                blocked.guard_name, tool_name, blocked.reason,
            )

        if blocked_ids:
            # 차단된 항목 제거
            remaining = [tc for tc in pending if tc.get("tool_use_id") not in blocked_ids]
            state.pending_tool_calls = remaining
            # 전부 차단됐으면 tool_results 를 messages 에 flush
            # (s07_act 는 pending 비어있으면 bypass 되므로 수동 flush 필요)
            if not remaining:
                state.flush_tool_results()

        return {
            "hook": HookPoint.PRE_TOOL.value,
            "guards_checked": len(chain.guards),
            "checked": checked,
            "blocked": len(blocked_ids),
        }

    def list_strategies(self) -> list[StrategyInfo]:
        # Strategy Variants 대신 Guard 조합으로 구성 — Strategy 슬롯 없음.
        return []
