"""Self-Forge — opt-in self-improvement loop over a typed HarnessConfig.

The harness tunes its OWN configuration — which registered strategy / guard /
criterion / scalar each stage uses — from its own run traces, validated by an
external benchmark J, promoting improvements and rolling back regressions.

This is provider-agnostic and domain-blind: the legal-move set is discovered
from the engine's own registries (substitution algebra), never hardcoded; the
loop only writes config knobs (engine code, benchmark, and criteria semantics
are a locked surface). Not imported by `import xgen_harness` — opt in with
`from xgen_harness.forge import SelfForge`.

See spec: forge-engineering/spec/CONFIG-FORGE.md.
"""
from __future__ import annotations

from .algebra import EngineAlgebra, Move
from .loop import Commit, ForgeResult, SelfForge, forge_config
from .reflect import Reflection, reflect, register_symptom_fix
from .runner import FakeProvider, PipelineRunner, RunRecord, Runner, SyntheticRunner
from .signals import extract_signals, register_signal_extractor

__all__ = [
    "EngineAlgebra",
    "Move",
    "Reflection",
    "reflect",
    "register_symptom_fix",
    "extract_signals",
    "register_signal_extractor",
    "Runner",
    "RunRecord",
    "PipelineRunner",
    "SyntheticRunner",
    "FakeProvider",
    "SelfForge",
    "ForgeResult",
    "Commit",
    "forge_config",
]
