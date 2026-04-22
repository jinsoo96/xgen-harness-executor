"""
Tool Synthesis Loop — LLM 이 생성한 도구를 샌드박스에서 검증 후 레지스트리에 등록 (v0.16.0).

비전 원문 (Phase 5): "카탈로그에 없는 도구를 LLM 이 생성 → 샌드박스 → 갤러리 등록 →
다음 Plan 이 재사용" — **자가 증식 도구 에이전트**.

최소 동작 루프 (Phase 5 기본):
  1. `SynthesizedTool(name, code, input_schema)` 스펙으로 LLM 소스 수신
  2. `Sandbox` 에서 `test_cases` 실행 → 전부 통과해야 다음 단계
  3. 통과 시 `SynthesizedToolSource` 로 래핑 → `register_tool_source` → 카탈로그 자동 합류
  4. 이후 Planner 호출부터 `catalog["tools"]` 에 새 도구 포함 → Plan 이 선택 가능

보안 / 안정성:
  - `Sandbox` 가 subprocess + timeout + stdout cap 을 걸어 엔진 프로세스 분리
  - 코드 자체는 `-I` (isolated mode) 로 실행해 site-packages 외 사용자 모듈 간섭 최소화
  - 실패한 생성 시도는 등록하지 않고 `reasoning` 문자열만 남긴다 (학습용)

다음 단계 (Phase 5.1+):
  - 샌드박스에 리소스 한도 (cgroups/rlimit) 강제
  - 생성된 도구를 갤러리에 upload (compile/gallery.py 와 연동)
  - 다중 LLM 합의 후 등록 (peer review)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..core.sandbox import Sandbox, SandboxResult
from . import register_tool_source, ToolSource

logger = logging.getLogger("harness.tool_synthesis")


@dataclass
class ToolTestCase:
    input: dict
    expected_return: Optional[dict] = None   # None 이면 "성공 실행만 확인"
    expected_contains: Optional[str] = None  # stdout 검사 보조


@dataclass
class SynthesizedTool:
    name: str
    description: str
    code: str                                # stdin JSON 읽어 stdout JSON 1 줄 쓰는 python 소스
    input_schema: dict = field(default_factory=dict)
    test_cases: list[ToolTestCase] = field(default_factory=list)

    def as_source(self) -> "SynthesizedToolSource":
        return SynthesizedToolSource(self)


@dataclass
class SynthesisReport:
    tool_name: str
    passed: bool
    test_results: list[dict] = field(default_factory=list)
    registered: bool = False
    reasoning: str = ""


class SynthesizedToolSource:
    """`SynthesizedTool` 을 `ToolSource` Protocol 로 래핑 — `call_tool` 마다 Sandbox 로 실행.

    엔진 본체 프로세스에서 LLM 생성 코드를 직접 exec 하지 않는다. 매 호출을 subprocess.
    호출 빈도가 높으면 성능 비용이 있으나 보안 우선.
    """

    def __init__(self, spec: SynthesizedTool, *, sandbox: Optional[Sandbox] = None) -> None:
        self.spec = spec
        self.sandbox = sandbox or Sandbox(timeout_sec=10.0)

    async def list_tools(self) -> list[dict]:
        return [{
            "name": self.spec.name,
            "description": self.spec.description,
            "input_schema": self.spec.input_schema or {"type": "object"},
            "source": "synthesized",
        }]

    async def call_tool(self, name: str, args: dict) -> dict:
        if name != self.spec.name:
            return {"content": "", "is_error": True, "error": f"unknown tool: {name}"}
        result = self.sandbox.run_code(self.spec.code, stdin_payload=args)
        if not result.success:
            return {
                "content": (result.stderr or "tool execution failed")[:1000],
                "is_error": True,
                "timed_out": result.timed_out,
            }
        # stdout 의 JSON 첫 대상이 return_value. 없으면 raw stdout.
        payload = result.return_value
        if payload is None:
            payload = result.stdout.strip()
        return {"content": payload, "is_error": False}

    def has_tool(self, name: str) -> bool:
        return name == self.spec.name


def test_synthesized_tool(
    tool: SynthesizedTool,
    *,
    sandbox: Optional[Sandbox] = None,
) -> SynthesisReport:
    """Sandbox 로 test_cases 전부 실행. 하나라도 실패하면 report.passed=False."""
    sb = sandbox or Sandbox(timeout_sec=10.0)
    report = SynthesisReport(tool_name=tool.name, passed=True)

    if not tool.test_cases:
        report.passed = False
        report.reasoning = "test_cases 가 비어있음 — 자가 증식은 반드시 테스트 동반"
        return report

    for idx, tc in enumerate(tool.test_cases):
        r: SandboxResult = sb.run_code(tool.code, stdin_payload=tc.input)
        tr: dict[str, Any] = {
            "index": idx,
            "success": r.success,
            "duration_ms": r.duration_ms,
            "return_value": r.return_value,
            "stderr_head": (r.stderr or "")[:200],
        }
        case_ok = r.success
        if case_ok and tc.expected_return is not None:
            case_ok = (r.return_value == tc.expected_return)
            tr["expected_return_match"] = case_ok
        if case_ok and tc.expected_contains:
            case_ok = tc.expected_contains in (r.stdout or "")
            tr["expected_contains_match"] = case_ok
        tr["passed"] = case_ok
        if not case_ok:
            report.passed = False
        report.test_results.append(tr)

    if report.passed:
        report.reasoning = f"{len(tool.test_cases)} test case 전부 통과"
    else:
        failed = sum(1 for tr in report.test_results if not tr.get("passed"))
        report.reasoning = f"{failed}/{len(tool.test_cases)} test case 실패"
    return report


def synthesize_and_register(
    tool: SynthesizedTool,
    *,
    sandbox: Optional[Sandbox] = None,
    on_pass: Optional[Callable[[SynthesizedToolSource], None]] = None,
) -> SynthesisReport:
    """Tool Synthesis Loop 의 단일 step — 검증 후 통과하면 register_tool_source.

    Usage::

        spec = SynthesizedTool(
            name="slugify",
            description="kebab-case slug 생성",
            code=CODE,
            test_cases=[ToolTestCase({"s": "Hello World"}, expected_return={"slug": "hello-world"})],
        )
        report = synthesize_and_register(spec)
        if report.registered:
            ...  # 다음 Plan 이 이 도구를 catalog 에서 보게 됨
    """
    report = test_synthesized_tool(tool, sandbox=sandbox)
    if not report.passed:
        return report
    source = tool.as_source()
    try:
        register_tool_source(source)
        report.registered = True
        logger.info("[synthesis] registered synthesized tool: %s", tool.name)
        if on_pass is not None:
            on_pass(source)
    except Exception as e:
        report.registered = False
        report.reasoning += f"; register_tool_source failed: {e}"
    return report
