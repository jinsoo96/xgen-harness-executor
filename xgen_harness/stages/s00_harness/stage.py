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
    def role(self) -> str:
        # v0.16.6 — Pipeline 이 이 Stage 를 ingress 최상단 prepend + bypass 금지 +
        # Phase B replan 대상으로 특수 처리. Pipeline 은 이름 "s00_harness" 대신
        # 이 role 을 본다.
        return "orchestrator_planner"

    @property
    def order(self) -> int:
        # s01 보다 먼저. Stage.phase 기본 계산은 order<=4 면 ingress 라 문제 없음.
        return 0

    @property
    def phase(self) -> str:
        return "ingress"

    def list_strategies(self) -> list[StrategyInfo]:
        # v0.14.0: Transport Strategy 를 자기서술 — StrategyResolver 에 등록된 모든
        # impl 을 동적으로 노출. 리터럴 목록 하드코딩 금지 (재귀적 자율주행).
        from ...core.strategy_resolver import _REGISTRY, _ensure_defaults_registered
        _ensure_defaults_registered()
        entries: list[StrategyInfo] = []
        for (sid, slot, impl), cls in sorted(_REGISTRY.items()):
            if sid != "s00_harness" or slot != "transport":
                continue
            try:
                inst = cls()
                entries.append(StrategyInfo(
                    name=impl,
                    description=getattr(inst, "description", "") or "",
                    is_default=(impl == "streaming"),
                ))
            except Exception:
                entries.append(StrategyInfo(name=impl, description="", is_default=(impl == "streaming")))
        if not entries:
            # 레지스트리 비어있을 때의 최후 폴백 — 기본 두 개 이름만.
            entries = [
                StrategyInfo(name="streaming", description="SSE 스트리밍", is_default=True),
                StrategyInfo(name="batch", description="비스트리밍 단일 호출"),
            ]
        return entries

    async def execute(self, state: PipelineState) -> dict:
        from ...core.planner import HarnessPlanner, HarnessPlan
        from ...events.types import PlanningEvent

        # ━━━━ 0. harness_mode=off 또는 레거시 noop: 전체 실행, Plan 없음 ━━━━
        # mode 는 HarnessConfig.harness_mode 를 우선 참조, 없으면 stage_params 폴백.
        harness_mode = getattr(state.config, "harness_mode", "") if state.config else ""
        if not harness_mode:
            harness_mode = self.get_param("strategy", state, "autonomous")
        # legacy: strategy="noop" 를 off 로 간주
        # v0.29.1 — fallback_all 분리. off 는 의도된 정상 경로 (사용자가 자율조립 끔)
        # → source="off" 로 emit 해서 port 의 fallback_all 경고 (planner 실패 안내) 가
        # off 케이스에선 안 뜨게 함. 빈 chosen 은 그대로 — Pipeline 이 모든 stage 실행
        # 하면서 사용자 s04 selected_tools 를 그대로 사용.
        if harness_mode in ("off", "noop"):
            plan = HarnessPlan.off_mode(f"harness_mode={harness_mode}")
            state.metadata["harness_plan"] = plan.to_dict()
            return {
                "planner_source": plan.source,
                "chosen_count": 0,
                "skipped_count": 0,
            }

        # selected: Planner LLM 호출 생략, 사용자 핀(chosen/strategies/params) 그대로 사용
        if harness_mode == "selected":
            pinned_chosen = list((state.config.stage_params.get("s00_harness") or {}).get("pinned_chosen") or [])
            pinned_strategies = dict((state.config.stage_params.get("s00_harness") or {}).get("pinned_strategies") or {})
            pinned_params = dict((state.config.stage_params.get("s00_harness") or {}).get("pinned_params") or {})
            plan = HarnessPlan(
                chosen=pinned_chosen,
                skipped={},
                params=pinned_params,
                strategies=pinned_strategies,
                reasoning="harness_mode=selected — 사용자 핀 그대로 적용",
                source="user_pinned",
                done=False,
            )
            state.metadata["harness_plan"] = plan.to_dict()
            self._merge_plan_into_config(state, plan)
            if state.event_emitter:
                await state.event_emitter.emit(PlanningEvent(
                    chosen=list(plan.chosen), skipped={}, params=dict(plan.params),
                    strategies=dict(plan.strategies), reasoning=plan.reasoning,
                    planner_model="", source=plan.source,
                    iteration=getattr(state, "loop_iteration", 0), done=False,
                ))
            return {
                "planner_source": plan.source,
                "chosen": list(plan.chosen),
                "skipped": {},
                "reasoning": plan.reasoning,
                "iteration": getattr(state, "loop_iteration", 0),
            }

        # ━━━━ 1. Plan 생성 (autonomous) ━━━━
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
                iteration=getattr(state, "loop_iteration", 0),
                done=plan.done,
                max_iterations=plan.max_iterations or 0,
                orchestrator_hint=plan.orchestrator_hint or "",
            ))

        logger.info(
            "[Harness] Plan%s 확정 source=%s chosen=%d skipped=%d params=%d strategies=%d done=%s",
            f"#{state.loop_iteration}" if state.loop_iteration > 0 else "",
            plan.source, len(plan.chosen), len(plan.skipped),
            len(plan.params), len(plan.strategies), plan.done,
        )

        return {
            "planner_source": plan.source,
            "chosen": list(plan.chosen),
            "skipped": dict(plan.skipped),
            "reasoning": plan.reasoning,
            "planner_model": plan.planner_model,
            "done": plan.done,
            "iteration": getattr(state, "loop_iteration", 0),
        }

    async def main_call(self, state: PipelineState, *, strategy: str = "streaming") -> dict:
        """v0.14.0 — 본문 LLM 호출 진입점. Pipeline Phase B 루프에서 호출.

        과거 s07_llm.execute 가 하던 역할. Transport Strategy (streaming/batch/…)
        를 StrategyResolver 에서 이름으로 해석해 위임. 리터럴 분기 없음.
        """
        from ...stages.interfaces import TransportStrategy
        from ...core.strategy_resolver import StrategyResolver

        resolver = StrategyResolver.default()
        transport = resolver.resolve("s00_harness", "transport", strategy)
        if transport is None or not isinstance(transport, TransportStrategy):
            # 레지스트리에 없는 이름 — 기본값 streaming 으로 재시도
            transport = resolver.resolve("s00_harness", "transport", "streaming")
        if transport is None:
            raise RuntimeError(
                f"Transport strategy '{strategy}' not registered for s00_harness"
            )
        return await transport.call(state)

    # ── helpers ─────────────────────────────────────────────────────

    def _merge_plan_into_config(self, state: PipelineState, plan: "HarnessPlan") -> None:  # type: ignore  # noqa: F821
        """Plan 의 params/strategies/max_iterations 를 `state.config` 에 덮어씌운다.

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

        # v0.15.0 재귀적 자율주행 — LLM 이 이번 요청 적정 반복 수 판단.
        # Plan 이 명시하지 않으면 (None) config 기본값(10) 유지.
        if isinstance(getattr(plan, "max_iterations", None), int) and plan.max_iterations > 0:
            logger.info(
                "[Harness] Plan.max_iterations=%d 적용 (기존 %d → override)",
                plan.max_iterations, config.max_iterations,
            )
            config.max_iterations = plan.max_iterations

        # orchestrator_hint 는 엔진에서는 메타데이터로만 기록.
        # 이식측 dispatcher / 프론트 PlanningCard 가 해석.
        hint = getattr(plan, "orchestrator_hint", "") or ""
        if hint:
            state.metadata["orchestrator_hint"] = hint
