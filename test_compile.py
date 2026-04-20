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
"""
        out = subprocess.check_output([str(py), "-c", script], text=True).strip().splitlines()
        assert out == ["installable", "0.1.0", "True"]
