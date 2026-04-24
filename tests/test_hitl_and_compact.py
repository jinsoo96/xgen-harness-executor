"""HITL Guard + CompactTool + approval 이벤트 단위 테스트 (v0.24.0)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from xgen_harness.core.state import PipelineState
from xgen_harness.events.types import (
    ApprovalDecidedEvent,
    ApprovalRequiredEvent,
    event_to_dict,
)
from xgen_harness.stages.strategies.guard import (
    GuardChain,
    HITLGuard,
    HookContext,
    HookPoint,
)
from xgen_harness.tools.builtin import CompactTool


class FakeEmitter:
    """최소 EventEmitter — Stage 들이 event_emitter 없이 돌 때 대체."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    async def emit(self, event: Any) -> None:
        self.events.append(event)


class FakeConfig:
    verbose_events = True


def _state_with_emitter() -> tuple[PipelineState, FakeEmitter]:
    st = PipelineState()
    st.config = FakeConfig()
    emitter = FakeEmitter()
    st.event_emitter = emitter
    return st, emitter


# ─────────────────────────────────────────────────────────────
# 1. ApprovalRequired/Decided 이벤트 직렬화
# ─────────────────────────────────────────────────────────────

def test_approval_events_serialize() -> None:
    ev = ApprovalRequiredEvent(
        approval_id="apv_1",
        tool_name="delete_file",
        tool_use_id="toolu_1",
        tool_input={"path": "/tmp/x"},
        guard_name="hitl",
        annotations={"destructiveHint": True, "openWorldHint": True},
        reason="destructiveHint=true",
        timeout_sec=60,
    )
    d = event_to_dict(ev)
    assert d["event_type"] == "approval_required"
    assert d["data"]["approval_id"] == "apv_1"
    assert d["data"]["tool_name"] == "delete_file"
    assert d["data"]["annotations"]["destructiveHint"] is True

    ev2 = ApprovalDecidedEvent(approval_id="apv_1", decision="approve", reason="ok")
    d2 = event_to_dict(ev2)
    assert d2["event_type"] == "approval_decided"
    assert d2["data"]["decision"] == "approve"


# ─────────────────────────────────────────────────────────────
# 2. PipelineState.request_approval + resolve_approval 라운드트립
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_request_and_resolve_approval() -> None:
    st, emitter = _state_with_emitter()

    async def resolver() -> None:
        # 잠깐 기다렸다가 외부(이식측)가 resolve 한다고 시뮬레이션
        await asyncio.sleep(0.02)
        assert st.pending_approval_ids() == ["apv_test"]
        ok = st.resolve_approval(
            "apv_test", decision="approve",
            reason="looks fine", edited_input={"path": "/tmp/safe"},
        )
        assert ok is True

    resolver_task = asyncio.create_task(resolver())
    decision = await st.request_approval(
        approval_id="apv_test",
        tool_name="delete_file",
        tool_use_id="toolu_1",
        tool_input={"path": "/tmp/x"},
        guard_name="hitl",
        annotations={"destructiveHint": True},
        reason="destructiveHint=true",
        timeout_sec=2,
    )
    await resolver_task

    assert decision["decision"] == "approve"
    assert decision["edited_input"] == {"path": "/tmp/safe"}

    # 이벤트 순서 확인: request → decided
    types = [type(e).__name__ for e in emitter.events]
    assert types == ["ApprovalRequiredEvent", "ApprovalDecidedEvent"]
    # resolve 후 큐에서 사라짐
    assert st.pending_approval_ids() == []


@pytest.mark.asyncio
async def test_approval_timeout() -> None:
    st, emitter = _state_with_emitter()
    decision = await st.request_approval(
        approval_id="apv_t",
        tool_name="x", tool_use_id="t", tool_input={},
        guard_name="hitl", annotations={}, reason="", timeout_sec=1,
    )
    assert decision["decision"] == "timeout"


# ─────────────────────────────────────────────────────────────
# 3. HITLGuard — destructive 트리거 / 비트리거 / auto-approve / deny
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hitl_skip_non_destructive() -> None:
    """readOnly 도구는 승인 없이 통과 — 모달 발생 0."""
    st, emitter = _state_with_emitter()
    st.tool_definitions = [
        {"name": "safe_tool", "annotations": {"readOnlyHint": True, "destructiveHint": False}}
    ]
    guard = HITLGuard()
    ctx = HookContext(
        hook=HookPoint.PRE_TOOL,
        pending_tool_call={"tool_name": "safe_tool", "tool_use_id": "t1", "tool_input": {}},
    )
    r = await guard.check_async(st, ctx)
    assert r.passed is True
    assert not emitter.events  # 이벤트 발생 X


