"""
v0.17.0 Policy Gate — 롯데 시나리오 재현 테스트.

기존 이식측 `InputValidationMiddleware` 의 하드코딩 로직
("submit_result 호출 시 fileNo.status=01 판정이 있고 iterative_document_search 호출
수가 0이면 BLOCK") 을 **Guard + 규칙 데이터 + PolicyGateStage** 조합으로 재현.

이 테스트가 통과한다는 것 = 아래 4축 모두 성립:
  1) 하드코딩 제로: 규칙은 params 로 주입, 엔진 코드에 클라이언트명/도구명 없음.
  2) 확장성: 새 Guard 는 entry_points 만 등록하면 합류.
  3) Policy Gate hook (pre_tool): 도구 실행 전 BLOCK 판정.
  4) 실제 동작: pending_tool_calls 제거 + 가짜 tool_result(is_error=True) 주입.
"""

from __future__ import annotations

import asyncio
import pytest

from xgen_harness import describe_guards
from xgen_harness.core.state import PipelineState
from xgen_harness.core.config import HarnessConfig
from xgen_harness.stages.s05_policy.stage import PolicyGateStage
from xgen_harness.stages.strategies.guard import HookPoint, build_guard_chain


# ─── fixture 헬퍼 ────────────────────────────────────────────

def make_state(
    pending_tool_calls: list[dict],
    tool_call_history: list[dict],
    stage_params: dict,
) -> PipelineState:
    """최소 PipelineState — Policy Gate 호출에 필요한 필드만 주입."""
    cfg = HarnessConfig()
    cfg.stage_params = {"s05_policy": stage_params}
    state = PipelineState(user_input="테스트")
    state.config = cfg
    state.pending_tool_calls = pending_tool_calls
    state.tool_call_history = tool_call_history
    return state


# ─── 1. 엔진 번들 Guard 자동 발견 ────────────────────────────

def test_discover_builtin_guards():
    names = {g["name"] for g in describe_guards()}
    assert "tool_precondition" in names, "번들 ToolPreconditionGuard 발견 실패"
    assert "iteration" in names and "cost_budget" in names and "token_budget" in names and "content" in names


# ─── 2. Guard 가 자기 스키마를 self-describe ────────────────

def test_guard_self_describes_schema():
    guards = {g["name"]: g for g in describe_guards()}
    tp = guards["tool_precondition"]
    # description 은 class docstring 에서 파싱됨 — 코드 property 박제 X
    assert tp["description"], "docstring 파싱 description 비어있음"
    # param_schema 에 한국어 label 리터럴 없어야 함
    fields = tp["param_schema"]
    assert fields and fields[0]["id"] == "rules"
    for f in fields:
        assert "label" not in f, f"FieldSchema.label 리터럴 잔존: {f}"
        assert "description" not in f, f"FieldSchema.description 리터럴 잔존: {f}"


# ─── 3. 롯데 시나리오 — submit_result 선행조건 BLOCK ──────

@pytest.mark.asyncio
async def test_lotte_scenario_blocks_submit_result_without_prior_search():
    """
    규칙: submit_result 호출 시 tool_input.fileNo[*].status 중 "01" 이 있고,
          이번 run 에서 iterative_document_search 호출이 0 회면 BLOCK.
    """
    rule = {
        "tool": "submit_result",
        "require_prior": [
            {"tool": "iterative_document_search", "min_count": 1},
        ],
        "when": {"path": "fileNo[*].status", "equals": "01"},
        "message": "시험성적서 합격 판정 전 QA 기준을 iterative_document_search 로 조회하세요.",
    }

    stage_params = {
        "guards": [
            {"name": "tool_precondition", "params": {"rules": [rule]}},
        ],
    }

    pending = [{
        "tool_use_id": "call_1",
        "tool_name": "submit_result",
        "tool_input": {"fileNo": [{"id": "5", "status": "01"}]},
    }]
    state = make_state(
        pending_tool_calls=pending,
        tool_call_history=[],  # 선행 호출 없음 — 위반
        stage_params=stage_params,
    )

    stage = PolicyGateStage()
    result = await stage.invoke_hook(state, "pre_tool")

    assert result["blocked"] == 1, f"BLOCK 되지 않음: {result}"
    assert state.pending_tool_calls == [], "차단된 도구가 pending 에서 제거되지 않음"
    # 가짜 tool_result (is_error=True) 가 주입됐는지 — flush_tool_results 가 호출돼
    # messages 에 user tool_result 메시지로 흘렀거나 tool.results 에 남음.
    flushed = state.messages[-1] if state.messages else None
    assert flushed and flushed["role"] == "user", "차단 안내 메시지 없음"
    content = flushed["content"]
    # content 는 [{"type":"tool_result", "content":"[BLOCKED by tool_precondition] ...", "is_error":True}]
    assert isinstance(content, list) and content[0]["is_error"] is True
    assert "BLOCKED" in content[0]["content"]
    assert "tool_precondition" in content[0]["content"]


