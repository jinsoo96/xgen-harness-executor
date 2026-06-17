"""Self-Forging loop over a typed HarnessConfig.

measure(J) -> reflect(trace) -> propose typed move -> cross-check validator
(independent of the reflector) -> Inertia-Brake (empirical J before/after) ->
promote or rollback -> audit. Writes only config knobs; engine code, the
benchmark, and criteria semantics are a locked surface (FORGE §2).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .algebra import EngineAlgebra, Move
from .reflect import Reflection, reflect
from .runner import Runner

# Independent cross-check (second opinion): which (op, target) a symptom maps to.
# Deliberately NOT the reflector's table.
_SYMPTOM_EXPECTED: dict[str, tuple[str, str]] = {
    "ungated_low_quality": ("set_stage_param", "s08_decide:judge_enabled"),
    "accepted_borderline": ("tune_scalar", "validation_threshold"),
    "over_strict_stall": ("tune_scalar", "validation_threshold"),
    "regulation_violation": ("toggle_guard", "content"),
    "missing_criterion": ("edit_criterion", "regulation"),
    "no_recovery": ("tune_scalar", "max_retries"),
    "retry_waste": ("tune_scalar", "max_retries"),
    "low_judge_score": ("tune_scalar", "max_retries"),
    "iteration_pressure": ("tune_scalar", "max_iterations"),
}


@dataclass
class Commit:
    step: int
    reflection_id: str
    lesson: str
    move: str
    inverse: str
    bench_before: float
    bench_after: float
    delta: float
    validator_agreement: bool
    verdict: str            # promoted | rolled_back


@dataclass
class ForgeResult:
    config: dict[str, Any]
    history: list[float]
    commits: list[Commit] = field(default_factory=list)

    @property
    def initial_j(self) -> float:
        return self.history[0]

    @property
    def final_j(self) -> float:
        return self.history[-1]


def _measure(runner: Runner, config: dict[str, Any], bench: list[dict[str, Any]]) -> float:
    return round(sum(runner.run(config, t).score for t in bench) / len(bench), 4)


def _validator_agrees(refl: Reflection, move: Move, algebra: EngineAlgebra) -> bool:
    """Cross-check (independent of the reflector): legal + addresses the dominant
    symptom. Built-in symptoms match an expected (op, target); externally registered
    symptoms fall back to a structural check (legal single-knob move)."""
    if not algebra.is_legal(move):
        return False
    expected = _SYMPTOM_EXPECTED.get(refl.dominant_symptom)
    if expected is None:
        return True                                   # external symptom: structural check only
    return (move.op, move.target) == expected


class SelfForge:
    """Self-improvement loop: evolve a HarnessConfig from run traces against a
    benchmark, promoting improvements and rolling back regressions."""

    def __init__(self, runner: Runner, algebra: Optional[EngineAlgebra] = None,
                 max_steps: int = 12, audit_log: Optional[str | Path] = None) -> None:
        self.runner = runner
        self.algebra = algebra or EngineAlgebra()
        self.max_steps = max_steps
        self.audit_log = Path(audit_log) if audit_log else None

    def run(self, base_config: dict[str, Any], bench: list[dict[str, Any]]) -> ForgeResult:
        config = dict(base_config)
        J = _measure(self.runner, config, bench)
        result = ForgeResult(config=config, history=[J])

        for step in range(self.max_steps):
            traces = [self.runner.run(config, t) for t in bench]
            refl = reflect(traces)
            if refl is None:
                break                                   # no symptoms -> converged

            progressed = False
            for move in refl.candidates:
                if not _validator_agrees(refl, move, self.algebra):
                    continue
                inv = self.algebra.inverse(config, move)
                new_config = self.algebra.apply(config, move)
                J_new = _measure(self.runner, new_config, bench)
                delta = round(J_new - J, 4)
                verdict = "promoted" if delta > 0 else "rolled_back"
                result.commits.append(Commit(
                    step=step, reflection_id=f"r{step:02d}:{refl.dominant_symptom}",
                    lesson=refl.lesson, move=str(move), inverse=str(inv),
                    bench_before=J, bench_after=J_new, delta=delta,
                    validator_agreement=True, verdict=verdict,
                ))
                if verdict == "promoted":
                    config, J = new_config, J_new
                    result.config = config
                    result.history.append(J)
                    progressed = True
                    break                               # re-reflect from the improved state

            if not progressed:
                break                                   # best candidate regressed -> converged

        if self.audit_log:
            self._write_log(result.commits)
        return result

    def _write_log(self, commits: list[Commit]) -> None:
        self.audit_log.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_log.open("w", encoding="utf-8") as f:
            for c in commits:
                f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")


def forge_config(runner: Runner, base_config: dict[str, Any], bench: list[dict[str, Any]],
                 *, max_steps: int = 12, audit_log: Optional[str | Path] = None) -> ForgeResult:
    """Convenience: run one self-forging pass and return the evolved config."""
    return SelfForge(runner, max_steps=max_steps, audit_log=audit_log).run(base_config, bench)
