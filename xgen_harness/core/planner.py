"""
HarnessPlanner — LLM 이 카탈로그를 보고 Stage/파라미터/도구를 런타임 조립.

철학 (REAL_HARNESS §1.3):
  "지도도 하드코딩 X (자동 발견), 선택도 하드코딩 X (LLM 이 런타임 결정)"

설계 (v0.12.0):
  - 시스템 프롬프트는 **1~2 줄**. 선택 기준은 각 Stage 가 self-describing 필드
    (when_to_use / when_to_skip / cost_hint) 로 카탈로그에서 직접 선언한다.
  - LLM 에게 `submit_plan` 도구 하나만 노출 — JSON 파싱·정규식 폴백 불필요.
    도구 스키마는 `HarnessPlan` 에서 자동 파생 (엔진 어디에도 JSON 스키마 중복 없음).
  - 환경이 스스로 말하고, 계약은 도구 스키마가 보장한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .state import PipelineState
    from ..providers.base import LLMProvider

logger = logging.getLogger("harness.planner")


# ───────────────────────────────────────────────────────────────────
#  Plan dataclass
# ───────────────────────────────────────────────────────────────────

@dataclass
class HarnessPlan:
    """LLM 이 카탈로그를 보고 만든 실행 계획.

    Attributes
    ----------
    chosen : list[str]
        실행할 stage_id. 빈 리스트 = fallback (전체 실행).
    skipped : dict[str, str]
        stage_id → 스킵 이유. 프론트 표시용.
    params : dict[str, dict[str, Any]]
        stage_id → 파라미터 override. Pipeline 실행 전 state.config.stage_params 에 병합.
        도구/리소스 선택 (mcp_sessions / rag_collections / selected_custom_tools /
        capabilities 등) 도 이 dict 에 들어간다.
    strategies : dict[str, str]
        stage_id → Strategy 이름. state.config.active_strategies 에 병합.
    reasoning : str
        선택 근거. 사람 납득용 (explainability).
    done : bool
        iterative planning 종료 신호. Planner 가 "이제 더 이상 돌 필요 없다" 판단하면
        True. Pipeline.Phase B 가 이 값을 보고 loop 를 끊는다.
    source : str
        "llm" | "fallback_all" | "error". Plan 출처 추적.
    planner_model : str
        Plan 을 만든 모델 식별자 (디버그/감사용).
    """
    chosen: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    params: dict[str, dict[str, Any]] = field(default_factory=dict)
    strategies: dict[str, str] = field(default_factory=dict)
    reasoning: str = ""
    done: bool = False
    source: str = "llm"
    planner_model: str = ""
    # v0.15.0 — 자율주행 확장. LLM 이 이번 요청에 "몇 번 돌면 충분한지"와
    # "어떤 실행 패턴이 어울리는지" 를 직접 판단해 주입한다.
    # None / "" 이면 config 기본값 유지.
    max_iterations: Optional[int] = None
    orchestrator_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def fallback_all(cls, reason: str = "Planner fallback — running all stages") -> "HarnessPlan":
        """LLM 호출/파싱 실패 시 안전망. 빈 chosen 은 Pipeline 에서 '전체 실행'."""
        return cls(chosen=[], reasoning=reason, source="fallback_all")


# ───────────────────────────────────────────────────────────────────
#  submit_plan 도구 스키마 — HarnessPlan 에서 자동 파생
# ───────────────────────────────────────────────────────────────────

PLAN_TOOL_NAME = "submit_plan"

PLAN_TOOL_DESCRIPTION = (
    "이번 턴의 실행 계획을 제출. 카탈로그의 각 Stage 가 선언한 "
    "when_to_use / when_to_skip / cost_hint 를 기준으로 선택하라. "
    "파라미터 override · 도구 선택(MCP/RAG/custom) · Strategy 모두 params 에 기록."
)

PLAN_TOOL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "chosen": {
            "type": "array",
            "items": {"type": "string"},
            "description": "실행할 stage_id 순서. required_stages 는 반드시 포함.",
        },
        "skipped": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "stage_id → 스킵 이유.",
        },
        "params": {
            "type": "object",
            "additionalProperties": {"type": "object"},
            "description": (
                "stage_id → 파라미터 override dict. Stage 의 fields.default 와 다를 때만. "
                "도구 선택(mcp_sessions / rag_collections / selected_custom_tools / "
                "capabilities / node_tags 등) 도 이 안에 기록."
            ),
        },
        "strategies": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "stage_id → Strategy 이름 (active_strategies 에 병합).",
        },
        "reasoning": {
            "type": "string",
            "description": "왜 이 조합·파라미터·도구를 선택했는지 사람이 납득할 수 있도록.",
        },
        "done": {
            "type": "boolean",
            "description": (
                "이번 iter 이후 **더 이상 루프를 돌 필요가 없다** 판단 시 true. "
                "첫 Plan 에서는 보통 false. 이전 실행 결과(messages/tool_results/"
                "validation_score)가 충분히 사용자 요청을 만족한다고 보이면 true 로 "
                "돌리면 Pipeline 이 즉시 종료한다."
            ),
        },
        "max_iterations": {
            "type": "integer",
            "minimum": 1,
            "maximum": 50,
            "description": (
                "이번 요청에 **적정한 최대 반복 횟수**. 단발성 질의는 1~2, "
                "도구 연쇄·reflection 필요한 복잡 요청은 5~10, 리서치/리포트는 10~20. "
                "미지정 시 HarnessConfig.max_iterations 기본값(10) 사용."
            ),
        },
        "orchestrator_hint": {
            # enum 은 `build_plan_tool()` 이 런타임에 OrchestratorRegistry 에서 동적 주입.
            # 여기에 리터럴 목록을 박지 않아 외부 플러그인이 즉시 합류 가능.
            "type": "string",
            "description": (
                "실행 패턴 힌트. 빈 문자열이면 이식측 기본 dispatcher. "
                "유효 값은 OrchestratorRegistry 에서 동적 발견 — 엔진 기본 + 외부 플러그인 합산."
            ),
        },
    },
    "required": ["chosen", "reasoning"],
}


def build_plan_tool() -> dict[str, Any]:
    """provider.chat(tools=[...]) 에 넘길 도구 정의.

    v0.15.0 자동 연동 자동 확장성 — `orchestrator_hint.enum` 을 매 호출마다
    OrchestratorRegistry 에서 **런타임 조회**해 주입한다. 외부 패키지가
    `register_orchestrator("my_pattern")` 한 줄만 해도 즉시 LLM 선택지에 합류.
    """
    from .orchestrator_registry import list_orchestrators, get_orchestrator_specs

    # 얕은 복사로 동적 enum 주입 — 원본 상수는 불변.
    props = {k: dict(v) if isinstance(v, dict) else v
             for k, v in PLAN_TOOL_INPUT_SCHEMA["properties"].items()}
    orch_names = list_orchestrators()
    if orch_names:
        orch_field = dict(props.get("orchestrator_hint") or {})
        orch_field["enum"] = orch_names
        # 각 enum 값의 설명을 description 에 합성 — LLM 이 고를 때 무슨 의미인지 바로 봄.
        specs = get_orchestrator_specs()
        legend = "; ".join(f"{s['name']}={s['description']}" for s in specs if s.get("description"))
        if legend:
            orch_field["description"] = (
                (orch_field.get("description") or "") + " 유효 값: " + legend
            )
        props["orchestrator_hint"] = orch_field

    schema = {
        **PLAN_TOOL_INPUT_SCHEMA,
        "properties": props,
    }
    return {
        "name": PLAN_TOOL_NAME,
        "description": PLAN_TOOL_DESCRIPTION,
        "input_schema": schema,
    }


# ───────────────────────────────────────────────────────────────────
#  Planner 본체
# ───────────────────────────────────────────────────────────────────

class HarnessPlanner:
    """카탈로그와 user_input 을 보고 Plan 을 내놓는 LLM 계획자.

    이 클래스는 provider 를 보유하지 않고 state.provider 를 사용 (프로바이더 독립).
    LLM 은 `submit_plan` 도구를 호출해 Plan 을 구조화된 형태로 반환 — 프롬프트
    하드코딩과 JSON 파싱 실패 경로가 사라진다.
    """

    DEFAULT_MAX_TOKENS = 2048
    DEFAULT_TEMPERATURE = 0.1

    # 시스템 프롬프트 최소화 — 역할 + iterative 맥락 + 규약. 선택 기준은 카탈로그가 말한다.
    SYSTEM_PROMPT = (
        "당신은 XGEN Harness Planner 입니다. "
        "카탈로그의 각 stage 가 선언한 when_to_use / when_to_skip / cost_hint 를 읽고 "
        "submit_plan 도구로 이번 turn 실행 계획을 제출하세요. "
        "이전 실행 결과(previous_results)가 있으면 그걸 참고해 **다음에 무엇을 할지** "
        "결정하고, 사용자 요청을 충분히 만족했다면 done=true 로 종료하세요."
    )

    async def plan(
        self,
        *,
        state: "PipelineState",
        user_input: str,
        workflow_hints: Optional[dict[str, Any]] = None,
    ) -> HarnessPlan:
        """Plan 을 반환. 실패해도 예외 raise 하지 않고 fallback Plan 반환."""
        from .catalog import get_catalog_async
        from .provider_bootstrap import ensure_provider

        try:
            await ensure_provider(state, stage_id="s00_harness")
        except Exception as e:
            logger.warning("[Planner] provider init failed: %s", e)
            return HarnessPlan.fallback_all(f"Provider init failed: {e}")

        provider = state.provider
        if provider is None:
            return HarnessPlan.fallback_all("Provider unavailable")

        catalog = await get_catalog_async(
            config=state.config,
            user_input=user_input,
            workflow_hints=workflow_hints,
        )

        # v0.13.0 iterative planning — 이전 실행 결과를 Planner 에 주입해
        # "이미 뭐 했는지" 보고 다음 행동 결정. 첫 iter 에서는 빈 dict 라 영향 없음.
        previous = self._collect_previous_results(state)
        if previous:
            catalog["previous_results"] = previous

        tool_input = await self._invoke_tool(provider, user_input, catalog)
        if tool_input is None:
            return HarnessPlan.fallback_all("Planner did not call submit_plan")

        plan = self._build_plan_from_tool_input(tool_input, catalog)
        plan.planner_model = getattr(provider, "model_name", "")
        return plan

    def _collect_previous_results(self, state: "PipelineState") -> dict[str, Any]:
        """iterative replan 용 이전 실행 snapshot.

        첫 호출(loop_iteration=1) 에서는 빈 dict 반환 → LLM 은 "처음 실행" 으로 판단.
        두 번째 이후 호출에서는 messages 요약·tool_results·validation_score·token_usage
        등을 실어 보내 "이미 뭐 했는지" 를 기반으로 다음 Plan 을 세우도록 한다.
        """
        if getattr(state, "loop_iteration", 0) <= 1:
            return {}
        try:
            last_msg = state.messages[-1] if state.messages else None
            last_assistant = state.last_assistant_text[:400] if state.last_assistant_text else ""
            tool_summary = [
                {"name": r.get("tool_name", ""), "is_error": bool(r.get("is_error"))}
                for r in (state.tool.results or [])[-5:]
            ]
            return {
                "iteration": state.loop_iteration,
                "last_assistant_preview": last_assistant,
                "tool_calls_so_far": len(state.tool.results or []),
                "recent_tool_calls": tool_summary,
                "validation_score": state.validation.score,
                "validation_feedback": (state.validation.feedback or "")[:200],
                "retry_count": state.validation.retry_count,
                "total_tokens": state.token_usage.total,
                "rag_snippet_loaded": bool(state.rag_context),
            }
        except Exception as e:
            logger.debug("[Planner] previous_results 수집 실패: %s", e)
            return {}

    # ── LLM 호출 (Tool-Use) ────────────────────────────────────────

    async def _invoke_tool(
        self,
        provider: "LLMProvider",
        user_input: str,
        catalog: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """submit_plan 도구를 required 로 강제해 LLM 이 도구 input 을 채우게 한다.

        provider.chat 의 TOOL_USE 이벤트를 소비해 tool_input dict 반환.
        도구 호출이 없으면 None → Planner 가 fallback 선택.
        """
        import json
        from ..providers.base import ProviderEventType

        # 카탈로그는 JSON 문자열로 한 번에 삽입. 프롬프트 가이드 없음 —
        # 각 Stage 의 when_to_use 가 이미 내장되어 있으므로 LLM 이 자기 기준으로 선택.
        user_msg = (
            f"[사용자 요청]\n{user_input}\n\n"
            f"[카탈로그]\n{json.dumps(catalog, ensure_ascii=False, indent=2)}"
        )

        tool_input: Optional[dict[str, Any]] = None

        try:
            async for event in provider.chat(
                messages=[{"role": "user", "content": user_msg}],
                system=self.SYSTEM_PROMPT,
                tools=[build_plan_tool()],
                temperature=self.DEFAULT_TEMPERATURE,
                max_tokens=self.DEFAULT_MAX_TOKENS,
                stream=True,
                thinking=None,
                tool_choice=PLAN_TOOL_NAME,  # provider 가 {required|tool_name} 정규화
            ):
                if event.type == ProviderEventType.TOOL_USE:
                    if event.tool_name == PLAN_TOOL_NAME and isinstance(event.tool_input, dict):
                        tool_input = event.tool_input
                        # 첫 도구 호출만 채택
                        break
                elif event.type == ProviderEventType.ERROR:
                    logger.warning("[Planner] provider error: %s", event.text)
                    return None
        except Exception as e:
            logger.warning("[Planner] LLM invocation failed: %s", e)
            return None

        return tool_input

    # ── Plan 구성 ──────────────────────────────────────────────────

    def _build_plan_from_tool_input(
        self,
        tool_input: dict[str, Any],
        catalog: dict[str, Any],
    ) -> HarnessPlan:
        """submit_plan 의 tool_input 을 HarnessPlan 으로 정규화.

        타입 방어 + 알려지지 않은 stage_id 제거 + 필수 Stage 강제 + 순서 보정.
        JSON 파싱 경로가 사라졌으므로 여기 로직이 짧아진다.
        """
        chosen = tool_input.get("chosen") or []
        skipped = tool_input.get("skipped") or {}
        params = tool_input.get("params") or {}
        strategies = tool_input.get("strategies") or {}
        reasoning = tool_input.get("reasoning") or ""
        done = bool(tool_input.get("done"))

        # v0.15.0 자율주행 — LLM 이 적정 반복 수 / 오케스트레이터 힌트 결정
        raw_max_iter = tool_input.get("max_iterations")
        max_iterations: Optional[int] = None
        if isinstance(raw_max_iter, int) and 1 <= raw_max_iter <= 50:
            max_iterations = raw_max_iter
        orchestrator_hint = tool_input.get("orchestrator_hint") or ""
        if not isinstance(orchestrator_hint, str):
            orchestrator_hint = ""
        # v0.15.0 — 레지스트리 기반 검증. 하드코딩 리터럴 없음. 외부 플러그인이
        # 등록한 이름도 즉시 허용된다 (자동 연동 자동 확장성).
        if orchestrator_hint:
            from .orchestrator_registry import list_orchestrators
            if orchestrator_hint not in list_orchestrators():
                orchestrator_hint = ""

        if not isinstance(chosen, list):
            chosen = []
        if not isinstance(skipped, dict):
            skipped = {}
        if not isinstance(params, dict):
            params = {}
        if not isinstance(strategies, dict):
            strategies = {}
        if not isinstance(reasoning, str):
            reasoning = str(reasoning)

        # 알려진 stage_id 로 필터 (환각 방지)
        known_ids = {s["stage_id"] for s in catalog.get("stages", [])}
        if known_ids:
            chosen = [sid for sid in chosen if sid in known_ids]
            skipped = {sid: r for sid, r in skipped.items() if sid in known_ids}
            params = {sid: p for sid, p in params.items()
                      if sid in known_ids and isinstance(p, dict)}
            strategies = {sid: s for sid, s in strategies.items()
                          if sid in known_ids and isinstance(s, str)}

        # 필수 Stage 강제 포함
        for sid in catalog.get("required_stages", []) or []:
            if sid in known_ids and sid not in chosen:
                chosen.append(sid)
                skipped.pop(sid, None)

        # chosen 순서 보정 — stage order 기준
        order_map = {s["stage_id"]: s.get("order", 0) for s in catalog.get("stages", [])}
        chosen.sort(key=lambda sid: order_map.get(sid, 0))

        return HarnessPlan(
            chosen=chosen,
            skipped=skipped,
            params=params,
            strategies=strategies,
            reasoning=reasoning,
            done=done,
            source="llm",
            max_iterations=max_iterations,
            orchestrator_hint=orchestrator_hint,
        )
