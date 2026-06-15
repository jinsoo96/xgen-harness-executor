"""builtin evidence 툴 (curate/verify/list_evidence) — state + pd_stores 배선."""

import pytest

from xgen_harness.core.state import PipelineState
from xgen_harness.memory.evidence import EvidenceSet
from xgen_harness.tools.builtin import (
    EVIDENCE_PD_KIND,
    CurateTool,
    ListEvidenceTool,
    VerifyTool,
)


async def test_curate_stores_in_state_and_pd():
    st = PipelineState()
    res = await CurateTool(st).execute(
        {"id": "d1", "content": "solar 0.5%/yr", "importance": "high", "source": "r.pdf"}
    )
    assert not res.is_error
    es = st.metadata["evidence_set"]
    assert isinstance(es, EvidenceSet)
    assert es.get("d1").importance.value == "high"
    # PD step-in: 전체 본문은 pd_stores["evidence"] 로 보존
    entry = st.pd_fetch(EVIDENCE_PD_KIND, "d1")
    assert entry is not None and entry["full"] == "solar 0.5%/yr"


async def test_curate_requires_id_and_content():
    st = PipelineState()
    assert (await CurateTool(st).execute({"id": "", "content": "x"})).is_error
    assert (await CurateTool(st).execute({"id": "a", "content": "  "})).is_error


async def test_verify_records_and_missing_errors():
    st = PipelineState()
    await CurateTool(st).execute({"id": "d1", "content": "claim"})
    res = await VerifyTool(st).execute({"id": "d1", "ok": False, "note": "stale"})
    assert not res.is_error
    assert st.metadata["evidence_set"].get("d1").verified is False
    assert (await VerifyTool(st).execute({"id": "zzz", "ok": True})).is_error


async def test_list_evidence_compact_view():
    st = PipelineState()
    await CurateTool(st).execute({"id": "d1", "content": "a", "importance": "very_high"})
    await CurateTool(st).execute({"id": "d2", "content": "b", "importance": "low"})
    res = await ListEvidenceTool(st).execute({})
    assert not res.is_error
    assert "evidence workspace" in res.content
    assert "d1" in res.content and "d2" in res.content


async def test_cap_read_from_config():
    class Cfg:
        evidence_cap = 1

    st = PipelineState(config=Cfg())
    await CurateTool(st).execute({"id": "d1", "content": "a", "importance": "low"})
    await CurateTool(st).execute({"id": "d2", "content": "b", "importance": "high"})
    es = st.metadata["evidence_set"]
    assert es.cap == 1
    assert len(es.items) == 1 and es.get("d2") is not None  # 최저 중요도 eviction
