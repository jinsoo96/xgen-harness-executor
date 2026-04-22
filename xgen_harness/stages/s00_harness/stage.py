"""
S00 Harness — 메타 스테이지 (Planner 진입점)

REAL_HARNESS.md §4.3 동작 명세 구현체.

책임:
  1. 카탈로그 수집 (`core.catalog.get_catalog`) — 하드코딩 0
  2. HarnessPlanner 로 Plan 산출 — LLM 이 Stage/파라미터/Strategy 결정
  3. Plan 을 `state.metadata["harness_plan"]` 에 저장
  4. Plan.params / Plan.strategies 를 `state.config` 에 병합 (런타임 조립)
  5. PlanningEvent 방출 → 프론트 카드 렌더

비담당 (명시):
  - 실제 Stage 실행은 Pipeline 이 함 (Plan.chosen 을 보고 bypass 판단)
  - provider 초기화는 `core.provider_bootstrap` 에 위임 (s07 과 공용)

이 Stage 는 order=0 / phase=ingress 최상단. `HarnessConfig.use_planner=True`
일 때만 Pipeline 이 주입하므로 기본 동작엔 영향 없다 (하위 호환).
"""

from __future__ import annotations

import logging
from typing import Any

from ...core.stage import Stage, StrategyInfo
from ...core.state import PipelineState

logger = logging.getLogger("harness.stage.harness")


class HarnessStage(Stage):
    """메타 스테이지 — Planner 가 조립 결정을 내리는 단일 지점."""

    @property
    def stage_id(self) -> str:
        return "s00_harness"

    @property
    def order(self) -> int:
        # s01 보다 먼저. Stage.phase 기본 계산은 order<=4 면 ingress 라 문제 없음.
        return 0

    @property
    def phase(self) -> str:
        return "ingress"

    def list_strategies(self) -> list[StrategyInfo]:
        return [
            StrategyInfo(
                name="llm",
                description="LLM 이 카탈로그를 보고 Stage/파라미터/Strategy 를 조립",
                is_default=True,
            ),
            StrategyInfo(
                name="noop",
                description="Plan 생성 skip — 전체 Stage 실행 (Planner 비활성과 동일)",
                is_default=False,
            ),
        ]

    async def execute(self, state: PipelineState) -> dict:
        from ...core.planner import HarnessPlanner, HarnessPlan
        from ...events.types import PlanningEvent

        # ━━━━ 0. noop 전략: 전체 실행, Plan 없음 ━━━━
        strategy_name = self.get_param("strategy", state, "llm")
        if strategy_name == "noop":
            plan = HarnessPlan.fallback_all("Planner strategy=noop")
            state.metadata["harness_plan"] = plan.to_dict()
            return {
                "planner_source": plan.source,
                "chosen_count": 0,
                "skipped_count": 0,
            }

        # ━━━━ 1. Plan 생성 ━━━━
        planner = HarnessPlanner()
        workflow_hints = state.metadata.get("workflow_hints") or {}

        plan = await planner.plan(
            state=state,
            user_input=state.user_input,
            workflow_hints=workflow_hints,
        )

        # ━━━━ 2. state 에 Plan 저장 (Pipeline 이 읽음) ━━━━
        state.metadata["harness_plan"] = plan.to_dict()

        # ━━━━ 3. Plan.params / Plan.strategies 를 config 에 병합 ━━━━
        self._merge_plan_into_config(state, plan)

        # ━━━━ 4. PlanningEvent 방출 — 프론트 카드 ━━━━
        if state.event_emitter:
            await state.event_emitter.emit(PlanningEvent(
                chosen=list(plan.chosen),
                skipped=dict(plan.skipped),
                params=dict(plan.params),
                strategies=dict(plan.strategies),
                reasoning=plan.reasoning,
                planner_model=plan.planner_model,
                source=plan.source,
            ))

        logger.info(
            "[Harness] Plan 확정 source=%s chosen=%d skipped=%d params=%d strategies=%d",
            plan.source, len(plan.chosen), len(plan.skipped),
            len(plan.params), len(plan.strategies),
        )

        return {
            "planner_source": plan.source,
            "chosen": list(plan.chosen),
            "skipped": dict(plan.skipped),
            "reasoning": plan.reasoning,
            "planner_model": plan.planner_model,
        }

    # ── helpers ─────────────────────────────────────────────────────

    def _merge_plan_into_config(self, state: PipelineState, plan: "HarnessPlan") -> None:  # type: ignore  # noqa: F821
        """Plan 의 params/strategies 를 `state.config` 에 덮어씌운다.

        이미 사용자가 UI 에서 지정한 값보다 Plan 이 우선. 하지만 Plan 이 언급하지
        않은 파라미터는 기존 값 유지 (부분 override). 이게 "환경만 주어주고 알아서
        조립" 의 실전 구현 — Planner 는 필요한 부분만 조정하고 나머지는 default 존중.
        """
        config = state.config
        if not config:
            return

        # params 병합 — 기존 stage_params[sid] 에 Plan.params[sid] 를 shallow update
        for sid, overrides in (plan.params or {}).items():
            if not isinstance(overrides, dict):
                continue
            existing = dict(config.stage_params.get(sid) or {})
            existing.update(overrides)
            config.stage_params[sid] = existing

        # strategies 병합 — active_strategies[sid] = plan 선택값
        for sid, strategy_name in (plan.strategies or {}).items():
            if isinstance(strategy_name, str) and strategy_name:
                config.active_strategies[sid] = strategy_name
