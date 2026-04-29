"""
xgen_harness.compile — 하네스 워크플로우 컴파일러

v0.28+ 부터 **npm package 가 1차 컴파일 산출물**. MCP 표준 생태계 (Claude Desktop /
Cursor / mcp-station server_type=node) 가 npm/npx 기반이므로 우리도 같은 채널을
1급 시민으로 사용. Python wheel 채널은 deprecated.

기본 사용:
    from xgen_harness.compile import compile_workflow_to_npm

    artifact = compile_workflow_to_npm(
        harness_config=config,
        workflow_data=wf_data,
        gallery_name="my_agent",
        gallery_version="0.1.0",
        out_dir="./dist",
    )
    # → dist/xgen-harness-my_agent-0.1.0.tgz (npm tarball)
    #   안에는 spec.json + bin/cli.js + package.json
    #   외부에서 `npx -y xgen-harness-my_agent serve-mcp` 즉시 실행 가능

레거시 (deprecated, 마이그레이션 후 제거 예정):
    from xgen_harness.compile import build_wheel  # Python wheel — 외부 환경 셋업 부담
    from xgen_harness.compile import serve_mcp    # Python wheel 안의 stdio MCP wrapper

설계 문서:
  docs/harness/2026-04-20-workflow-compiler.md (legacy wheel)
  docs/harness/2026-04-30-npm-compile.md (v0.28 신설)
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
# v0.28 신규 — npm 채널이 default
from .npm_spec import build_spec, HarnessSpec, SPEC_VERSION
from .npm_pack import (
    NpmPackResult,
    build_npm_package,
    compile_workflow_to_npm,
    NPM_PACKAGE_PREFIX,
)
from .nom_compile import compile_nom_graph
from .gallery import (
    InstalledGallery,
    discover_galleries,
    get_gallery,
    ENTRY_POINT_GROUP,
)
# 레거시 wheel 채널 — deprecated. 새 publish 는 npm 사용. 마이그레이션 후 v0.30 제거.
from .wheel import (
    WheelBuildResult,
    build_wheel,
    compile_workflow,
    GALLERY_DIST_PREFIX,
    GALLERY_PKG_PREFIX,
)
from .mcp_server import (
    serve as serve_mcp,
    run_blocking as run_mcp_blocking,
    MCPNotInstalledError,
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
    # v0.28+ npm 채널 (1차)
    "build_spec",
    "HarnessSpec",
    "SPEC_VERSION",
    "NpmPackResult",
    "build_npm_package",
    "compile_workflow_to_npm",
    "NPM_PACKAGE_PREFIX",
    # NOM (v0.21.0 Phase C)
    "compile_nom_graph",
    # gallery
    "InstalledGallery",
    "discover_galleries",
    "get_gallery",
    "ENTRY_POINT_GROUP",
    # 레거시 wheel — deprecated
    "WheelBuildResult",
    "build_wheel",
    "compile_workflow",
    "GALLERY_DIST_PREFIX",
    "GALLERY_PKG_PREFIX",
    "serve_mcp",
    "run_mcp_blocking",
    "MCPNotInstalledError",
]