@pytest.mark.asyncio
async def test_lotte_scenario_passes_after_prior_search():
    """동일 규칙 + 선행 호출 ≥ 1 → PASS."""
    rule = {
        "tool": "submit_result",
        "require_prior": [{"tool": "iterative_document_search", "min_count": 1}],
        "when": {"path": "fileNo[*].status", "equals": "01"},
    }
    stage_params = {"guards": [{"name": "tool_precondition", "params": {"rules": [rule]}}]}
    pending = [{
        "tool_use_id": "call_2",
        "tool_name": "submit_result",
        "tool_input": {"fileNo": [{"id": "5", "status": "01"}]},
    }]
    state = make_state(
        pending_tool_calls=pending,
        tool_call_history=[
            {"tool_name": "iterative_document_search", "tool_use_id": "x", "tool_input": {}, "iteration": 1},
        ],
        stage_params=stage_params,
    )

    stage = PolicyGateStage()
    result = await stage.invoke_hook(state, "pre_tool")

    assert result["blocked"] == 0, f"선행 호출 있음에도 BLOCK: {result}"
    assert len(state.pending_tool_calls) == 1, "pending 이 그대로 있어야 (s07_act 가 실행)"


@pytest.mark.asyncio
async def test_when_condition_skips_rule():
    """페이로드 when 조건 미충족 시 규칙 적용 안 함 (BLOCK 안 됨)."""
    rule = {
        "tool": "submit_result",
        "require_prior": [{"tool": "iterative_document_search", "min_count": 1}],
        "when": {"path": "fileNo[*].status", "equals": "01"},  # status=01 일 때만
    }
    stage_params = {"guards": [{"name": "tool_precondition", "params": {"rules": [rule]}}]}
    pending = [{
        "tool_use_id": "call_3",
        "tool_name": "submit_result",
        "tool_input": {"fileNo": [{"id": "5", "status": "99"}]},  # 불합격
    }]
    state = make_state(
        pending_tool_calls=pending,
        tool_call_history=[],  # 선행 없음
        stage_params=stage_params,
    )

    stage = PolicyGateStage()
    result = await stage.invoke_hook(state, "pre_tool")

    # when 조건 (status=01) 미충족 → 규칙 건너뜀 → BLOCK 안 됨
    assert result["blocked"] == 0, "when 조건 미충족인데 규칙 적용됨"


# ─── 4. Guard 체인 hook_points 필터링 ──────────────────────

def test_guard_chain_filters_by_hook():
    """hook_points 에 맞는 Guard 만 실행 — 다른 훅에서는 no-op."""
    chain = build_guard_chain([
        {"name": "tool_precondition", "params": {"rules": []}},  # PRE_TOOL 전용
        {"name": "iteration"},                                    # LOOP_BOUNDARY 전용
    ])
    class _FakeState:
        tool_call_history = []
        pending_tool_calls = []
    # LOOP_BOUNDARY 호출 — tool_precondition 은 skip, iteration 만 실행
    results = chain.invoke(HookPoint.LOOP_BOUNDARY, _FakeState())
    names = [r.guard_name for r in results]
    assert "tool_precondition" not in names
    assert "iteration" in names


if __name__ == "__main__":
    # 수동 실행: pytest 없이 asyncio.run 으로 주요 시나리오 빠르게 확인
    asyncio.run(test_lotte_scenario_blocks_submit_result_without_prior_search())
    asyncio.run(test_lotte_scenario_passes_after_prior_search())
    asyncio.run(test_when_condition_skips_rule())
    test_discover_builtin_guards()
    test_guard_self_describes_schema()
    test_guard_chain_filters_by_hook()
    print("All Policy Gate tests passed.")
