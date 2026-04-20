"""
컴파일러 단위/통합 테스트.

실행:
    cd xgen-harness-executor && python -m pytest test_compile.py -v

폐쇄망 친화: 모든 테스트는 외부 네트워크 없이 수행 (build --no-isolation).
"""

from __future__ import annotations

import json
import os
import sys
import subprocess
from pathlib import Path

import pytest

from xgen_harness import HarnessConfig
from xgen_harness.compile import (
    ExternalInputSpec,
    InputType,
    scan_placeholders,
    merge_scanned,
    collect_runtime_values,
    MissingExternalInputError,
    WorkflowSnapshot,
    SNAPSHOT_VERSION,
    resolve_dependencies,
    register_dependency_rule,
    compile_workflow,
)
from xgen_harness.compile.external_inputs import parse_declared


# ──────────────────────────────────────────────
# external_inputs
# ──────────────────────────────────────────────

class TestExternalInputs:
    def test_scan_basic_placeholder(self):
        payload = {"prompt": "hello ${OPENAI_API_KEY}"}
        found = scan_placeholders(payload)
        assert "OPENAI_API_KEY" in found
        # providers 레지스트리에 등록된 env 라서 secret 확정.
        assert found["OPENAI_API_KEY"].type == InputType.SECRET.value

    def test_scan_url_hint(self):
        payload = {"endpoint": "${MY_MCP_URL}"}
        found = scan_placeholders(payload)
        assert found["MY_MCP_URL"].type == InputType.URL.value

    def test_scan_with_default(self):
        payload = "use ${BUCKET_NAME:default-bucket}"
        found = scan_placeholders(payload)
        spec = found["BUCKET_NAME"]
        assert spec.default == "default-bucket"
        assert spec.required is False

    def test_merge_prefers_declared(self):
        declared = parse_declared({
            "OPENAI_API_KEY": {"type": "secret", "required": True, "description": "from user"}
        })
        scanned = scan_placeholders({"prompt": "${OPENAI_API_KEY}"})
        merged = merge_scanned(declared, scanned)
        assert merged["OPENAI_API_KEY"].description == "from user"

    def test_collect_runtime_values_resolves(self):
        specs = {
            "API_KEY": ExternalInputSpec(name="API_KEY", type="secret", required=True),
            "TOP_K": ExternalInputSpec(name="TOP_K", type="int", required=False, default="5"),
        }
        env = {"API_KEY": "sk-test"}
        resolved = collect_runtime_values(specs, env=env)
        assert resolved["API_KEY"] == "sk-test"
        assert resolved["TOP_K"] == "5"

    def test_collect_runtime_values_missing(self):
        specs = {"API_KEY": ExternalInputSpec(name="API_KEY", required=True)}
        with pytest.raises(MissingExternalInputError):
            collect_runtime_values(specs, env={})

    def test_overrides_beat_env(self):
        specs = {"X": ExternalInputSpec(name="X", required=True)}
        resolved = collect_runtime_values(specs, env={"X": "from_env"}, overrides={"X": "from_override"})
        assert resolved["X"] == "from_override"


# ──────────────────────────────────────────────
# WorkflowSnapshot
# ──────────────────────────────────────────────

class TestSnapshot:
    def test_roundtrip(self):
        config = HarnessConfig(provider="openai", temperature=0.5)
        snap = WorkflowSnapshot.from_config(
            harness_config=config,
            workflow_data={"nodes": [], "edges": []},
            gallery_name="test_bot",
            gallery_version="0.1.0",
        )
        text = snap.to_json()
        restored = WorkflowSnapshot.from_json(text)
        assert restored.gallery_name == "test_bot"
        assert restored.compile_version == SNAPSHOT_VERSION
        assert restored.harness_config["provider"] == "openai"

    def test_invalid_name_rejected(self):
        config = HarnessConfig()
        with pytest.raises(Exception):
            WorkflowSnapshot.from_config(
                harness_config=config,
                gallery_name="Bad Name!",  # 공백/대문자/느낌표
                gallery_version="0.1.0",
            )

    def test_auto_scan_populates_external_inputs(self):
        config = HarnessConfig(
            provider="anthropic",
            system_prompt="key=${ANTHROPIC_API_KEY}",
        )
        snap = WorkflowSnapshot.from_config(
            harness_config=config,
            gallery_name="scan_test",
        )
        assert "ANTHROPIC_API_KEY" in snap.external_inputs

    def test_declared_passes_through(self):
        config = HarnessConfig(
            external_inputs={
                "CUSTOM": {"type": "string", "required": False, "default": "x"},
            },
        )
        snap = WorkflowSnapshot.from_config(
            harness_config=config,
            gallery_name="decl_test",
        )
        assert snap.external_inputs["CUSTOM"]["default"] == "x"


