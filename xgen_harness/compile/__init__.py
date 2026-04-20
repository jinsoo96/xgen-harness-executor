"""
xgen_harness.compile — 하네스 워크플로우 컴파일러

하네스 워크플로우를 `xgen.compile(wf)` 한 줄로 실행 가능한 배포 아티팩트
(PyPI wheel / MCP stdio 서버) 로 변환한다.

기본 사용:
    from xgen_harness.compile import compile_workflow

    artifact = compile_workflow(
        harness_config=config,
        workflow_data=wf_data,
        gallery_name="my_agent",
        gallery_version="0.1.0",
        out_dir="./dist",
    )

설계 문서: docs/harness/2026-04-20-workflow-compiler.md
"""

from .external_inputs import (
    ExternalInputSpec,
    InputType,
    scan_placeholders,
    merge_scanned,
    validate_external_inputs,
    collect_runtime_values,
    MissingExternalInputError,
)
from .snapshot import (
    WorkflowSnapshot,
    SNAPSHOT_VERSION,
    load_snapshot,
)
from .deps import (
    DependencyResolver,
    resolve_dependencies,
    register_dependency_rule,
    DependencyRule,
)
from .wheel import (
    WheelBuildResult,
    build_wheel,
    compile_workflow,
)

__all__ = [
    # external_inputs
    "ExternalInputSpec",
    "InputType",
    "scan_placeholders",
    "merge_scanned",
    "validate_external_inputs",
    "collect_runtime_values",
    "MissingExternalInputError",
    # snapshot
    "WorkflowSnapshot",
    "SNAPSHOT_VERSION",
    "load_snapshot",
    # deps
    "DependencyResolver",
    "resolve_dependencies",
    "register_dependency_rule",
    "DependencyRule",
    # wheel
    "WheelBuildResult",
    "build_wheel",
    "compile_workflow",
]
