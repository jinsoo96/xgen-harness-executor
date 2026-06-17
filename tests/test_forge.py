"""Tests for the opt-in self-forging loop (xgen_harness.forge).

Offline: the loop/algebra/brake are exercised against the REAL engine
registries via SyntheticRunner; the real Pipeline path is smoke-tested with a
FakeProvider. No API key required.
"""
from __future__ import annotations

from xgen_harness.forge import (
    EngineAlgebra,
    Move,
    PipelineRunner,
    SelfForge,
    SyntheticRunner,
)

BENCH = [
    {"id": "t1", "regulated": False},
    {"id": "t2", "regulated": False},
    {"id": "t3", "regulated": True},
    {"id": "t4", "regulated": True},
    {"id": "t5", "regulated": False},
    {"id": "t6", "regulated": True},
]

WEAK_CONFIG = {
    "active_strategies": {},
    "stage_params": {"s08_decide": {"judge_enabled": False}},
    "validation_threshold": 0.5,
    "max_retries": 1,
}


def test_algebra_introspects_real_registries():
    alg = EngineAlgebra()
    moves = alg.legal_moves(WEAK_CONFIG)
    assert moves, "no legal moves discovered from engine registries"

    ops = {m.op for m in moves}
    assert {"toggle_guard", "tune_scalar", "set_stage_param", "edit_criterion"} <= ops

    # builtin guards must be discovered from the real guard registry
    guard_targets = {m.target for m in moves if m.op == "toggle_guard"}
    assert "content" in guard_targets

    # apply + inverse round-trip is deterministic (Inertia-Brake rollback)
    mv = Move("tune_scalar", "validation_threshold", 0.8)
    after = alg.apply(WEAK_CONFIG, mv)
    assert after["validation_threshold"] == 0.8
    restored = alg.apply(after, alg.inverse(WEAK_CONFIG, mv))
    assert restored["validation_threshold"] == 0.5

    # guard toggle writes the real config path (stage_params.s05_policy.guards)
    on = alg.apply(WEAK_CONFIG, Move("toggle_guard", "content", True))
    assert any(g["name"] == "content" for g in on["stage_params"]["s05_policy"]["guards"])


def test_synthetic_loop_improves(tmp_path):
    log = tmp_path / "commits.jsonl"
    res = SelfForge(SyntheticRunner(), max_steps=12, audit_log=log).run(WEAK_CONFIG, BENCH)

    assert res.final_j > res.initial_j
    assert any(c.verdict == "promoted" for c in res.commits)
    assert any(c.verdict == "rolled_back" for c in res.commits)   # inertia-brake fired
    assert all(c.validator_agreement for c in res.commits)        # cross-check held

    # the loop discovered the healthy profile via typed config moves
    assert res.config["stage_params"]["s08_decide"]["judge_enabled"] is True
    assert res.config["validation_threshold"] == 0.8
    assert any(g["name"] == "content" for g in res.config["stage_params"]["s05_policy"]["guards"])
    assert log.exists() and log.read_text(encoding="utf-8").strip()


class _FakeState:
    """Minimal stand-in for PipelineState (the engine state contract forge reads)."""
    def __init__(self, **kw):
        self.validation_score = kw.get("validation_score")
        self.loop_iteration = kw.get("loop_iteration", 0)
        self.loop_decision = kw.get("loop_decision", "complete")
        self.policy_block_reason = kw.get("policy_block_reason")


def test_signal_extraction_is_data_derived_and_extensible():
    from xgen_harness.forge import extract_signals, register_signal_extractor

    # judge off + hit iteration cap without converging
    sig = extract_signals(
        _FakeState(validation_score=None, loop_iteration=5, loop_decision="continue"),
        {"validation_threshold": 0.8, "max_iterations": 4},
    )
    assert "ungated_low_quality" in sig
    assert "iteration_pressure" in sig

    # judge below threshold -> magnitude is the data-derived gap, not a magic number
    sig2 = extract_signals(_FakeState(validation_score=0.6), {"validation_threshold": 0.8})
    assert sig2.get("low_judge_score") == 0.2

    # extensibility: external extractor is merged in
    register_signal_extractor(lambda st, c: {"custom_symptom": 1.0})
    assert "custom_symptom" in extract_signals(_FakeState(validation_score=0.95), {})