# ──────────────────────────────────────────────
# Dependency resolver
# ──────────────────────────────────────────────

class TestDeps:
    def test_always_xgen_harness(self):
        snap = WorkflowSnapshot.from_config(
            harness_config=HarnessConfig(),
            gallery_name="dep_test",
        )
        deps = resolve_dependencies(snap)
        assert "xgen-harness" in deps

    def test_rag_rule(self):
        snap = WorkflowSnapshot.from_config(
            harness_config=HarnessConfig(),
            workflow_data={"rag_collections": ["docs"]},
            gallery_name="rag_test",
        )
        deps = resolve_dependencies(snap)
        assert "qdrant-client" in deps

    def test_custom_rule_registration(self):
        register_dependency_rule(
            "test_only_vendor",
            lambda s: [("vendor-pkg", ">=1.0")] if s.gallery_name == "vendor_test" else [],
            overwrite=True,
        )
        snap = WorkflowSnapshot.from_config(
            harness_config=HarnessConfig(),
            gallery_name="vendor_test",
        )
        deps = resolve_dependencies(snap)
        assert deps.get("vendor-pkg") == ">=1.0"


# ──────────────────────────────────────────────
# 엔드투엔드 — 실제 wheel 빌드
# ──────────────────────────────────────────────

@pytest.fixture
def tmp_dist(tmp_path):
    d = tmp_path / "dist"
    d.mkdir()
    return d


class TestWheelBuild:
    def test_build_minimal_wheel(self, tmp_dist):
        config = HarnessConfig(
            provider="openai",
            system_prompt="You are a helper. API key=${OPENAI_API_KEY}",
        )
        result = compile_workflow(
            harness_config=config,
            workflow_data={"nodes": [], "edges": []},
            gallery_name="mini",
            gallery_version="0.1.0",
            description="minimal compile test",
            out_dir=str(tmp_dist),
        )
        assert result.wheel_path.exists()
        assert result.wheel_path.suffix == ".whl"
        # 이름 규칙
        assert result.wheel_path.name.startswith("xgen_gallery_mini-0.1.0")
        # snapshot.json 이 wheel 내부에 포함돼야 함 — zip 검사
        import zipfile
        with zipfile.ZipFile(result.wheel_path) as zf:
            names = zf.namelist()
            assert any(n.endswith("snapshot.json") for n in names)
            assert any(n.endswith("env.example") for n in names)
            assert any(n.endswith("__init__.py") for n in names)
            # manifest 가 읽힐 수 있는 구조인지
            snap_name = next(n for n in names if n.endswith("snapshot.json"))
            data = json.loads(zf.read(snap_name))
            assert data["gallery_name"] == "mini"
            assert "OPENAI_API_KEY" in data["external_inputs"]

    def test_build_includes_deps(self, tmp_dist):
        config = HarnessConfig()
        result = compile_workflow(
            harness_config=config,
            workflow_data={"rag_collections": ["docs"]},
            gallery_name="with_rag",
            gallery_version="0.1.0",
            out_dir=str(tmp_dist),
            keep_source=True,
        )
        pyproject_text = (result.source_dir / "pyproject.toml").read_text()
        assert "xgen-harness" in pyproject_text
        assert "qdrant-client" in pyproject_text

    def test_requires_python_inherits_engine(self, tmp_dist):
        """compiled wheel 의 requires-python 은 엔진 자신의 스펙을 상속해야 함 (하드코딩 금지)."""
        config = HarnessConfig()
        result = compile_workflow(
            harness_config=config,
            gallery_name="req_python",
            out_dir=str(tmp_dist),
            keep_source=True,
        )
        pyproject_text = (result.source_dir / "pyproject.toml").read_text()
        # 엔진 자신의 requires-python 을 기본 상속 — 현재 >=3.10.
        assert 'requires-python = ">=3.10"' in pyproject_text

    def test_requires_python_override(self, tmp_dist):
        """사용자가 명시적으로 requires_python 을 덮을 수 있어야 함."""
        config = HarnessConfig()
        result = compile_workflow(
            harness_config=config,
            gallery_name="req_override",
            out_dir=str(tmp_dist),
            keep_source=True,
            requires_python=">=3.11",
        )
        pyproject_text = (result.source_dir / "pyproject.toml").read_text()
        assert 'requires-python = ">=3.11"' in pyproject_text


