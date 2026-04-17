"""
통합 수준 예외 경로 검증 — 실제 pipeline.run() 중 발동 확인

- Guard 실전 트리거: 예산 초과 시 loop 중단
- Loop back-jump: validation 실패 시 retry 경로
- Strategy 예외 전파: Stage.on_error 호출
- 동시성 스트레스: N 개 파이프라인 병렬 + 오염 없음
"""

import asyncio
from xgen_harness import (
    HarnessConfig, PipelineState, Pipeline, EventEmitter,
    Stage, register_stage,
)
from xgen_harness.core.execution_context import set_execution_context, get_api_key


# ──────────────── 공용 fake Stage ────────────────

class CountingLLMStage(Stage):
    """s07_llm 대체 — iter 증가 & cost 축적. 실제 LLM 호출 안 함."""
    @property
    def stage_id(self): return "s07_llm"
    @property
    def order(self): return 7
    async def execute(self, state):
        state.cost_usd += 1.0
        state.llm_call_count += 1
        state.last_assistant_text = f"답변 iter={state.loop_iteration}"
        state.metadata.setdefault("llm_calls", 0)
        state.metadata["llm_calls"] += 1
        return {}


# ──────────────── 1. Guard 실전 트리거 ────────────────

async def test_cost_guard_blocks_loop():
    """cost_budget=0.5 인데 각 iter 가 1.0씩 증가 → 첫 iter 후 완료 처리"""
    register_stage("s07_llm", "counting_llm", CountingLLMStage)
    set_execution_context(api_key="d", provider="openai", model="gpt-4o-mini")

    config = HarnessConfig(
        provider="openai", model="gpt-4o-mini",
        artifacts={"s07_llm": "counting_llm"},
        cost_budget_usd=0.5,
        max_iterations=10,
        disabled_stages={"s05_plan", "s06_context", "s08_execute",
                         "s09_validate", "s11_save"},
    )
    emitter = EventEmitter()
    pipeline = Pipeline.from_config(config, emitter)
    state = PipelineState(config=config, user_input="t")
    state.tool_definitions = [{"name":"x","description":"y","input_schema":{"type":"object"}}]
    try:
        await pipeline.run(state)
    except Exception:
        pass

    # cost 가 1회 이상 쌓였고 loop 는 10회 전에 멈춰야 함
    assert state.cost_usd >= 0.5
    assert state.loop_iteration < 10, f"예산 초과 후에도 계속 돔: {state.loop_iteration}"
    print(f"  ✅ cost_guard_blocks_loop — iter={state.loop_iteration}, cost=${state.cost_usd:.2f}, decision={state.loop_decision}")


async def test_iteration_guard_blocks_loop():
    """max_iterations=3 에 도달하면 중단"""
    register_stage("s07_llm", "counting_llm", CountingLLMStage)
    set_execution_context(api_key="d", provider="openai", model="gpt-4o-mini")

    config = HarnessConfig(
        provider="openai", model="gpt-4o-mini",
        artifacts={"s07_llm": "counting_llm"},
        cost_budget_usd=10000,  # cost 는 넉넉
        max_iterations=3,
        disabled_stages={"s05_plan", "s06_context", "s08_execute",
                         "s09_validate", "s11_save"},
    )
    emitter = EventEmitter()
    pipeline = Pipeline.from_config(config, emitter)
    state = PipelineState(config=config, user_input="t")
    state.tool_definitions = [{"name":"x","description":"y","input_schema":{"type":"object"}}]
    try:
        await pipeline.run(state)
    except Exception:
        pass

    assert state.loop_iteration <= 4, f"max 3 인데 {state.loop_iteration}회 반복"
    print(f"  ✅ iteration_guard_blocks_loop — iter={state.loop_iteration}, decision={state.loop_decision}")


# ──────────────── 2. Loop back-jump 통합 ────────────────

class AlwaysLowScoreValidate(Stage):
    """s09_validate 대체 — 항상 낮은 점수. retry 유발."""
    @property
    def stage_id(self): return "s09_validate"
    @property
    def order(self): return 9
    async def execute(self, state):
        state.validation_score = 0.2
        state.validation_feedback = "not good"
        state.metadata.setdefault("validate_calls", 0)
        state.metadata["validate_calls"] += 1
        return {}


async def test_loop_back_jump_retry():
    """낮은 점수 반환 → s10_decide=retry → pipeline 이 다음 iter 로 넘어가 s07 재호출"""
    register_stage("s07_llm", "counting_llm", CountingLLMStage)
    register_stage("s09_validate", "always_low", AlwaysLowScoreValidate)
    set_execution_context(api_key="d", provider="openai", model="gpt-4o-mini")

    config = HarnessConfig(
        provider="openai", model="gpt-4o-mini",
        artifacts={"s07_llm": "counting_llm", "s09_validate": "always_low"},
        max_iterations=4,
        max_retries=4,
        validation_threshold=0.7,
        cost_budget_usd=1000,
        disabled_stages={"s05_plan", "s06_context", "s08_execute", "s11_save"},
    )
    emitter = EventEmitter()
    pipeline = Pipeline.from_config(config, emitter)
    state = PipelineState(config=config, user_input="t")
    state.tool_definitions = [{"name":"x","description":"y","input_schema":{"type":"object"}}]
    try:
        await pipeline.run(state)
    except Exception:
        pass

    # validate 가 여러 번 호출됐어야 함 (loop 가 반복됐다는 증거)
    v = state.metadata.get("validate_calls", 0)
    assert v >= 2, f"loop back-jump 가 일어나지 않아 validate 가 {v}번만 호출됨"
    print(f"  ✅ loop_back_jump_retry — validate 호출 {v}회 (back-jump 확인)")