def test_reflect_handles_real_trace_symptoms():
    from xgen_harness.forge.reflect import reflect
    from xgen_harness.forge.runner import RunRecord

    refl = reflect([RunRecord("t", 0.5, "partial", {"low_judge_score": 0.3})])
    assert refl and refl.dominant_symptom == "low_judge_score" and refl.candidates

    refl2 = reflect([RunRecord("t", 0.4, "failure", {"iteration_pressure": 1.0})])
    assert refl2 and refl2.candidates[0].op == "tune_scalar"


def test_pipeline_runner_smoke():
    runner = PipelineRunner()                       # FakeProvider(judge_score=0.9)
    rec = runner.run(
        {"validation_threshold": 0.8, "max_retries": 1,
         "stage_params": {"s08_decide": {"judge_enabled": True}}},
        {"id": "smoke", "input": "Say hello in one word."},
    )
    assert rec.task_id == "smoke"
    assert 0.0 <= rec.score <= 1.0
    assert rec.outcome in {"success", "partial", "failure"}


# ---- v1.22.0: Goodhart-defended objective, synthesis seam, judge-path regression ----
import importlib
_reflect_mod = importlib.import_module("xgen_harness.forge.reflect")  # submodule (name shadowed by the fn)
from xgen_harness.forge import (
    FakeProvider,
    Objective,
    RunRecord,
    register_reflector,
    register_secondary_metric,
    synthesize,
)


class _GoodhartRunner:
    """proxy(dev) tasks reward a high threshold; true(held-out) tasks are hurt by it
    — overfitting the proxy degrades truth (the overoptimization the gate must block)."""
    def run(self, config, task):
        thr = config.get("validation_threshold", 0.5)
        s = thr if task.get("proxy") else 1.0 - thr
        return RunRecord(task["id"], round(max(0.0, min(1.0, s)), 4), "partial", {})


def test_objective_blocks_overoptimization():
    _reflect_mod._REFLECTORS.clear()
    register_reflector(lambda tr: [Move("tune_scalar", "validation_threshold", 0.8)])
    try:
        runner = _GoodhartRunner()
        obj = Objective(runner,
                        dev=[{"id": "p1", "proxy": True}, {"id": "p2", "proxy": True}],
                        heldout=[{"id": "t1"}, {"id": "t2"}])
        res = SelfForge(runner, max_steps=3).run({"validation_threshold": 0.5}, obj)
        assert res.final_j <= res.initial_j + 1e-9         # held-out (true) never regressed
        assert any(c.overopt for c in res.commits)         # the proxy-up/true-down move was flagged
        assert res.config["validation_threshold"] == 0.5   # and rejected (unchanged)
    finally:
        _reflect_mod._REFLECTORS.clear()


def test_synthesis_proposes_registered_criteria_and_is_gated():
    alg = EngineAlgebra()
    moves = synthesize([], {"stage_params": {"s08_decide": {"criteria_defs": []}}}, alg)
    assert moves and all(m.op == "edit_criterion" and alg.is_legal(m) for m in moves)
    res = SelfForge(SyntheticRunner(), max_steps=6, enable_synthesis=True).run(WEAK_CONFIG, BENCH)
    assert res.final_j > res.initial_j                     # gated synthesis never breaks the loop


def test_secondary_metric_registry():
    register_secondary_metric(
        "frac_success", lambda recs: sum(r.outcome == "success" for r in recs) / max(1, len(recs)))
    sc = Objective(SyntheticRunner(), dev=BENCH, heldout=BENCH, secondary="frac_success").evaluate(WEAK_CONFIG)
    assert sc.secondary is not None and 0.0 <= sc.secondary <= 1.0


def test_judge_path_no_int_none_regression():
    # judge enabled, aux_max_tokens UNSET (None) — the int(None) bug crashed exactly here
    rec = PipelineRunner(provider=FakeProvider(judge_score=0.8)).run(
        {"stage_params": {"s08_decide": {"judge_enabled": True}}, "judge_use_main": True,
         "max_iterations": 2, "max_retries": 0},
        {"id": "j", "input": "Say hello in one word."},
    )
    assert "int()" not in (rec.error or "")                # the aux_max_tokens fix holds
    assert 0.0 <= rec.score <= 1.0