# ──────────────────────────────────────────────
# xgen-gallery (PlateerLab/xgen-gallery React 컴포넌트) 규약 통합
# ──────────────────────────────────────────────

class TestXgenGalleryConvention:
    def test_demo_json_and_examples_generated(self, tmp_dist):
        """기본값(include_gallery_hints=True) → .xgen-gallery/demo.json + examples/quickstart.py 생성."""
        config = HarnessConfig(provider="openai")
        result = compile_workflow(
            harness_config=config,
            gallery_name="hint_test",
            gallery_version="0.1.0",
            description="gallery hint test",
            out_dir=str(tmp_dist),
            keep_source=True,
        )

        demo_path = result.source_dir / ".xgen-gallery" / "demo.json"
        example_path = result.source_dir / "examples" / "quickstart.py"
        assert demo_path.exists()
        assert example_path.exists()

        demo = json.loads(demo_path.read_text())
        assert demo["name"] == "hint_test"
        assert demo["version"] == "0.1.0"
        snippet_labels = [s["label"] for s in demo["snippets"]]
        assert any("pip install" in s for s in snippet_labels)
        assert any("MCP" in s for s in snippet_labels)

        example_src = example_path.read_text()
        assert "from xgen_gallery_hint_test import arun" in example_src

    def test_gallery_hints_can_be_disabled(self, tmp_dist):
        """include_gallery_hints=False → 파일 생성 안 함."""
        config = HarnessConfig()
        result = compile_workflow(
            harness_config=config,
            gallery_name="no_hints",
            out_dir=str(tmp_dist),
            keep_source=True,
            include_gallery_hints=False,
        )
        assert not (result.source_dir / ".xgen-gallery").exists()
        assert not (result.source_dir / "examples").exists()


# ──────────────────────────────────────────────
# 설치/실행 — 실제 pip install 후 manifest 호출
# ──────────────────────────────────────────────

