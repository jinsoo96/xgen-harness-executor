"""Tests for message → 정제 장기기억 (memory.refine, v1.24).

프라이버시 floor(원문 미저장·민감정보 마스킹), 결정성, md 표면, 라운드트립을 검증.
"""
from __future__ import annotations

from xgen_harness.memory.refine import (
    ExtractiveRefiner,
    RefinedMemory,
    redact_sensitive,
    refine_message,
)

RAW = "내 키 sk-ABCD1234EFGH5678IJKL 로 john.doe@acme.com 한테 010-1234-5678 결제 알림 보내줘"
RESULT = "결제 알림 발송 워크플로우를 구성했습니다. SMTP 노드 + 스케줄 트리거로 연결."


def test_redaction_masks_sensitive():
    red, n = redact_sensitive(RAW)
    assert "sk-ABCD1234EFGH5678IJKL" not in red
    assert "john.doe@acme.com" not in red
    assert "010-1234-5678" not in red
    assert "⟪api_key⟫" in red and "⟪email⟫" in red
    assert n >= 3


def test_refine_never_leaks_raw_secrets():
    m = refine_message(RAW, RESULT, memory_id="mem-1",
                       provenance={"run_id": "exec-1", "thread_id": "t-1"})
    blob = m.intent + " " + m.outcome
    # 원문 민감정보가 정제 기억에 절대 없어야 한다 (프라이버시 floor)
    assert "sk-ABCD1234EFGH5678IJKL" not in blob
    assert "john.doe@acme.com" not in blob
    assert m.redaction_count >= 3
    assert m.provenance["run_id"] == "exec-1"
    assert m.source == "message"


def test_extractive_refiner_is_deterministic():
    r = ExtractiveRefiner()
    a = r.refine(RAW, RESULT, {})
    b = r.refine(RAW, RESULT, {})
    assert a == b
    assert isinstance(a["tags"], list)


def test_markdown_surface_and_roundtrip():
    m = refine_message(RAW, RESULT, memory_id="mem-2",
                       provenance={"spec_ref": "spec.json#abc"})
    md = m.to_markdown()
    assert md.startswith("## mem-2")
    assert "**의도:**" in md and "**결과:**" in md
    assert "마스킹" in md  # redaction 가시화
    # round-trip
    again = RefinedMemory.from_dict(m.to_dict())
    assert again.memory_id == m.memory_id
    assert again.intent == m.intent
    assert again.redaction_count == m.redaction_count


def test_pluggable_refiner_contract():
    class _UpperRefiner:
        def refine(self, raw_message, result, context):
            return {"intent": "FIXED", "outcome": result[:10], "tags": ["x"]}

    m = refine_message(RAW, RESULT, memory_id="mem-3", refiner=_UpperRefiner())
    assert m.intent == "FIXED"
    # refiner 가 원문을 안 줬어도 redaction_count 는 엔진이 raw 기준으로 계산
    assert m.redaction_count >= 3