@pytest.mark.asyncio
async def test_hitl_triggers_on_destructive_and_approves() -> None:
    st, emitter = _state_with_emitter()
    st.tool_definitions = [
        {"name": "delete_file", "annotations": {"destructiveHint": True, "openWorldHint": True}}
    ]
    guard = HITLGuard(timeout_sec=5)
    pending = {"tool_name": "delete_file", "tool_use_id": "t1", "tool_input": {"path": "/bad"}}
    ctx = HookContext(hook=HookPoint.PRE_TOOL, pending_tool_call=pending)

    async def approver() -> None:
        # 이벤트가 먼저 방출되기를 기다린 후 resolve
        for _ in range(20):
            ids = st.pending_approval_ids()
            if ids:
                st.resolve_approval(
                    ids[0], decision="approve",
                    edited_input={"path": "/ok"},
                )
                return
            await asyncio.sleep(0.01)
        raise AssertionError("approval never requested")

    task = asyncio.create_task(approver())
    r = await guard.check_async(st, ctx)
    await task

    assert r.passed is True
    # args 편집 반영
    assert pending["tool_input"] == {"path": "/ok"}
    # 이벤트 2개 (requested + decided)
    assert [type(e).__name__ for e in emitter.events] == [
        "ApprovalRequiredEvent", "ApprovalDecidedEvent",
    ]


@pytest.mark.asyncio
async def test_hitl_deny_blocks_with_tool_error_message() -> None:
    st, emitter = _state_with_emitter()
    st.tool_definitions = [
        {"name": "drop_db", "annotations": {"destructiveHint": True}}
    ]
    guard = HITLGuard(timeout_sec=5)
    ctx = HookContext(
        hook=HookPoint.PRE_TOOL,
        pending_tool_call={"tool_name": "drop_db", "tool_use_id": "t1", "tool_input": {}},
    )

    async def denier() -> None:
        for _ in range(20):
            ids = st.pending_approval_ids()
            if ids:
                st.resolve_approval(ids[0], decision="deny", reason="don't do that")
                return
            await asyncio.sleep(0.01)

    task = asyncio.create_task(denier())
    r = await guard.check_async(st, ctx)
    await task

    assert r.passed is False
    assert r.severity == "block"
    assert "don't do that" in r.tool_error_message
    assert "deny" in r.reason


@pytest.mark.asyncio
async def test_hitl_auto_approve_dev_mode() -> None:
    st, emitter = _state_with_emitter()
    st.tool_definitions = [
        {"name": "delete_file", "annotations": {"destructiveHint": True}}
    ]
    guard = HITLGuard(auto_approve_for_dev=True)
    ctx = HookContext(
        hook=HookPoint.PRE_TOOL,
        pending_tool_call={"tool_name": "delete_file", "tool_use_id": "t1", "tool_input": {}},
    )
    r = await guard.check_async(st, ctx)
    assert r.passed is True
    assert "auto-approve" in r.reason
    # 이벤트 없음 — 승인 대기 건너뜀
    assert not emitter.events


# ─────────────────────────────────────────────────────────────
# 4. GuardChain.invoke_async — 여러 Guard 조합
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_guard_chain_invoke_async_mixes_sync_and_async() -> None:
    """기존 sync Guard 는 check_async 기본구현 (check 래핑) 으로 그대로 동작."""
    from xgen_harness.stages.strategies.guard import Guard, GuardResult

    class AlwaysPass(Guard):
        @property
        def name(self) -> str: return "pass"
        @property
        def hook_points(self): return {HookPoint.PRE_TOOL}
        def check(self, state, context): return GuardResult(passed=True, guard_name="pass")

    st, _ = _state_with_emitter()
    st.tool_definitions = [{"name": "t", "annotations": {"destructiveHint": True}}]
    chain = GuardChain([AlwaysPass(), HITLGuard(auto_approve_for_dev=True)])
    results = await chain.invoke_async(
        HookPoint.PRE_TOOL, st,
        pending_tool_call={"tool_name": "t", "tool_use_id": "x", "tool_input": {}},
    )
    assert len(results) == 2
    assert all(r.passed for r in results)


# ─────────────────────────────────────────────────────────────
# 5. CompactTool
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_compact_history_before() -> None:
    st = PipelineState()
    # 10 턴 대화
    st.messages = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg {i} " + ("x" * 500)}
        for i in range(10)
    ]

    tool = CompactTool(state_ref=st)   # summarizer 미주입 → truncate fallback
    r = await tool.execute({"scope": "history_before:3", "summary_hint": "keep topics"})
    assert not r.is_error
    # 결과: 1 요약 + 마지막 3 = 4 메시지
    assert len(st.messages) == 4
    assert "[compacted history" in st.messages[0]["content"]


@pytest.mark.asyncio
async def test_compact_tool_results_before() -> None:
    st = PipelineState()
    # 4 개 tool_result 메시지 + 2 일반 대화 섞음
    st.messages = [
        {"role": "user", "content": "hi"},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "A" * 800}]},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "b", "content": "B" * 800}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c", "content": "C" * 800}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "d", "content": "D" * 800}]},
    ]

    tool = CompactTool(state_ref=st)
    r = await tool.execute({"scope": "tool_results_before:1", "summary_hint": "key only"})
    assert not r.is_error
    # 마지막 tool_result (d) 만 유지, 앞 3 개는 요약으로 합쳐짐
    tool_result_msgs = [
        m for m in st.messages
        if isinstance(m.get("content"), list)
        and any(b.get("type") == "tool_result" for b in m["content"])
    ]
    assert len(tool_result_msgs) == 1


def test_compact_tool_annotations_destructive() -> None:
    tool = CompactTool(state_ref=PipelineState())
    ann = tool.annotations()
    assert ann["destructiveHint"] is True
    assert ann["readOnlyHint"] is False
    assert ann["openWorldHint"] is False
