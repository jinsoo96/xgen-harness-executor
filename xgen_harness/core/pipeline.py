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

        # v0.14.0/v0.16.6 — "orchestrator_planner" role Stage (예: s00_harness) 를
        # ingress 최상단에 prepend. 이름 리터럴 대신 **role 검색** 으로 전환.
        # 외부 기여자가 자기 Planner Stage 를 `role == "orchestrator_planner"` 로
        # 선언하면 Pipeline 코드 변경 0 으로 대체 가능.
        mode = getattr(config, "harness_mode", "") or ("autonomous" if config.use_planner else "off")
        if mode != "off":
            planner_stage = _find_role_in_registry(reg, config, "orchestrator_planner")
            if planner_stage is not None:
                # 기존 stages 에서 같은 stage_id 제거 후 최상단 prepend.
                stages = [s for s in stages if s.stage_id != planner_stage.stage_id]
                stages.insert(0, planner_stage)
                logger.info("[Pipeline] Planner 주입 (mode=%s, stage_id=%s)", mode, planner_stage.stage_id)
            else:
                logger.warning("[Pipeline] orchestrator_planner role Stage 미등록 — 기본 파이프라인으로 진행")

        return cls(config, stages, event_emitter)

    async def run(self, state: PipelineState) -> PipelineState:
        """파이프라인 실행 — 3 Phase (v0.13.0 단일 provider + iterative planning)."""
        state.event_emitter = self.event_emitter
        state.config = self.config
        state.start_time = time.time()

        try:
            # ━━━━ v0.14.0 — LLM provider 1 회 선초기화 ━━━━
            # "한 번 설정하고 그 핸들을 쭉 재사용" — state.provider 를 Pipeline 진입부에서
            # 미리 띄워 s00_harness (Planner + main_call) / s08_judge 가 전부 **같은
            # 인스턴스** 를 재활용. ensure_provider 는 idempotent.
            from .provider_bootstrap import ensure_provider
            try:
                await ensure_provider(state, stage_id="pipeline")
            except Exception as e:
                # provider 초기화 실패해도 s01_input 등 provider 불필요 단계는 돌아야 하므로
                # 여기서 abort 하지 않는다. 실제로 provider 가 필요한 Stage 에서 재시도.
                logger.debug("[Pipeline] provider 선초기화 보류: %s", e)

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

            # Phase B: Agentic Loop — v0.13.0 iterative planning / v0.15.3 orchestrator_hint 분기
            # 매 iter 시작에 s00_harness 를 다시 호출해 **Plan 을 갱신**. 이전 iter 의
            # 결과(tool_results / validation_score / rag_context / messages)를 s00 이
            # 카탈로그 + 누적 state 로 다시 보고 "이제 뭐 해야 할지" 재결정. 첫 iter 는
            # Phase A 에서 이미 s00 실행했으므로 skip (loop_iteration == 0 → 1 전환 직후).
            #
            # v0.15.3 — orchestrator_hint (Plan 이 제시) 가 loop 행동을 조정:
            #   linear       : 1회만 돌고 즉시 종료 (단발 Q&A)
            #   iterative    : 기본. 매 iter replan
            #   plan_execute : 첫 Plan 고수. replan 생략, 반복은 함
            #   react / dag  : 엔진 no-op (이식측 dispatcher 위임)
            s00_stage = self._find_loop_s00()
            # v0.17.0 — Policy Gate (role="policy_gate") 인스턴스. 없으면 훅 no-op.
            policy_stage = self._find_role_stage("policy_gate")
            orch_hint = (state.metadata.get("orchestrator_hint") or "").strip().lower()
            logger.info(
                "[Pipeline] Phase B: Agentic Loop (max %d iterations, orchestrator_hint=%r)",
                self.config.max_iterations, orch_hint or "iterative(default)",
            )
            while state.loop_decision == "continue" and not state.is_over_iterations and not state.is_over_budget:
                state.loop_iteration += 1
                logger.info("[Pipeline] Loop iteration %d", state.loop_iteration)

                # iterative replan — 2번째 iter 부터. linear / plan_execute 는 replan 생략.
                # Plan 이 done 플래그를 세우면 즉시 종료 (모든 hint 공통).
                if (
                    state.loop_iteration > 1
                    and s00_stage is not None
                    and orch_hint not in ("linear", "plan_execute")
                ):
                    await self._execute_stage(s00_stage, state)
                    plan = state.metadata.get("harness_plan") or {}
                    if plan.get("done"):
                        logger.info("[Pipeline] Planner 가 done 선언 — Phase B 종료")
                        state.loop_decision = "complete"
                        break

                for stage in self.loop_stages:
                    # v0.17.0 — Policy Gate 는 loop 순서에서 skip. Pipeline 이 3 훅에 별도 호출.
                    if stage.role == "policy_gate":
                        await self._emit_bypass(stage, state, reason="Policy Gate 는 훅 경로로 호출")
                        continue

                    # v0.16.6 — "main_actor" role Stage 직전에 Planner 의 main_call 주입.
                    # 이름 리터럴("s07_act") 대신 Stage.role 로 검색 → 외부 Stage 가
                    # 같은 role 로 바꿔 끼워도 자동 인식.
                    if stage.role == "main_actor":
                        # v0.17.0 — pre_main 훅 (입력/Plan 정책)
                        await self._invoke_policy_gate(state, policy_stage, "pre_main")
                        if state.policy_block_reason:
                            state.loop_decision = "complete"
                            break

                        if s00_stage is not None:
                            await self._invoke_main_call(state, s00_stage)

                        # v0.17.0 — main_call 직후 pre_tool / post_response 훅
                        hook = "pre_tool" if state.pending_tool_calls else "post_response"
                        await self._invoke_policy_gate(state, policy_stage, hook)
                        if state.policy_block_reason:
                            state.loop_decision = "complete"
                            break

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

                # v0.17.0 — iter 말미 loop_boundary 훅 (예산·반복 등 누적 정책)
                await self._invoke_policy_gate(state, policy_stage, "loop_boundary")

                # v0.15.3 — linear hint: 1회만 돌고 강제 종료. replan/iteration 전부 skip.
                if orch_hint == "linear" and state.loop_decision == "continue":
                    logger.info("[Pipeline] orchestrator_hint=linear — 1회 실행 후 종료")
                    state.loop_decision = "complete"
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
                # v0.16.6 — "scorer" role Stage 의 StageExit 에만 validation_score 노출.
                score=state.validation_score if stage.role == "scorer" else None,
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
        v0.16.6 — Planner 자신(role="orchestrator_planner") 은 skip 대상 외.
        """
        if stage.role == "orchestrator_planner":
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

    async def _invoke_main_call(self, state: PipelineState, planner_stage: Stage) -> None:
        """v0.14.0/v0.16.6 — Planner(role="orchestrator_planner") 의 main_call 호출.

        StageEnter/Exit 이벤트는 planner_stage.stage_id 를 그대로 사용.
        transport 선택은 `state.config.active_strategies[<stage_id>]` — 이름 리터럴 없음.
        """
        if not hasattr(planner_stage, "main_call"):
            logger.error("[Pipeline] planner_stage has no main_call — upgrade required")
            return

        sid = planner_stage.stage_id
        step = self._get_step_number(planner_stage)
        transport = "streaming"
        if state.config:
            active = state.config.active_strategies.get(sid)
            if isinstance(active, str) and active:
                transport = active
            params = state.config.stage_params.get(sid) or {}
            strat = params.get("strategy")
            if isinstance(strat, str) and strat:
                transport = strat

        await self.event_emitter.emit(StageEnterEvent(
            stage_id=sid,
            stage_name=planner_stage.display_name_ko,
            phase="loop",
            step=step,
            total=self._total_stage_count,
            description=f"main_call ({transport})",
        ))

        t0 = time.time()
        try:
            result = await planner_stage.main_call(state, strategy=transport)
            elapsed = time.time() - t0
            state.stage_timings[f"{sid}_main_call"] = elapsed * 1000
            await self.event_emitter.emit(StageExitEvent(
                stage_id=sid,
                stage_name=planner_stage.display_name_ko,
                output=result,
                step=step,
                total=self._total_stage_count,
            ))
        except Exception as e:
            await self.event_emitter.emit(ErrorEvent(
                message=str(e), stage_id=sid, recoverable=False,
            ))
            raise

    def _find_loop_s00(self) -> Optional[Stage]:
        """v0.16.6 — Planner(role="orchestrator_planner") 인스턴스 조회.

        Phase B iter 의 replan 은 같은 인스턴스를 재호출해야 Plan 이 누적·갱신.
        Planner 비활성이면 None 반환. 이름 리터럴 없이 role 로만 검색.
        """
        return self._find_role_stage("orchestrator_planner")

    def _find_role_stage(self, role: str) -> Optional[Stage]:
        """v0.17.0 — role 이름으로 Stage 인스턴스 조회 (범용)."""
        for stage in self._all_stages:
            if stage.role == role:
                return stage
        return None

    async def _invoke_policy_gate(
        self,
        state: "PipelineState",
        policy_stage: Optional[Stage],
        hook_name: str,
    ) -> None:
        """v0.17.0 — Policy Gate Stage 의 invoke_hook 호출.

        Stage 가 없거나 예외가 나도 Pipeline 실행은 계속 (정책 검사 실패가 본 흐름을 막지 않음).
        """
        if policy_stage is None or not hasattr(policy_stage, "invoke_hook"):
            return
        try:
            await policy_stage.invoke_hook(state, hook_name)
        except Exception as e:
            logger.warning("[Pipeline] Policy gate %s 호출 실패: %s", hook_name, e)

    def _get_step_number(self, stage: Stage) -> int:
        for i, s in enumerate(self._all_stages, 1):
            if s.stage_id == stage.stage_id:
                return i
        return 0


def _find_role_in_registry(reg, config, role: str) -> Optional[Stage]:
    """레지스트리에서 role 일치 Stage 를 찾아 인스턴스 반환.

    v0.16.6 — Pipeline 이 Stage 이름 리터럴 없이 role 기반으로 특수 분기 찾도록.
    외부 플러그인 Stage 가 같은 role 로 선언하면 자동으로 잡힌다.
    """
    try:
        for sid in reg._registry.keys():  # type: ignore[attr-defined]
            try:
                cls = reg.get(sid, "default")
                inst = cls()
                if inst.role == role:
                    return inst
            except Exception:
                continue
    except Exception:
        pass
    return None

    def describe(self) -> list[dict]:
        """파이프라인 스테이지 설명 목록 (API/UI용)"""
        return [
            {
                **stage.describe().__dict__,
                "strategies": [s.__dict__ for s in stage.list_strategies()],
            }
            for stage in self._all_stages
        ]
