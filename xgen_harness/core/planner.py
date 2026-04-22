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
    source: str = "llm"
    planner_model: str = ""

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
    },
    "required": ["chosen", "reasoning"],
}


def build_plan_tool() -> dict[str, Any]:
    """provider.chat(tools=[...]) 에 넘길 도구 정의. Anthropic 포맷 기준 — OpenAI 변환은 provider 내부."""
    return {
        "name": PLAN_TOOL_NAME,
        "description": PLAN_TOOL_DESCRIPTION,
        "input_schema": PLAN_TOOL_INPUT_SCHEMA,
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

    # 시스템 프롬프트 최소화 — 역할 1줄 + 규약 1줄. 선택 기준은 카탈로그가 말한다.
    SYSTEM_PROMPT = (
        "당신은 XGEN Harness Planner 입니다. "
        "아래 카탈로그의 each stage 의 when_to_use / when_to_skip / cost_hint 를 읽고 "
        "submit_plan 도구로 이번 턴 실행 계획을 제출하세요."
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

        tool_input = await self._invoke_tool(provider, user_input, catalog)
        if tool_input is None:
            return HarnessPlan.fallback_all("Planner did not call submit_plan")

        plan = self._build_plan_from_tool_input(tool_input, catalog)
        plan.planner_model = getattr(provider, "model_name", "")
        return plan

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
            source="llm",
        )
