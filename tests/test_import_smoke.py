"""Import smoke — v0.22.0 류 stale import 재발 방지.

한 줄짜리 import 파손도 PyPI 에 흘러가지 않도록 CI 에서 이 파일만 통과하면 된다.
v0.22.0 main 커밋 4b65831 이 `adapters/xgen.py` 를 지우고
`adapters/__init__.py:11` 의 `from .xgen import ...` 는 안 지워서 `import xgen_harness`
자체가 죽었던 사고가 직접적 동기.
"""

import importlib


def test_top_level_import() -> None:
    """`import xgen_harness` 가 예외 없이 성공."""
    mod = importlib.import_module("xgen_harness")
    assert hasattr(mod, "__version__")


def test_public_surface_exists() -> None:
    """README / docs 가 약속한 최상위 export 가 실제로 있는지."""
    import xgen_harness as xh

    expected = {
        # compile
        "compile", "compile_workflow", "compile_nom_graph", "WheelBuildResult",
        # NOM IR (v0.21)
        "NOMGraph", "NOMNode", "NOMKind", "NOMParam", "NOMOutput",
        "snapshot_current_registry_as_nom",
        # Planner
        "HarnessPlan", "HarnessPlanner",
        # Tool source
        "ToolSource", "register_tool_source", "get_tool_sources",
        # Sandbox (v0.20)
        "MCPStdioVerifier", "verify_mcp_stdio",
        # Stage basics
        "ALL_STAGES", "REQUIRED_STAGES", "Stage", "register_stage",
        # Service layer (두 주입 체계)
        "ServiceProvider", "NullServiceProvider",
    }
    missing = {name for name in expected if not hasattr(xh, name)}
    assert not missing, f"missing exports: {sorted(missing)}"


def test_submodule_imports() -> None:
    """주요 서브모듈이 따로 import 해도 깨지지 않음."""
    for name in [
        "xgen_harness.adapters",
        "xgen_harness.api.router",
        "xgen_harness.capabilities",
        "xgen_harness.core.nom",
        "xgen_harness.core.planner",
        "xgen_harness.core.pipeline",
        "xgen_harness.core.registry",
        "xgen_harness.core.service_registry",
        "xgen_harness.providers",
        "xgen_harness.stages.strategies.guard",
        "xgen_harness.tools",
        "xgen_harness.tools.base",
    ]:
        importlib.import_module(name)


def test_stage_registry_shape() -> None:
    """registry.list_stages() 는 13, ALL_STAGES 는 11, REQUIRED 는 3."""
    from xgen_harness.core.config import ALL_STAGES, REQUIRED_STAGES
    from xgen_harness.core.registry import _get_default_registry

    reg = _get_default_registry()
    stages = reg.list_stages()

    assert len(stages) == 13, f"expected 13 registered stages, got {len(stages)}: {sorted(stages)}"
    assert len(ALL_STAGES) == 11, f"expected 11 in ALL_STAGES, got {len(ALL_STAGES)}"
    assert REQUIRED_STAGES == {"s01_input", "s09_decide", "s11_finalize"}


def test_harness_router_routes() -> None:
    """fastapi optional — 설치돼 있으면 4 routes 확인."""
    try:
        from xgen_harness.api.router import harness_router
    except ImportError:
        return  # fastapi 미설치 환경에선 skip
    paths = {r.path for r in harness_router.routes}
    assert paths >= {"/stages", "/execute", "/orchestrate"}, f"router routes: {paths}"