class TestInstalledWheel:
    """격리된 venv 에 wheel 설치 후 manifest() 호출 (오프라인 가능)."""

    def _make_venv_and_install(self, tmp_path: Path, wheel: Path) -> Path:
        venv_dir = tmp_path / "venv"
        subprocess.check_call([sys.executable, "-m", "venv", str(venv_dir)])
        bin_dir = venv_dir / ("Scripts" if os.name == "nt" else "bin")
        py = bin_dir / "python"

        # --no-build-isolation 을 쓰기 때문에 venv 안에 setuptools/wheel 이 필요.
        subprocess.check_call(
            [str(py), "-m", "pip", "install", "--disable-pip-version-check",
             "--quiet", "setuptools>=68", "wheel"],
            env={**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1"},
        )

        # 엔진 휠 (xgen-harness 현재 소스) 도 같이 설치해야 import 가능.
        engine_src = Path(__file__).resolve().parent
        subprocess.check_call(
            [str(py), "-m", "pip", "install", "--disable-pip-version-check",
             "--quiet", "--no-build-isolation",
             str(engine_src), str(wheel)],
            env={**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1"},
        )
        return py

    def test_install_and_manifest(self, tmp_path, tmp_dist):
        config = HarnessConfig(
            provider="openai",
            system_prompt="hello ${OPENAI_API_KEY}",
        )
        result = compile_workflow(
            harness_config=config,
            workflow_data={},
            gallery_name="installable",
            gallery_version="0.1.0",
            description="install test",
            out_dir=str(tmp_dist),
        )

        py = self._make_venv_and_install(tmp_path, result.wheel_path)

        script = """
import xgen_gallery_installable as g
m = g.manifest()
print(m['name'])
print(m['version'])
print('OPENAI_API_KEY' in m['external_inputs'])
print(m['dist_name'])
print(m['package_name'])
"""
        out = subprocess.check_output([str(py), "-c", script], text=True).strip().splitlines()
        assert out == ["installable", "0.1.0", "True", "xgen-gallery-installable", "xgen_gallery_installable"]


# ──────────────────────────────────────────────
# 단계 5 — MCP 서버 래퍼
# ──────────────────────────────────────────────

class TestMCPWrapper:
    def test_mcp_import_friendly_error(self):
        """mcp 미설치 상태에서 `_require_mcp` 는 친절한 에러."""
        from xgen_harness.compile.mcp_server import _require_mcp, MCPNotInstalledError
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *a, **kw):
            if name.startswith("mcp"):
                raise ImportError(f"mocked: no module {name}")
            return real_import(name, *a, **kw)

        builtins.__import__ = mock_import
        try:
            with pytest.raises(MCPNotInstalledError) as ei:
                _require_mcp()
            assert "mcp" in str(ei.value).lower()
        finally:
            builtins.__import__ = real_import

    def test_cli_has_serve_mcp(self, tmp_dist):
        """컴파일된 wheel 의 cli.py 에 serve-mcp 서브커맨드 존재."""
        config = HarnessConfig(provider="openai")
        result = compile_workflow(
            harness_config=config,
            workflow_data={},
            gallery_name="mcp_test",
            out_dir=str(tmp_dist),
            keep_source=True,
        )
        cli_src = (result.source_dir / result.package_name / "cli.py").read_text()
        assert "serve-mcp" in cli_src
        assert "run_blocking" in cli_src

    def test_pyproject_has_mcp_extra(self, tmp_dist):
        """컴파일된 wheel 의 pyproject.toml 에 [project.optional-dependencies] mcp 섹션."""
        config = HarnessConfig()
        result = compile_workflow(
            harness_config=config,
            gallery_name="mcp_extra",
            out_dir=str(tmp_dist),
            keep_source=True,
        )
        py_text = (result.source_dir / "pyproject.toml").read_text()
        assert "[project.optional-dependencies]" in py_text
        assert "mcp = [" in py_text
        assert "mcp>=0.9" in py_text


# ──────────────────────────────────────────────
# 단계 6 — 갤러리 discover
# ──────────────────────────────────────────────

class TestGalleryDiscover:
    def test_discover_empty_when_nothing_installed(self):
        """현재 env 에 설치된 갤러리가 없으면 빈 리스트 (혹은 현재 설치 품목). 로드 실패 시 skip."""
        from xgen_harness.compile.gallery import discover_galleries, ENTRY_POINT_GROUP
        assert ENTRY_POINT_GROUP == "xgen_harness.galleries"
        # 이 테스트 env 에는 설치된 갤러리가 없을 수 있고, 있을 수도 있음 (다른 test 가 먼저 venv 설치했으면).
        # 핵심 계약은: "호출이 성공하고 리스트를 반환" — 타입 계약.
        result = discover_galleries()
        assert isinstance(result, list)
        for g in result:
            assert g.entry_point_name
            assert isinstance(g.manifest, dict)

    def test_discover_finds_installed_gallery(self, tmp_path, tmp_dist):
        """실제 wheel 빌드 → 격리 venv 설치 → 그 venv 안에서 discover 호출."""
        config = HarnessConfig(provider="openai")
        result = compile_workflow(
            harness_config=config,
            workflow_data={},
            gallery_name="discoverable",
            gallery_version="0.1.0",
            description="discover test",
            out_dir=str(tmp_dist),
        )

        venv_dir = tmp_path / "venv"
        subprocess.check_call([sys.executable, "-m", "venv", str(venv_dir)])
        bin_dir = venv_dir / ("Scripts" if os.name == "nt" else "bin")
        py = bin_dir / "python"
        subprocess.check_call(
            [str(py), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
             "setuptools>=68", "wheel"],
        )
        subprocess.check_call(
            [str(py), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
             "--no-build-isolation",
             str(Path(__file__).resolve().parent), str(result.wheel_path)],
        )
        script = """
from xgen_harness.compile.gallery import discover_galleries
galleries = discover_galleries()
names = [g.entry_point_name for g in galleries]
print('discoverable' in names)
for g in galleries:
    if g.entry_point_name == 'discoverable':
        print(g.manifest.get('name'))
        print(g.manifest.get('version'))
        print(g.dist_name)
        print(g.package_name)
"""
        out = subprocess.check_output([str(py), "-c", script], text=True).strip().splitlines()
        assert out[0] == "True"
        assert out[1] == "discoverable"
        assert out[2] == "0.1.0"
        assert out[3] == "xgen-gallery-discoverable"
        assert out[4] == "xgen_gallery_discoverable"
