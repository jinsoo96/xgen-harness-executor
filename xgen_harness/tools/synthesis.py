"""
Tool Synthesis Loop — LLM 이 생성한 도구를 샌드박스에서 검증 후 레지스트리에 등록.

비전 원문 (Phase 5): "카탈로그에 없는 도구를 LLM 이 생성 → 샌드박스 → 갤러리 등록 →
다음 Plan 이 재사용" — **자가 증식 도구 에이전트**.

v0.16.1 자가감사 수정:
  - prefix/tags/manifest 스키마 박제 제거
  - 갤러리 업로드는 `compile.local_manifest` 단일 모듈 경유
  - 네임스페이스는 모듈 최상단 상수 한 곳에서만 (외부 override 가능)
  - 같은 로컬 매니페스트 포맷을 Node Plugin 과 공유 → drift 불가

보안:
  - Sandbox rlimit (CPU/AS/NOFILE/FSIZE) + timeout + `-I` isolated
  - 실패한 생성 시도는 등록 차단, reasoning 만 기록

확장 지점:
  - 네임스페이스: `set_synthesis_namespace("<prefix>")` 로 교체 가능
  - 태그: `set_synthesis_tags([...])` 로 교체
  - 실행 격리: 호출자가 `Sandbox(limits=...)` 를 주입하면 임의 정책
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..core.sandbox import Sandbox, SandboxResult
from . import register_tool_source, ToolSource

logger = logging.getLogger("harness.tool_synthesis")


# ────────────────────────────────────────────────────────────────
#  설정 상수 — 이 모듈에서 유일하게 박는 이름. 외부에서 set_* 로 교체 가능.
# ────────────────────────────────────────────────────────────────

_NAMESPACE = "synth.tools"          # NOMNode.id prefix → "{namespace}.{tool_name}"
_ENTRY_PREFIX = "synth"             # NOMNode.entry → "{entry_prefix}:{tool_name}"
_TAGS: tuple[str, ...] = ("synthesized", "tool")
_PLUGIN_PACKAGE = "synthesized"
_SOURCE_LABEL = "synthesized"       # ToolSource.list_tools 의 'source' 값


def set_synthesis_namespace(prefix: str) -> None:
    """NOMNode.id 의 prefix 를 런타임 교체 (기본 'synth.tools')."""
    global _NAMESPACE
    _NAMESPACE = prefix


def set_synthesis_tags(tags: list[str]) -> None:
    """NOMNode.tags 를 런타임 교체."""
    global _TAGS
    _TAGS = tuple(tags)


def get_synthesis_config() -> dict:
    """현재 synthesis 모듈 네임스페이스·태그 확인 (진단용)."""
    return {
        "namespace": _NAMESPACE,
        "entry_prefix": _ENTRY_PREFIX,
        "tags": list(_TAGS),
        "plugin_package": _PLUGIN_PACKAGE,
        "source_label": _SOURCE_LABEL,
    }


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
            "source": _SOURCE_LABEL,
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
        # v0.16.5 — LLM/Anthropic/OpenAI API 는 tool_result content 를 **string** 으로 요구.
        # dict/list 반환 시 JSON 직렬화. 엔진 어떤 경로에서 slicing 해도 타입 안전.
        import json as _json
        payload = result.return_value
        if payload is None:
            payload = result.stdout.strip()
        if not isinstance(payload, str):
            try:
                payload = _json.dumps(payload, ensure_ascii=False, default=str)
            except Exception:
                payload = str(payload)
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
    upload_to_gallery: Optional[str] = None,
) -> SynthesisReport:
    """Tool Synthesis Loop 의 단일 step — 검증 후 통과하면 register_tool_source.

    Args:
        upload_to_gallery: 갤러리 매니페스트 파일 경로. 제공 시 통과한 도구를
            해당 JSON 에 NOMNode 로 append. pip wheel 갤러리 또는 로컬 파일
            갤러리 모두 이 규격.

    Usage::

        spec = SynthesizedTool(
            name="slugify",
            description="kebab-case slug 생성",
            code=CODE,
            test_cases=[ToolTestCase({"s": "Hello World"}, expected_return={"slug": "hello-world"})],
        )
        report = synthesize_and_register(spec, upload_to_gallery="gallery/manifest.json")
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

    # v0.16.1 — 갤러리 업로드 연동 (Phase 5.1).
    if upload_to_gallery:
        try:
            saved_to = upload_synthesized_to_gallery(tool, upload_to_gallery)
            report.reasoning += f"; uploaded to gallery: {saved_to}"
        except Exception as e:
            report.reasoning += f"; gallery upload failed: {e}"

    return report


# ────────────────────────────────────────────────────────────────
#  Tool Synthesis → NOM → Gallery 배포 경로 (Phase 5.1)
# ────────────────────────────────────────────────────────────────

def to_nom_node(tool: SynthesizedTool) -> "NOMNode":
    """SynthesizedTool → NOMNode 변환 (통일 IR).

    prefix/tags/package 는 모듈 상수 참조 — set_synthesis_* 로 런타임 교체 가능.
    소스 코드는 NOMNode.kind_meta["synthesis_code"] 에 저장하여 갤러리에서 복원 가능.
    """
    from ..core.nom import NOMNode, NOMKind
    return NOMNode(
        id=f"{_NAMESPACE}.{tool.name}",
        kind=NOMKind.TOOL,
        name=tool.name,
        description=tool.description,
        source_file="",
        entry=f"{_ENTRY_PREFIX}:{tool.name}",
        kind_meta={
            "synthesis_code": tool.code,
            "input_schema": tool.input_schema or {},
            "test_case_count": len(tool.test_cases),
        },
        inputs=[],
        outputs=[],
        tags=list(_TAGS),
        version="0.0.1",
        plugin_package=_PLUGIN_PACKAGE,
    )


def upload_synthesized_to_gallery(tool: SynthesizedTool, manifest_path: str) -> str:
    """검증 통과 도구를 로컬 매니페스트에 upsert.

    스키마는 `compile.local_manifest.LocalManifest` 단일 포맷 — Node Plugin 과 공유.
    synthesis 가 자기 스키마 박제하지 않는다 (feedback_no_hardcoding_extensibility).

    반환: 저장된 파일의 절대 경로.
    """
    from ..compile.local_manifest import upsert_node_in_file
    node = to_nom_node(tool)
    saved = upsert_node_in_file(node, manifest_path, manifest_name="synthesized-tools")
    logger.info("[synthesis] manifest upsert: %s -> %s", tool.name, saved)
    return saved


def load_synthesized_from_gallery(manifest_path: str) -> list[SynthesizedTool]:
    """로컬 매니페스트에서 SynthesizedTool 복원.

    `synthesis_code` 키를 보유한 TOOL 노드만 복원 — 다른 kind 는 무시.
    """
    from ..compile.local_manifest import load_manifest
    manifest = load_manifest(manifest_path)
    out: list[SynthesizedTool] = []
    for node in manifest.nodes:
        meta = node.kind_meta or {}
        code = meta.get("synthesis_code")
        if not code:
            continue
        out.append(SynthesizedTool(
            name=node.name or node.id,
            description=node.description,
            code=code,
            input_schema=meta.get("input_schema") or {},
        ))
    return out
