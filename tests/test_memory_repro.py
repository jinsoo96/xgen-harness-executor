"""Tests for ReproBundle — 형상 관리/재현 단위 (memory.repro, v1.24)."""
from __future__ import annotations

from xgen_harness.memory.repro import (
    ReproBundle,
    build_repro_bundle,
    config_fingerprint,
)
from xgen_harness.memory.refine import refine_message

CFG = {"provider": "openai", "max_iterations": 8, "stage_params": {"s08_decide": {"judge_enabled": True}}}


def test_fingerprint_deterministic_and_ignores_volatile():
    a = config_fingerprint(CFG)
    b = config_fingerprint(dict(CFG))
    assert a == b and len(a) == 16
    # 휘발성 식별자는 지문에 영향 없음
    with_ids = {**CFG, "execution_id": "x1", "run_id": "r1", "interaction_id": "i1"}
    assert config_fingerprint(with_ids) == a
    # 정책 변경은 지문을 바꾼다
    assert config_fingerprint({**CFG, "max_iterations": 10}) != a


def test_reproduces_check():
    bundle = build_repro_bundle(bundle_id="b1", config=CFG, judge_score=0.91, gate_passed=True)
    assert bundle.reproduces(dict(CFG)) is True
    assert bundle.reproduces({**CFG, "max_iterations": 99}) is False


def test_bundle_hash_stable_and_content_sensitive():
    b1 = build_repro_bundle(bundle_id="b1", config=CFG, judge_score=0.9, gate_passed=True)
    b2 = build_repro_bundle(bundle_id="b2-different-id", config=CFG, judge_score=0.9, gate_passed=True)
    # bundle_id 는 hash 에 안 들어감 — 같은 의도+config+품질이면 같은 형상 식별자
    assert b1.bundle_hash() == b2.bundle_hash()
    b3 = build_repro_bundle(bundle_id="b3", config=CFG, judge_score=0.5, gate_passed=False)
    assert b3.bundle_hash() != b1.bundle_hash()


def test_compose_with_refined_memory_and_roundtrip():
    mem = refine_message("키 sk-AAAA1111BBBB2222CCCC 로 결제 알림 보내줘", "워크플로우 구성 완료",
                         memory_id="m1", provenance={"run_id": "r1"})
    bundle = build_repro_bundle(
        bundle_id="b1", config=CFG, intent_memory=mem, judge_score=0.88, gate_passed=True,
        provenance={"tenant": "t", "project": "p", "run": "r1"}, spec_ref="spec.json#abc")
    md = bundle.to_markdown()
    assert "재현 번들 b1" in md and "config 지문" in md
    assert "sk-AAAA1111BBBB2222CCCC" not in md  # 정제기억 경유라 원문 비누출
    # round-trip
    again = ReproBundle.from_dict(bundle.to_dict())
    assert again.config_fingerprint == bundle.config_fingerprint
    assert again.intent_memory.memory_id == "m1"
    assert again.bundle_hash() == bundle.bundle_hash()
