"""Tests for ProjectLifecycle — 전 생애주기 게이트 상태기계 (memory.lifecycle, v1.24)."""
from __future__ import annotations

import pytest

from xgen_harness.memory.lifecycle import (
    LifecyclePhase,
    ProjectLifecycle,
    ProgressStatus,
)


def test_gates_enforce_order():
    lc = ProjectLifecycle(project_id="p1")
    assert lc.can_advance(LifecyclePhase.INTAKE) is True
    # SIMULATE 는 INTAKE done 전엔 불가
    assert lc.can_advance(LifecyclePhase.SIMULATE) is False
    with pytest.raises(ValueError):
        lc.advance(LifecyclePhase.PUBLISH)
    lc.advance(LifecyclePhase.INTAKE)
    assert lc.can_advance(LifecyclePhase.SIMULATE) is True


def test_gate_failure_blocks_next():
    lc = ProjectLifecycle(project_id="p1")
    lc.advance(LifecyclePhase.INTAKE)
    # 시뮬레이션은 done 이지만 게이트 미통과 → publish 불가
    lc.advance(LifecyclePhase.SIMULATE, gate_passed=False, artifact_ref="bundle#xx")
    assert lc.can_advance(LifecyclePhase.PUBLISH) is False
    with pytest.raises(ValueError):
        lc.advance(LifecyclePhase.PUBLISH)
    # 게이트 통과로 재진행하면 publish 가능
    lc.advance(LifecyclePhase.SIMULATE, gate_passed=True)
    assert lc.can_advance(LifecyclePhase.PUBLISH) is True


def test_full_progression_and_state():
    lc = ProjectLifecycle(project_id="xgen-self")
    lc.advance(LifecyclePhase.INTAKE, artifact_ref="msg-1")
    lc.advance(LifecyclePhase.SIMULATE, gate_passed=True, artifact_ref="bundle#abc")
    assert lc.current_phase() == LifecyclePhase.SIMULATE
    assert lc.next_phase() == LifecyclePhase.PUBLISH
    lc.advance(LifecyclePhase.PUBLISH, artifact_ref="tool-9")
    lc.advance(LifecyclePhase.DEPLOY, artifact_ref="k8s/svc")
    lc.advance(LifecyclePhase.MANAGE, artifact_ref="memory.md")
    assert lc.is_complete() is True
    assert lc.next_phase() is None


def test_markdown_and_roundtrip():
    lc = ProjectLifecycle(project_id="p1")
    lc.advance(LifecyclePhase.INTAKE)
    lc.advance(LifecyclePhase.SIMULATE, gate_passed=True, artifact_ref="bundle#1")
    md = lc.to_markdown()
    assert "프로젝트 생애주기 p1" in md and "simulate" in md
    again = ProjectLifecycle.from_dict(lc.to_dict())
    assert again.steps["simulate"].gate_passed is True
    assert again.steps["simulate"].status == ProgressStatus.DONE
