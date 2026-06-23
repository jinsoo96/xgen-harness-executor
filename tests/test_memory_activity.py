"""Tests for ActivityFeed — 집단지성 활동현황(정제) 피드 (memory.activity, v1.24)."""
from __future__ import annotations

from xgen_harness.memory.activity import (
    ActivityEvent,
    ActivityFeed,
    activity_from_message,
)


def test_activity_from_message_is_redacted():
    e = activity_from_message(seq=1, actor="jin", raw_message="키 sk-ABCD1234EFGH5678IJKL 로 결제 보내줘",
                              ref={"run_id": "r1"})
    assert "sk-ABCD1234EFGH5678IJKL" not in e.intent  # 원문 비누출
    assert e.actor == "jin" and e.kind == "chat" and e.ref["run_id"] == "r1"


def test_live_returns_latest_per_member():
    f = ActivityFeed()
    f.record(ActivityEvent(seq=1, actor="jin", intent="A", status="done"))
    f.record(ActivityEvent(seq=2, actor="sang", intent="B", status="active"))
    f.record(ActivityEvent(seq=3, actor="jin", intent="C", status="active"))
    live = f.live()
    assert live["jin"].intent == "C"   # 최신
    assert live["sang"].intent == "B"
    assert f.active_members() == ["jin", "sang"]


def test_stream_since_seq():
    f = ActivityFeed()
    for i in range(1, 5):
        f.record(ActivityEvent(seq=i, actor="a", intent=str(i)))
    got = [e.seq for e in f.stream(since_seq=2)]
    assert got == [3, 4]


def test_markdown_and_roundtrip():
    f = ActivityFeed()
    f.record(activity_from_message(seq=1, actor="jin", raw_message="결제 알림 워크플로우 만들어줘", status="active"))
    f.record(ActivityEvent(seq=2, actor="sang", kind="forge", intent="방어 강화", status="done"))
    md = f.to_markdown()
    assert "활동현황" in md and "jin" in md and "sang" in md
    again = ActivityFeed.from_dict(f.to_dict())
    assert len(again.events) == 2
    assert again.live()["sang"].kind == "forge"
