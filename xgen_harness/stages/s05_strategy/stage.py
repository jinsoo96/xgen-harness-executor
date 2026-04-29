"""
S05 Plan — 계획 수립 단계

지원 모드:
- auto: complexity에 따라 none/cot/react 결정
- none: 계획 단계 비활성
- cot: Chain-of-Thought 지시 주입
- react: ReAct 지시 주입
- capability: 자연어 intent → CapabilityMatcher로 capability 자동 발견 + 동적 바인딩
"""

import logging

from ...capabilities import (
    CapabilityMatcher,
    MatchStrategy,
    get_default_registry,
    materialize_capabilities,
    merge_into_state,
)
from ...core.stage import Stage, StrategyInfo
from ...core.state import PipelineState

logger = logging.getLogger("harness.stage.plan")


class PlanStage(Stage):

    @property
    def stage_id(self) -> str:
        return "s05_strategy"

    @property
    def order(self) -> int:
        return 5

    def should_bypass(self, state: PipelineState) -> bool:
        # 2번째 루프부터는 bypass (재계획 불필요)
        return state.loop_iteration > 1

    async def execute(self, state: PipelineState) -> dict:
        # ── Intent Routing (RR2) ──
        # 쿼리 의도를 경량 키워드 규칙으로 분류해 s06 이 쓸 metadata_filter 자동 생성.
        # 규칙 선언: stage_params.s05_strategy.intent_rules = [
        #   {"keywords": ["상품", "product"], "filter": {"file_name": "products.csv"}},
        #   {"keywords": ["리뷰", "review"],  "filter": {"file_name": "reviews.csv"}},
        # ]
        # 매칭되면 state.metadata["auto_metadata_filter"] 에 저장. s06 이 stage_params 의
        # metadata_filter 가 비어있을 때만 이 값을 fallback 으로 사용 (명시 설정 우선).
        await self._apply_intent_routing(state)

        # v0.29.3 — Strategy 카드 (active_strategies) 가 picked 됐으면 그 값을
        # planning_mode 로 매핑해서 사용. 이전엔 카드 픽이 stage 코드에 도달 안 해
        # vestigial UI 였음. 이제 카드 = 모드 단축 프리셋:
        #   cot_planner → cot / react → react / capability → capability / none → none
        # 카드 미픽 또는 알 수 없는 값이면 planning_mode 필드 (default=auto) 폴백.
        mode = self._resolve_planning_mode(state)

        # "auto" 모드: input_complexity에 따라 planning depth 결정
        if mode == "auto":
            complexity = state.metadata.get("input_complexity", "moderate")
            if complexity == "simple":
                mode = "none"
            elif complexity == "complex":
                mode = "react"
            else:
                mode = "cot"
            logger.info("[Plan] auto mode resolved to '%s' (complexity=%s)", mode, complexity)

        # capability 모드는 먼저 시도 (성공 여부 무관하게 CoT와 병행 가능)
        cap_result = None
        if mode == "capability" or self.get_param("capability_discovery", state, False):
            cap_result = await self._discover_and_bind_capabilities(state)

        # mode == "none" or mode == "capability" (capability 전용 모드): 계획 지시 skip
        if mode == "none":
            logger.info("[Plan] planning_mode=none, bypassed")
            return {"planning_enabled": False, "planning_mode": "none",
                    **(cap_result or {})}

        if mode == "capability":
            logger.info("[Plan] capability-only mode")
            return {
                "planning_enabled": True,
                "planning_mode": "capability",
                **(cap_result or {}),
            }

        # mode == "react": ReAct-style prompt (복잡한 멀티스텝 태스크)
        if mode == "react":
            planning_instruction = (
                "\n\n<planning_instruction>\n"
                "Use the ReAct (Reason + Act) framework:\n"
                "1. Thought: Analyze the current situation and decide the next action.\n"
                "2. Action: Execute a tool or generate a response.\n"
                "3. Observation: Review the result and decide if more steps are needed.\n"
                "Repeat until the task is complete.\n"
                "</planning_instruction>"
            )
        else:
            # 기본 CoT (moderate 복잡도)
            planning_instruction = (
                "\n\n<planning_instruction>\n"
                "Before answering, think step by step about what information you need "
                "and which tools to use. Create a brief plan, then execute it.\n"
                "</planning_instruction>"
            )

        if planning_instruction not in state.system_prompt:
            state.system_prompt += planning_instruction

        logger.info("[Plan] Planning instruction added (mode=%s)", mode)
        return {
            "planning_enabled": True,
            "planning_mode": mode,
            **(cap_result or {}),
        }

    # ---------- Strategy 카드 → planning_mode 매핑 (v0.29.3) ----------

    _STRATEGY_TO_MODE = {
        "cot_planner": "cot",
        "react": "react",
        "capability": "capability",
        "none": "none",
    }

    def _resolve_planning_mode(self, state: PipelineState) -> str:
        """active_strategies (UI 전략 카드) → planning_mode 매핑. 카드 미픽이면 필드 폴백.

        StrategyInfo("cot_planner", ..., is_default=True) 처럼 default 카드도 있어서
        active_strategies 가 ""이거나 dict 자체 미설정인 경우만 필드 폴백 — 카드가
        명시적으로 picked 되었으면 그 값을 신뢰.
        """
        active = ""
        if hasattr(state, "config") and state.config:
            picked = (state.config.active_strategies or {}).get(self.stage_id)
            if isinstance(picked, str):
                active = picked.strip()
        if active and active in self._STRATEGY_TO_MODE:
            return self._STRATEGY_TO_MODE[active]
        # 카드 미픽 또는 알 수 없는 값 → planning_mode 필드 폴백 (default=auto)
        return self.get_param("planning_mode", state, "auto")

    # ---------- Intent Routing (RR2) ----------

    async def _apply_intent_routing(self, state: PipelineState) -> None:
        """stage_params.s05_strategy.intent_rules 로 쿼리 의도 분류 → auto_metadata_filter.

        rules: list[dict] 구조
          [{"keywords": [...], "filter": {...}}, ...]
        첫 매칭 rule 의 filter 를 state.metadata["auto_metadata_filter"] 에 저장.
        s06 이 stage_params 의 metadata_filter 우선 + 없으면 이 값 fallback.
        """
        rules = self.get_param("intent_rules", state, None)
        # UI textarea 로 오면 JSON 문자열. 파싱.
        if isinstance(rules, str) and rules.strip():
            try:
                import json as _json
                rules = _json.loads(rules)
            except Exception as e:
                logger.debug("[Plan] intent_rules JSON 파싱 실패: %s", e)
                rules = None
        if not rules or not isinstance(rules, list):
            return
        user_input = (state.user_input or "").lower()
        if not user_input:
            return
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            keywords = rule.get("keywords") or []
            if not isinstance(keywords, list) or not keywords:
                continue
            if any(str(k).lower() in user_input for k in keywords):
                filt = rule.get("filter")
                if isinstance(filt, dict) and filt:
                    state.metadata["auto_metadata_filter"] = filt
                    logger.info(
                        "[Plan] intent_routing matched keywords=%s → auto_metadata_filter=%s",
                        keywords, filt,
                    )
                    return

    # ---------- Capability 모드 ----------

    async def _discover_and_bind_capabilities(self, state: PipelineState) -> dict:
        """
        자연어 intent(user_input)에서 capability 후보를 찾아 state에 바인딩.

        - 이미 config.capabilities에 선언된 것은 s04에서 처리됨 → 중복 회피
        - 여기서는 매칭된 것 중 아직 안 된 것만 materialize
        """
        if state.config is None:
            return {"capability_suggestions": 0, "capability_bound": 0}

        intent = state.user_input or ""
        if not intent.strip():
            return {"capability_suggestions": 0, "capability_bound": 0}

        already_bound = set(state.metadata.get("capability_bindings", {}).keys())
        already_declared = set(getattr(state.config, "capabilities", []) or [])
        skip = already_bound | already_declared

        top_k = int(self.get_param("capability_top_k", state, 3))
        min_score = float(self.get_param("capability_min_score", state, 0.4))

        registry = get_default_registry()
        matcher = CapabilityMatcher(registry, min_score=min_score)
        matches = matcher.match(intent, limit=top_k * 2, strategy=MatchStrategy.AUTO)

        suggested = [m for m in matches if m.spec.name not in skip][:top_k]
        if not suggested:
            logger.info("[Plan] capability discovery: no new matches (intent=%r)", intent[:80])
            return {"capability_suggestions": 0, "capability_bound": 0}

        names = [m.spec.name for m in suggested]
        state.metadata.setdefault("suggested_capabilities", []).extend(
            [{"name": m.spec.name, "score": m.score, "strategy": m.strategy} for m in suggested]
        )

        # factory가 있는 것만 materialize 시도
        report = materialize_capabilities(
            names,
            registry=registry,
            capability_params=getattr(state.config, "capability_params", None),
        )
        added = merge_into_state(report, state)

        logger.info(
            "[Plan] capability discovery: suggestions=%s, bound=%d, unknown=%d, no_factory=%d",
            names,
            added,
            len(report.unknown),
            len(report.no_factory),
        )

        # verbose: 자연어 발견으로 바인딩된 capability 각각 발행 (source=discovery)
        from ...events.types import CapabilityBindEvent
        for m in suggested:
            if m.spec.name in report.resolved:
                await state.emit_verbose(CapabilityBindEvent(
                    name=m.spec.name, source="discovery",
                    score=m.score, stage_id=self.stage_id,
                ))

        return {
            "capability_suggestions": len(names),
            "capability_bound": added,
            "capability_names": names,
        }

    def list_strategies(self) -> list[StrategyInfo]:
        return [
            StrategyInfo("cot_planner", "Chain-of-Thought 계획 수립", is_default=True),
            StrategyInfo("react", "ReAct 프레임워크 지시"),
            StrategyInfo("capability", "자연어 intent → capability 자동 발견"),
            StrategyInfo("none", "계획 단계 비활성화"),
        ]