# ──────────────── 3. Strategy/Stage 예외 격리 ────────────────

class ExplodingStage(Stage):
    """s02_memory 대체 — 항상 예외. on_error 훅으로 복구."""
    @property
    def stage_id(self): return "s02_memory"
    @property
    def order(self): return 2
    async def execute(self, state):
        raise RuntimeError("intentional stage failure")
    async def on_error(self, error, state):
        state.metadata["on_error_called"] = True
        state.metadata["recovered_from"] = str(error)
        return {"recovered": True}


async def test_stage_exception_recovered_via_on_error():
    """Stage.execute 가 예외 raise → on_error 가 복구값 반환 → Pipeline 계속"""
    register_stage("s02_memory", "exploding", ExplodingStage)
    register_stage("s07_llm", "counting_llm", CountingLLMStage)
    set_execution_context(api_key="d", provider="openai", model="gpt-4o-mini")

    config = HarnessConfig(
        provider="openai", model="gpt-4o-mini",
        artifacts={"s02_memory": "exploding", "s07_llm": "counting_llm"},
        max_iterations=2,
        cost_budget_usd=1000,
        disabled_stages={"s05_plan", "s06_context", "s08_execute",
                         "s09_validate", "s11_save"},
    )
    emitter = EventEmitter()
    pipeline = Pipeline.from_config(config, emitter)
    state = PipelineState(config=config, user_input="t")
    state.tool_definitions = [{"name":"x","description":"y","input_schema":{"type":"object"}}]
    try:
        await pipeline.run(state)
    except Exception as e:
        # on_error 가 None 반환해서 raise 됐으면 실패지만, dict 반환해서 복구됐어야 함
        pass

    # on_error 가 호출됐고, pipeline 이 s07 까지 진행됐어야 함
    assert state.metadata.get("on_error_called") is True, "on_error 훅 미호출"
    llm_calls = state.metadata.get("llm_calls", 0)
    assert llm_calls >= 1, f"on_error 복구 후 s07 이 안 돌음 (llm_calls={llm_calls})"
    print(f"  ✅ stage_exception_recovered_via_on_error — on_error 복구 후 LLM {llm_calls}회 실행")


# ──────────────── 4. 동시성 스트레스 ────────────────

class TagStage(Stage):
    """단순히 state 에 실행 태그 기록"""
    @property
    def stage_id(self): return "s04_tool_index"
    @property
    def order(self): return 4
    async def execute(self, state):
        # 의도적 yield 로 스케줄러에게 양보 (오염 유발 조건)
        await asyncio.sleep(0.01)
        observed = get_api_key()
        state.metadata["observed_key"] = observed
        state.metadata["tag"] = state.user_input
        return {}


async def test_concurrent_stress_50():
    """50개 파이프라인 병렬 — 각자 key/tag 가 섞이지 않아야 함"""
    register_stage("s04_tool_index", "tag", TagStage)

    async def run_one(tag: str):
        set_execution_context(api_key=f"sk-{tag}", provider="openai", model="gpt-4o-mini")
        config = HarnessConfig(
            provider="openai", model="gpt-4o-mini",
            artifacts={"s04_tool_index": "tag"},
            disabled_stages={"s07_llm", "s08_execute", "s09_validate",
                             "s10_decide", "s11_save", "s12_complete"},
        )
        state = PipelineState(config=config, user_input=tag)
        state.tool_definitions = [{"name":"x","description":"y","input_schema":{"type":"object"}}]
        pipeline = Pipeline.from_config(config, EventEmitter())
        try:
            await pipeline.run(state)
        except Exception:
            pass
        return tag, state

    N = 50
    tags = [f"T{i:03d}" for i in range(N)]
    results = await asyncio.gather(*[run_one(t) for t in tags])

    leaks = 0
    for tag, state in results:
        obs_tag = state.metadata.get("tag")
        obs_key = state.metadata.get("observed_key")
        if obs_tag != tag or obs_key != f"sk-{tag}":
            leaks += 1
    assert leaks == 0, f"{leaks}/{N} 개 state 오염 (contextvars 격리 실패)"
    print(f"  ✅ concurrent_stress_50 — {N}개 병렬, 오염 0")


# ──────────────── Runner ────────────────


async def run():
    tests = [
        test_cost_guard_blocks_loop,
        test_iteration_guard_blocks_loop,
        test_loop_back_jump_retry,
        test_stage_exception_recovered_via_on_error,
        test_concurrent_stress_50,
    ]
    failed = 0
    for t in tests:
        try:
            await t()
        except AssertionError as e:
            print(f"  ❌ {t.__name__} — {e}")
            failed += 1
        except Exception as e:
            import traceback; traceback.print_exc()
            failed += 1
    return failed


if __name__ == "__main__":
    print("=" * 60)
    print("통합 수준 예외 경로 — 실제 pipeline.run() 중 발동 검증")
    print("=" * 60)
    n = asyncio.run(run())
    print("=" * 60)
    if n == 0:
        print("🎉 통합 예외 경로 전부 통과!")
    else:
        print(f"❌ {n}건 실패")
    print("=" * 60)
