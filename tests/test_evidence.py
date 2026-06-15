"""memory/evidence — Evidence Workspace (외부화 작업기억) 자료구조."""

import pytest

from xgen_harness.memory.evidence import (
    DEFAULT_EVIDENCE_CAP,
    EvidenceItem,
    EvidenceSet,
    Importance,
)


def test_importance_rank_order():
    assert Importance.VERY_HIGH.rank > Importance.HIGH.rank > Importance.FAIR.rank > Importance.LOW.rank


def test_importance_coerce():
    assert Importance.coerce("very_high") is Importance.VERY_HIGH
    assert Importance.coerce(Importance.LOW) is Importance.LOW
    assert Importance.coerce("nonsense") is Importance.FAIR
    assert Importance.coerce(None) is Importance.FAIR


def test_curate_adds_item():
    es = EvidenceSet()
    it = es.curate(id="d1", content="solar panels degrade ~0.5%/yr", source="report.pdf",
                   importance="high", score=0.9, turn=1)
    assert it.importance is Importance.HIGH
    assert len(es.items) == 1
    assert es.get("d1").source == "report.pdf"


def test_curate_dedup_by_id_updates():
    es = EvidenceSet()
    es.curate(id="d1", content="v1")
    es.curate(id="d1", content="v2 longer body", importance="very_high")
    assert len(es.items) == 1
    assert es.get("d1").content == "v2 longer body"
    assert es.get("d1").importance is Importance.VERY_HIGH


def test_curate_dedup_by_fingerprint_even_with_new_id():
    es = EvidenceSet()
    es.curate(id="a", content="Same Body Here")
    es.curate(id="b", content="same   body here")  # 같은 지문 → 갱신, 신규 추가 X
    assert len(es.items) == 1


def test_importance_upgrade_only():
    es = EvidenceSet()
    es.curate(id="d1", content="x", importance="very_high")
    es.curate(id="d1", content="x", importance="low")  # downgrade 무시
    assert es.get("d1").importance is Importance.VERY_HIGH


def test_cap_evicts_lowest_importance_first():
    es = EvidenceSet(cap=2)
    es.curate(id="low", content="a", importance="low", turn=1)
    es.curate(id="hi", content="b", importance="very_high", turn=2)
    es.curate(id="mid", content="c", importance="high", turn=3)
    ids = {i.id for i in es.items}
    assert len(es.items) == 2
    assert ids == {"hi", "mid"}  # 최저 중요도(low) 가 eviction


def test_verify_records_verdict():
    es = EvidenceSet()
    es.curate(id="d1", content="claim")
    assert es.verify("d1", True, "checked against source") is not None
    assert es.get("d1").verified is True
    assert es.get("d1").verdict == "checked against source"
    assert es.verify("missing", True) is None


def test_discard():
    es = EvidenceSet()
    es.curate(id="d1", content="x")
    assert es.discard("d1") is True
    assert es.discard("d1") is False
    assert es.items == []


def test_ranked_order():
    es = EvidenceSet()
    es.curate(id="a", content="a", importance="low", score=0.1, turn=1)
    es.curate(id="b", content="b", importance="very_high", score=0.2, turn=2)
    es.curate(id="c", content="c", importance="high", score=0.9, turn=3)
    order = [i.id for i in es.ranked()]
    assert order == ["b", "c", "a"]


def test_render_compact_and_budget():
    es = EvidenceSet()
    for i in range(5):
        es.curate(id=f"d{i}", content=f"body number {i} " * 10, importance="high", turn=i)
    full = es.render()
    assert "evidence workspace" in full
    assert full.count("\n- ") == 5
    budgeted = es.render(max_chars=120)
    assert len(budgeted) <= 200  # 헤더+일부 + budget 안내
    assert "budget reached" in budgeted


def test_render_empty():
    assert EvidenceSet().render() == "[evidence workspace: empty]"


def test_roundtrip_dict_and_json():
    es = EvidenceSet(cap=7)
    es.curate(id="d1", content="hello", source="s", importance="very_high", score=0.5, turn=2)
    es.verify("d1", False, "stale")
    again = EvidenceSet.from_dict(es.to_dict())
    assert again.cap == 7
    assert again.get("d1").importance is Importance.VERY_HIGH
    assert again.get("d1").verified is False
    assert again.get("d1").verdict == "stale"
    again2 = EvidenceSet.from_json(es.to_json())
    assert again2.get("d1").content == "hello"


def test_default_cap_constant():
    assert EvidenceSet().cap == DEFAULT_EVIDENCE_CAP


def test_item_roundtrip():
    it = EvidenceItem(id="x", content="c", importance=Importance.HIGH, verified=True)
    assert EvidenceItem.from_dict(it.to_dict()).verified is True
