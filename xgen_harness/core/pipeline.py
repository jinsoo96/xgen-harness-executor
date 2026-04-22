"""
Pipeline — 3-Phase 실행 엔진

Phase A (Ingress, 1회): Input → Memory → System Prompt → Tool Index
Phase B (Agentic Loop, N회): Plan → Context → LLM ↔ Execute → Validate → Decide
Phase C (Egress, 1회): Save → Complete

도구 루프(LLM↔Execute)는 Phase B 내부에서 자체 반복.
검증 재시도(Validate→Decide→retry)는 Phase B 전체를 재시작.
"""

import asyncio
import logging
import time
from typing import Optional

from .config import HarnessConfig
from .stage import Stage
from .state import PipelineState
from ..events.emitter import EventEmitter
from ..events.types import (
    StageEnterEvent,
    StageExitEvent,
    ErrorEvent,
    DoneEvent,
    MetricsEvent,
)
from ..errors import HarnessError, PipelineAbortError

logger = logging.getLogger("harness.pipeline")


class Pipeline:
    """하네스 파이프라인 실행기"""

    def __init__(
        self,
        config: HarnessConfig,
        stages: list[Stage],
        event_emitter: Optional[EventEmitter] = None,
    ):
        self.config = config
        self.event_emitter = event_emitter or EventEmitter()
        self._all_stages = stages

        # Phase별 분류
        self.ingress_stages = [s for s in stages if s.phase == "ingress"]
        self.loop_stages = [s for s in stages if s.phase == "loop"]
        self.egress_stages = [s for s in stages if s.phase == "egress"]

        self._total_stage_count = len(stages)

    @classmethod
    def from_config(
        cls,
        config: HarnessConfig,
        event_emitter: Optional[EventEmitter] = None,
        registry: Optional["ArtifactRegistry"] = None,
    ) -> "Pipeline":
        """설정으로부터 파이프라인 생성.

        registry 미지정 시 전역 싱글톤(`_get_default_registry()`)을 사용하여
        `register_stage()` 나 entry_points 로 등록된 외부 플러그인 Stage 도
        함께 반영됩니다. 테스트/격리가 필요하면 registry 를 명시 전달하세요.

        v0.12.0 — `config.use_planner=True` 면 s00_harness (Planner 메타 스테이지)
        를 ingress 최상단에 prepend. Planner 가 없을 때 기본 파이프라인 그대로.
        """
        from .registry import _get_default_registry
        reg = registry or _get_default_registry()
        stages = reg.build_pipeline_stages(config)

        if config.use_planner:
            try:
                s00_cls = reg.get("s00_harness", "default")
                s00 = s00_cls()
                # 중복 방지 + 최상단 보장
                stages = [s for s in stages if s.stage_id != "s00_harness"]
                stages.insert(0, s00)
                logger.info("[Pipeline] Harness Planner 활성 — s00_harness 주입")
            except KeyError:
                logger.warning("[Pipeline] use_planner=True 이나 s00_harness 미등록 — 기본 파이프라인으로 진행")

        return cls(config, stages, event_emitter)

    async def run(self, state: PipelineState) -> PipelineState:
        """파이프라인 실행 — 3 Phase"""
        state.event_emitter = self.event_emitter
        state.config = self.config
        state.start_time = time.time()

        try:
            # Phase A: Ingress (1회)
            logger.info("[Pipeline] Phase A: Ingress (%d stages)", len(self.ingress_stages))
            for stage in self.ingress_stages:
                if self._planner_skips(stage, state):
                    await self._emit_bypass(stage, state, reason=self._planner_skip_reason(stage, state))
                    continue
                if stage.should_bypass(state):
                    await self._emit_bypass(stage, state)
                    continue
                await self._execute_stage(stage, state)

            # Phase B: Agentic Loop
            logger.info("[Pipeline] Phase B: Agentic Loop (max %d iterations)", self.config.max_iterations)
            while state.loop_decision == "continue" and not state.is_over_iterations and not state.is_over_budget:
                state.loop_iteration += 1
                logger.info("[Pipeline] Loop iteration %d", state.loop_iteration)

                for stage in self.loop_stages:
                    if self._planner_skips(stage, state):
                        await self._emit_bypass(stage, state, reason=self._planner_skip_reason(stage, state))
                        continue
                    if stage.should_bypass(state):
                        await self._emit_bypass(stage, state)
                        continue
                    await self._execute_stage(stage, state)

                    # Decide 스테이지가 loop_decision을 설정
                    if state.loop_decision in ("complete", "abort"):
                        break

                # retry → loop_decision을 continue로 돌리고 재시작
                if state.loop_decision == "retry":
                    state.retry_count += 1
                    if state.retry_count >= self.config.max_retries:
                        logger.warning("[Pipeline] Max retries reached (%d)", self.config.max_retries)
                        state.loop_decision = "complete"
                    else:
                        logger.info("[Pipeline] Retry %d/%d", state.retry_count, self.config.max_retries)
                        state.loop_decision = "continue"
                        # verbose: 에이전틱 루프 재시도 이벤트
                        from ..events.types import RetryEvent
                        await state.emit_verbose(RetryEvent(
                            stage_id="pipeline_loop",
                            reason="loop retry by decide",
                            attempt=state.retry_count,
                            max_attempts=self.config.max_retries,
                        ))

            # Phase C: Egress (1회)
            logger.info("[Pipeline] Phase C: Egress (%d stages)", len(self.egress_stages))
            for stage in self.egress_stages:
                if self._planner_skips(stage, state):
                    await self._emit_bypass(stage, state, reason=self._planner_skip_reason(stage, state))
                    continue
                if stage.should_bypass(state):
                    await self._emit_bypass(stage, state)
                    continue
                await self._execute_stage(stage, state)

            # 완료 이벤트
            await self.event_emitter.emit(DoneEvent(
                final_output=state.final_output,
                success=True,
            ))

        except PipelineAbortError as e:
            logger.error("[Pipeline] Abort: %s", e)
            await self.event_emitter.emit(ErrorEvent(
                message=str(e),
                stage_id=e.stage_id,
                recoverable=False,
            ))
            await self.event_emitter.emit(DoneEvent(
                final_output=state.final_output or str(e),
                success=False,
            ))

        except Exception as e:
            logger.exception("[Pipeline] Unexpected error")
            await self.event_emitter.emit(ErrorEvent(
                message=str(e),
                stage_id="",
                recoverable=False,
            ))
            await self.event_emitter.emit(DoneEvent(
                final_output=state.final_output or str(e),
                success=False,
            ))

        return state

    async def _execute_stage(self, stage: Stage, state: PipelineState) -> dict:
        """단일 스테이지 실행 (라이프사이클 훅 + 이벤트 발행 + I/O 검증)"""
        step = self._get_step_number(stage)

        # I/O 입력 검증 (Stage 인터페이스 계약)
        if stage.input_spec:
            missing = stage.input_spec.validate(state)
            if missing:
                logger.warning("[Pipeline] Stage %s missing inputs: %s (continuing anyway)", stage.stage_id, missing)

        # on_enter + 이벤트
        await self.event_emitter.emit(StageEnterEvent(
            stage_id=stage.stage_id,
            stage_name=stage.display_name_ko,
            phase=stage.phase,
            step=step,
            total=self._total_stage_count,
        ))
        await stage.on_enter(state)

        t0 = time.time()
        try:
            result = await stage.execute(state)
            elapsed = time.time() - t0
            state.stage_timings[stage.stage_id] = elapsed * 1000

            # on_exit + 이벤트
            await stage.on_exit(result, state)
            await self.event_emitter.emit(StageExitEvent(
                stage_id=stage.stage_id,
                stage_name=stage.display_name_ko,
                output=result,
                score=state.validation_score if stage.stage_id == "s09_judge" else None,
                step=step,
                total=self._total_stage_count,
            ))
            return result

        except HarnessError as e:
            elapsed = time.time() - t0
            state.stage_timings[stage.stage_id] = elapsed * 1000

            recovery = await stage.on_error(e, state)
            if recovery is not None:
                logger.info("[Pipeline] Stage %s recovered from error", stage.stage_id)
                # verbose: Stage on_error 복구 시 RetryEvent
                from ..events.types import RetryEvent
                await state.emit_verbose(RetryEvent(
                    stage_id=stage.stage_id,
                    reason=f"on_error recovered: {type(e).__name__}",
                    attempt=1,
                    max_attempts=1,
                ))
                await self.event_emitter.emit(StageExitEvent(
                    stage_id=stage.stage_id,
                    stage_name=stage.display_name_ko,
                    output=recovery,
                    step=step,
                    total=self._total_stage_count,
                ))
                return recovery

            await self.event_emitter.emit(ErrorEvent(
                message=str(e),
                stage_id=stage.stage_id,
                recoverable=e.recoverable,
            ))
            raise

        except Exception as e:
            # 커스텀 Stage / 외부 플러그인이 raise 한 일반 예외도 on_error 복구 기회 제공
            try:
                recovery = await stage.on_error(e, state)
            except Exception as on_err_exc:
                logger.debug("[Pipeline] stage.on_error(%s) itself raised: %s", stage.stage_id, on_err_exc)
                recovery = None
            if recovery is not None:
                logger.info("[Pipeline] Stage %s recovered from generic error", stage.stage_id)
                await self.event_emitter.emit(StageExitEvent(
                    stage_id=stage.stage_id,
                    stage_name=stage.display_name_ko,
                    output=recovery,
                    step=step,
                    total=self._total_stage_count,
                ))
                return recovery

            await self.event_emitter.emit(ErrorEvent(
                message=str(e),
                stage_id=stage.stage_id,
                recoverable=False,
            ))
            raise PipelineAbortError(str(e), stage.stage_id)

    async def _emit_bypass(
        self,
        stage: Stage,
        state: PipelineState,
        reason: str = "조건 미충족으로 건너뜀",
    ) -> None:
        """bypass된 스테이지도 이벤트 발행 (UI에서 스킵 상태 표시).

        Planner 가 skip 한 경우 reason 에 Plan.skipped[stage_id] 가 주입되어
        "왜 이 단계를 건너뛰었는지"를 프론트가 그대로 표시할 수 있다.
        """
        step = self._get_step_number(stage)
        logger.debug("[Pipeline] Bypass: %s (%s)", stage.stage_id, reason)
        await self.event_emitter.emit(StageEnterEvent(
            stage_id=stage.stage_id,
            stage_name=stage.display_name_ko,
            phase=stage.phase,
            step=step,
            total=self._total_stage_count,
            description="bypassed",
        ))
        await self.event_emitter.emit(StageExitEvent(
            stage_id=stage.stage_id,
            stage_name=stage.display_name_ko,
            output={"bypassed": True, "reason": reason},
            step=step,
            total=self._total_stage_count,
        ))

    # ── Harness Planner 연동 (v0.12.0) ─────────────────────────────

    def _planner_skips(self, stage: Stage, state: PipelineState) -> bool:
        """Planner 가 세운 Plan 에 따라 이 Stage 를 skip 해야 하는지.

        Plan 미수립(state.metadata["harness_plan"] 없음) 또는 chosen 이 비어있는
        fallback 상태에서는 skip 하지 않음 (전체 실행 — 하위 호환).
        s00_harness 자체는 Plan 생성 주체이므로 절대 skip 되지 않는다.
        """
        if stage.stage_id == "s00_harness":
            return False
        plan = state.metadata.get("harness_plan")
        if not isinstance(plan, dict):
            return False
        chosen = plan.get("chosen") or []
        if not chosen:
            return False  # fallback — 전체 실행
        return stage.stage_id not in chosen

    def _planner_skip_reason(self, stage: Stage, state: PipelineState) -> str:
        """Plan.skipped[stage_id] 가 있으면 그 이유, 없으면 일반 메시지."""
        plan = state.metadata.get("harness_plan") or {}
        skipped = plan.get("skipped") or {}
        reason = skipped.get(stage.stage_id)
        if reason:
            return f"Planner: {reason}"
        return "Planner 가 이번 턴에는 불필요하다고 판단"

    def _get_step_number(self, stage: Stage) -> int:
        for i, s in enumerate(self._all_stages, 1):
            if s.stage_id == stage.stage_id:
                return i
        return 0

    def describe(self) -> list[dict]:
        """파이프라인 스테이지 설명 목록 (API/UI용)"""
        return [
            {
                **stage.describe().__dict__,
                "strategies": [s.__dict__ for s in stage.list_strategies()],
            }
            for stage in self._all_stages
        ]
