"""Planner 정규화 지문 테스트 — behavioral fingerprinting.

`HarnessPlanner._build_plan_from_tool_input` 은 순수 함수 (LLM 호출 없음).
이 함수가 하는 일:
  - submit_plan tool_input 타입 방어
  - 알려지지 않은 stage_id 필터 (환각 방지)
  - REQUIRED_STAGES 강제 삽입
  - 순서 보정
  - orchestrator_hint 레지스트리 검증

엔진 수정으로 이 정규화 결과가 변하면 test 가 실패 → fixture 를 리뷰 후 업데이트.
단위 테스트로는 잡기 어려운 "행동 변화" 를 지문으로 포착.

각 케이스의 지문(JSON) 은 `tests/fingerprints/plan_*.json` 에 저장. 첫 실행 시
baseline 으로 기록되고, 이후 실행은 비교.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import pytest

from xgen_harness.core.planner import HarnessPlanner


FIXTURE_DIR = Path(__file__).parent / "fingerprints"
FIXTURE_DIR.mkdir(exist_ok=True)


# 실측 상태 (registry 13 stages) 에 맞춘 표준 catalog
# s00_harness 는 자기 자신이라 LLM 선택 대상에 보통 포함 안 함.
CATALOG = {
    "stages": [
        {"stage_id": sid}
        for sid in [
            "s01_input", "s02_history", "s03_prompt", "s04_tool",
            "s05_policy", "s05_strategy", "s06_context",
            "s07_act", "s08_judge", "s09_decide", "s10_save", "s11_finalize",
        ]
    ],
}


CASES: list[dict[str, Any]] = [
    {
        "id": "simple_qa",
        "tool_input": {
            "chosen": ["s01_input", "s03_prompt", "s07_act", "s11_finalize"],
            "skipped": {"s02_history": "no prior turn", "s08_judge": "low complexity"},
            "strategies": {"s07_act": "parallel_read"},
            "reasoning": "단순 QA — 평가·기억 생략",
            "done": False,
        },
    },
    {
        "id": "rag_with_judge",
        "tool_input": {
            "chosen": [
                "s01_input", "s02_history", "s03_prompt", "s04_tool",
                "s05_strategy", "s06_context", "s07_act", "s08_judge", "s11_finalize",
            ],
            "strategies": {
                "s06_context": "compactor_pd",
                "s07_act": "parallel_read",
                "s08_judge": "llm_judge",
            },
            "params": {"s06_context": {"top_k": 5}},
            "reasoning": "RAG 문서 검색 + 품질 평가",
            "max_iterations": 3,
            "done": False,
        },
    },
    {
        "id": "filters_unknown_stage",
        "tool_input": {
            # 존재하지 않는 stage_id — Planner 가 filter 해야 함
            "chosen": ["s01_input", "s99_hallucinated", "s07_act", "s11_finalize"],
            "reasoning": "환각 방지 테스트",
            "done": False,
        },
    },
    {
        "id": "injects_required",
        "tool_input": {
            # REQUIRED (s01_input / s09_decide / s11_finalize) 누락 — 강제 주입되어야
            "chosen": ["s07_act"],
            "reasoning": "필수 Stage 강제 테스트",
            "done": False,
        },
    },
    {
        "id": "malformed_types_defended",
        "tool_input": {
            # 타입이 전부 틀림 — Planner 방어 로직이 기본값으로 정규화해야
            "chosen": "not a list",
            "skipped": "not a dict",
            "params": None,
            "strategies": 42,
            "reasoning": {"weird": "object"},
            "done": "yes please",
        },
    },
]


def _planner() -> HarnessPlanner:
    # HarnessPlanner 는 provider/config 보유 안 함 — state.provider 경유.
    # _build_plan_from_tool_input 는 순수 함수라 기본 생성자로 충분.
    return HarnessPlanner()


def _normalize(plan) -> dict[str, Any]:
    """HarnessPlan 에서 지문용 결정적 필드만 뽑아 정렬."""
    if is_dataclass(plan):
        d = asdict(plan)
    else:
        d = dict(plan)

    # reasoning 은 LLM 자연어라 결정적이지 않음 — 존재 여부만 기록
    d.pop("reasoning", None)

    # set / dict 정렬 (결정적 비교)
    if isinstance(d.get("skipped"), dict):
        d["skipped"] = {k: d["skipped"][k] for k in sorted(d["skipped"])}
    if isinstance(d.get("strategies"), dict):
        d["strategies"] = {k: d["strategies"][k] for k in sorted(d["strategies"])}
    if isinstance(d.get("params"), dict):
        d["params"] = {k: d["params"][k] for k in sorted(d["params"])}
    return d


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_plan_fingerprint(case: dict[str, Any]) -> None:
    planner = _planner()
    plan = planner._build_plan_from_tool_input(case["tool_input"], CATALOG)
    actual = _normalize(plan)

    fixture = FIXTURE_DIR / f"plan_{case['id']}.json"
    if not fixture.exists():
        fixture.write_text(json.dumps(actual, ensure_ascii=False, indent=2) + "\n")
        pytest.skip(f"baseline created: {fixture.name} — rerun to compare")

    expected = json.loads(fixture.read_text())
    assert actual == expected, (
        f"Plan 지문 변경됨 — {case['id']}\n"
        f"expected: {json.dumps(expected, ensure_ascii=False, indent=2)}\n"
        f"actual  : {json.dumps(actual, ensure_ascii=False, indent=2)}\n"
        f"의도된 변경이면 {fixture} 를 삭제 후 재실행하여 baseline 갱신."
    )


def test_fingerprint_runs_without_llm() -> None:
    """이 테스트 모듈이 실 Provider / 네트워크 호출 없이 돌아감을 확인."""
    # _build_plan_from_tool_input 은 순수 함수라 API 키 불필요.
    # 만약 내부가 네트워크 호출하도록 변하면 이 테스트는 환경에 따라 실패하게 될 것.
    planner = _planner()
    plan = planner._build_plan_from_tool_input({"chosen": [], "done": True}, CATALOG)
    assert plan.done is True
