"""Tests for RuntimeConfigMutator — the gated runtime self-config seam (v1.24).

Exercises the REAL HarnessConfig + EngineAlgebra (no API key, no provider).
Verifies the three gate modes (off/observe/act), legality admission, inverse
rollback, apply_plan (the s00 _merge_plan_into_config replacement), per-node
overrides, env persistence via MutableConfigService, and the PipelineState wiring.
"""
from __future__ import annotations

from xgen_harness.core.config import HarnessConfig
from xgen_harness.core.runtime_config import RuntimeConfigMutator
from xgen_harness.core.state import PipelineState
from xgen_harness.core.planner import HarnessPlan


def _cfg(**kw) -> HarnessConfig:
    base = dict(max_retries=1, max_iterations=6, validation_threshold=0.5)
    base.update(kw)
    return HarnessConfig(**base)


# ── gate: off (default) is fully inert ───────────────────────────────
def test_off_mode_is_noop():
    c = _cfg()
    m = RuntimeConfigMutator(c, mode="off")
    assert m.set_stage_param("s06_context", "rag_pd_mode", "eager") is False
    assert m.set_scalar("max_retries", 3) is False
    assert m.set_node_override("n1", "temperature", 0.1) is False
    # config untouched
    assert c.stage_params.get("s06_context", {}).get("rag_pd_mode") is None
    assert c.max_retries == 1
    assert not m.journal


# ── gate: observe records proposals but does not apply ───────────────
def test_observe_records_without_applying():
    c = _cfg()
    m = RuntimeConfigMutator(c, mode="observe")
    assert m.set_stage_param("s06_context", "rag_pd_mode", "eager") is True
    assert m.set_scalar("max_retries", 3) is True
    # nothing applied
    assert c.stage_params.get("s06_context", {}).get("rag_pd_mode") is None
    assert c.max_retries == 1
    # but proposed
    assert len(m.proposals) == 2
    assert not m.journal
    assert any("rag_pd_mode" in d for d in m.diff())


# ── gate: act applies, journals, and is reversible ───────────────────
def test_act_applies_and_rolls_back():
    c = _cfg()
    m = RuntimeConfigMutator(c, mode="act")
    assert m.set_stage_param("s06_context", "rag_pd_mode", "eager") is True
    assert m.set_scalar("max_retries", 3) is True
    # applied to the real dataclass
    assert c.stage_params["s06_context"]["rag_pd_mode"] == "eager"
    assert c.max_retries == 3
    assert len(m.journal) == 2
    # rollback restores the scalar to its original value
    n = m.rollback()
    assert n == 2
    assert c.max_retries == 1
    assert not m.journal


# ── legality admission filter ────────────────────────────────────────
def test_illegal_moves_rejected_even_in_act():
    c = _cfg()
    m = RuntimeConfigMutator(c, mode="act")
    # not a tunable scalar key
    assert m.set_scalar("not_a_real_scalar", 1) is False
    # strategy value not in any registry slot
    assert m.set_strategy("s07_act", "__definitely_not_a_strategy__") is False
    assert not m.journal


# ── node_overrides (outside algebra vocabulary, gated only) ──────────
def test_node_override_applies_in_act():
    c = _cfg()
    m = RuntimeConfigMutator(c, mode="act")
    assert m.set_node_override("agent-1", "provider", "anthropic") is True
    assert c.node_overrides["agent-1"]["provider"] == "anthropic"
    m.rollback()
    assert c.node_overrides["agent-1"]["provider"] is None


# ── apply_plan == gated _merge_plan_into_config ──────────────────────
def test_apply_plan_gated():
    plan = HarnessPlan(
        params={"s06_context": {"rag_pd_mode": "eager"}},
        max_iterations=10,
    )
    # off → nothing
    c_off = _cfg()
    assert RuntimeConfigMutator(c_off, mode="off").apply_plan(plan) == 0
    assert c_off.max_iterations == 6
    # act → applied
    c_act = _cfg()
    n = RuntimeConfigMutator(c_act, mode="act").apply_plan(plan)
    assert n == 2
    assert c_act.stage_params["s06_context"]["rag_pd_mode"] == "eager"
    assert c_act.max_iterations == 10


# ── persist_env via a MutableConfigService duck-type ─────────────────
class _FakeCfgSvc:
    def __init__(self):
        self.writes = []

    async def set_value(self, key, value, category=""):
        self.writes.append((key, value, category))
        return True


class _FakeServices:
    def __init__(self, cfg):
        self.config = cfg


async def test_persist_env_writes_through_service():
    svc = _FakeCfgSvc()
    c = _cfg()
    m = RuntimeConfigMutator(c, services=_FakeServices(svc), mode="act")
    ok = await m.persist_env("provider.openai.temperature", "0.3", "tuning")
    assert ok is True
    assert svc.writes == [("provider.openai.temperature", "0.3", "tuning")]


async def test_persist_env_graceful_without_service():
    c = _cfg()
    m = RuntimeConfigMutator(c, services=None, mode="act")
    assert await m.persist_env("k", "v") is False  # no set_value available → no-op


# ── PipelineState wiring reads the gate from config ──────────────────
def test_state_get_config_mutator_reads_gate():
    c_off = _cfg(runtime_self_govern="off")
    st_off = PipelineState(config=c_off)
    assert st_off.get_config_mutator().mode == "off"

    c_act = _cfg(runtime_self_govern="act")
    st_act = PipelineState(config=c_act)
    mut = st_act.get_config_mutator()
    assert mut.mode == "act"
    assert mut.set_scalar("max_iterations", 8) is True
    assert c_act.max_iterations == 8
