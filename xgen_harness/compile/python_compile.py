"""Python 패키지 컴파일러 (v1.10.0).

HarnessConfig dict → pip install 가능한 PyPI 패키지 트리 생성.

산출물 구조:
    {package_dir}-{version}/
    ├── pyproject.toml                ← exact xgen-harness pin (1:1 미러)
    ├── README.md                     ← 워크플로우 description
    ├── {package_module}/
    │   ├── __init__.py               ← build_pipeline export
    │   ├── flow.py                   ← CLUSTER_DEFAULTS dict + Pipeline 구축
    │   ├── mcp.py                    ← FastMCP 서버 entry (옵션)
    │   └── config.toml.example       ← 사용자 override 템플릿

사용자가 받은 후:
    pip install plateer-xgen-wf-abc-0.1.0    # PyPI publish 산출물
    또는
    pip install ./plateer-xgen-wf-abc-0.1.0  # 로컬 디렉토리

    export OPENAI_API_KEY=sk-...
    export QDRANT_URL=http://localhost:6333
    python -m plateer_xgen_wf_abc "안녕"     # CLI entry
    또는
    python -m plateer_xgen_wf_abc.mcp        # FastMCP stdio
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from .snapshot import WorkflowSnapshot

logger = logging.getLogger("harness.compile.python")


# ─────────────────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────────────────


def transpile_to_python(
    snapshot: WorkflowSnapshot | dict,
    *,
    package_name: str,
    package_version: str = "0.1.0",
    include_mcp: bool = True,
    harness_version_pin: Optional[str] = None,
    workflow_description: str = "",
) -> dict[str, str]:
    """HarnessConfig 스냅샷 → 패키지 파일 트리.

    Args:
        snapshot: WorkflowSnapshot 인스턴스 또는 dict (`harness_config` 키 필수).
        package_name: PyPI 패키지명 (예: "plateer-xgen-wf-abc"). 모듈명은 자동 sanitize.
        package_version: SemVer (예: "0.1.0").
        include_mcp: FastMCP 서버 entry 포함 여부.
        harness_version_pin: xgen-harness deps 핀 (예: "==1.10.0").
            None 이면 현재 설치된 xgen-harness 버전을 exact pin 으로 박음 (1:1 미러).
        workflow_description: README 에 박힐 워크플로우 설명.

    Returns:
        `{relative_path: file_content}` dict — write_package 으로 디스크 박음.
    """
    if isinstance(snapshot, WorkflowSnapshot):
        snap_dict = snapshot.to_dict()
        cluster_defaults = dict(snap_dict.get("harness_config") or {})
    elif isinstance(snapshot, dict):
        if "harness_config" in snapshot:
            cluster_defaults = dict(snapshot["harness_config"])
        else:
            # bare HarnessConfig dict 형태로 받은 케이스
            cluster_defaults = dict(snapshot)
        snap_dict = {"harness_config": cluster_defaults}
    else:
        raise TypeError(f"snapshot must be WorkflowSnapshot or dict, got {type(snapshot).__name__}")

    module_name = _sanitize_module_name(package_name)
    if harness_version_pin is None:
        harness_version_pin = _current_harness_pin()

    files: dict[str, str] = {}
    files["pyproject.toml"] = _render_pyproject(
        package_name=package_name,
        package_version=package_version,
        module_name=module_name,
        harness_version_pin=harness_version_pin,
        include_mcp=include_mcp,
        description=workflow_description or f"XGEN Harness workflow — {package_name}",
    )
    files["README.md"] = _render_readme(
        package_name=package_name,
        module_name=module_name,
        description=workflow_description,
        include_mcp=include_mcp,
    )
    files[f"{module_name}/__init__.py"] = _render_init(module_name=module_name)
    files[f"{module_name}/flow.py"] = _render_flow(
        cluster_defaults=cluster_defaults,
        module_name=module_name,
    )
    files[f"{module_name}/__main__.py"] = _render_main(module_name=module_name)
    files[f"{module_name}/config.toml.example"] = _render_config_example(cluster_defaults)
    if include_mcp:
        files[f"{module_name}/mcp.py"] = _render_mcp(
            package_name=package_name,
            module_name=module_name,
        )

    return files


def write_package(tree: dict[str, str], out_dir: str | Path) -> Path:
    """transpile 결과 dict 를 디스크에 박음."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for rel_path, content in tree.items():
        target = out / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return out


# ─────────────────────────────────────────────────────────
# Sanitize / version
# ─────────────────────────────────────────────────────────


def _sanitize_module_name(package_name: str) -> str:
    """PyPI 패키지명 (예: "plateer-xgen-wf-abc") → Python 모듈명 ("plateer_xgen_wf_abc")."""
    name = re.sub(r"[^A-Za-z0-9_]", "_", package_name)
    # 숫자로 시작하면 underscore prefix
    if name and name[0].isdigit():
        name = "_" + name
    name = name.lower().strip("_")
    return name or "workflow"


def _current_harness_pin() -> str:
    """현재 설치된 xgen-harness 버전을 exact pin 으로 반환 (예: "==1.10.0")."""
    try:
        from .. import __version__ as v
        return f"=={v}"
    except Exception:
        return ">=1.10.0,<2.0"


# ─────────────────────────────────────────────────────────
# Templates — stdlib repr 기반 (Jinja 불필요)
# ─────────────────────────────────────────────────────────


def _render_pyproject(
    *,
    package_name: str,
    package_version: str,
    module_name: str,
    harness_version_pin: str,
    include_mcp: bool,
    description: str,
) -> str:
    # toml 직접 작성 — tomli_w 같은 신규 deps 회피
    extras_block = ""
    if include_mcp:
        extras_block = '\n[project.optional-dependencies]\nmcp = ["fastmcp>=0.2.0"]\n'

    scripts_block = (
        f'[project.scripts]\n'
        f'{module_name} = "{module_name}.__main__:cli_main"\n'
    )
    if include_mcp:
        scripts_block += f'{module_name}-mcp = "{module_name}.mcp:main"\n'

    safe_description = description.replace('"', '\\"').replace("\n", " ").strip()

    return (
        f'[project]\n'
        f'name = "{package_name}"\n'
        f'version = "{package_version}"\n'
        f'description = "{safe_description}"\n'
        f'requires-python = ">=3.10"\n'
        f'dependencies = [\n'
        f'    "xgen-harness{harness_version_pin}",\n'
        f'    "httpx>=0.27",\n'
        f']\n'
        f'{extras_block}\n'
        f'{scripts_block}\n'
        f'[build-system]\n'
        f'requires = ["setuptools>=69.0"]\n'
        f'build-backend = "setuptools.build_meta"\n'
        f'\n'
        f'[tool.setuptools.packages.find]\n'
        f'include = ["{module_name}*"]\n'
        f'\n'
        f'[tool.setuptools.package-data]\n'
        f'"{module_name}" = ["*.example"]\n'
    )


def _render_readme(
    *,
    package_name: str,
    module_name: str,
    description: str,
    include_mcp: bool,
) -> str:
    desc = description.strip() or "xgen 에서 말려나온 하네스 워크플로우 패키지."
    mcp_section = ""
    if include_mcp:
        mcp_section = (
            "\n## MCP 서버 모드 (Claude Code / Desktop / Cursor)\n\n"
            "```bash\n"
            f"claude mcp add my-wf -- python -m {module_name}.mcp\n"
            "```\n"
        )

    return (
        f"# {package_name}\n\n"
        f"{desc}\n\n"
        "## 설치\n\n"
        "```bash\n"
        f"pip install {package_name}"
        + (f"[mcp]" if include_mcp else "")
        + "\n```\n\n"
        "## 사용\n\n"
        "### CLI\n\n"
        "```bash\n"
        "export OPENAI_API_KEY=sk-...\n"
        "export QDRANT_URL=http://localhost:6333\n"
        f"{module_name} \"안녕\"\n"
        "```\n\n"
        "### Python 라이브러리\n\n"
        "```python\n"
        f"from {module_name} import build_pipeline\n"
        "from xgen_harness import PipelineState\n"
        "import asyncio\n\n"
        "async def main():\n"
        "    pipeline = build_pipeline()\n"
        "    state = PipelineState(user_input=\"안녕\")\n"
        "    result = await pipeline.run(state)\n"
        "    print(result.final_text)\n\n"
        "asyncio.run(main())\n"
        "```\n"
        f"{mcp_section}"
        "## 외부 wire — 설정 override\n\n"
        "환경값은 5 단계 resolution chain (위가 우선):\n\n"
        "1. 코드 인자 (`HarnessConfig(...)`)\n"
        "2. ENV (`XGEN_HARNESS_*` prefix + `__` nested)\n"
        "3. Config file (`./xgen-harness.toml`)\n"
        "4. Cluster origin default (transpile 시 박힌 `CLUSTER_DEFAULTS`)\n"
        "5. SDK builtin default\n\n"
        "예시:\n"
        "```bash\n"
        "export XGEN_HARNESS_STAGE_PARAMS__S06_CONTEXT__TOP_K=20\n"
        "export XGEN_HARNESS_RUNTIME_DEFAULTS__SYNTH_SUB_MAX_TURNS=12\n"
        "```\n"
    )


def _render_init(*, module_name: str) -> str:
    return (
        f'"""\n'
        f'{module_name} — xgen 에서 말려나온 하네스 워크플로우 패키지.\n'
        f'\n'
        f'사용:\n'
        f'    from {module_name} import build_pipeline\n'
        f'    pipeline = build_pipeline()\n'
        f'"""\n\n'
        f'from .flow import build_pipeline, CLUSTER_DEFAULTS\n\n'
        f'__all__ = ["build_pipeline", "CLUSTER_DEFAULTS"]\n'
    )


def _render_flow(*, cluster_defaults: dict, module_name: str) -> str:
    # cluster_defaults 가 set 같은 비-JSON 객체 가질 수 있음 — 안전 변환
    safe_defaults = _coerce_json_safe(cluster_defaults)
    # repr / json 으로 어느 게 좋은가:
    #   json.dumps + indent → 깔끔 + IDE 친화
    #   다만 Python literal 형태 필요 (True/False/None, single quote 가능)
    #   → json.dumps 결과를 Python literal 로 변환
    defaults_literal = _dict_to_python_literal(safe_defaults, indent=4)

    return (
        '"""\n'
        'flow.py — cluster origin 환경값 + Pipeline 구축\n\n'
        'CLUSTER_DEFAULTS dict 가 cluster UI 에서 사용자가 선택한 값들.\n'
        'IDE 에서 직접 편집 가능. 또는 env / toml 로 외부 override.\n'
        '"""\n\n'
        'import os\n'
        'from pathlib import Path\n\n'
        'from xgen_harness import HarnessConfig, Pipeline, PipelineState\n'
        'from xgen_harness.config import DictConfigSource, EnvConfigSource, FileConfigSource\n'
        'from xgen_harness.adapters import create_provider\n\n\n'
        '# === Cluster origin default — cluster UI 에서 사용자가 선택한 환경값 ===\n'
        '# IDE 에서 직접 편집 가능. env / toml 로 외부 override 도 가능.\n'
        f'CLUSTER_DEFAULTS = {defaults_literal}\n\n\n'
        'def build_pipeline() -> Pipeline:\n'
        '    """Pipeline 인스턴스 생성. 5 단계 resolution + 외부 인프라 wire."""\n'
        '    config_toml = Path("./xgen-harness.toml")\n'
        '    config = HarnessConfig.resolve(sources=[\n'
        '        EnvConfigSource(prefix="XGEN_HARNESS_"),\n'
        '        FileConfigSource(config_toml) if config_toml.exists() else None,\n'
        '        DictConfigSource(CLUSTER_DEFAULTS),\n'
        '    ])\n\n'
        '    # === 외부 인프라 wire — env 변수에서 받음 ===\n'
        '    provider = create_provider(\n'
        '        os.environ.get("XGEN_HARNESS_PROVIDER", config.provider or "openai"),\n'
        '        api_key=_resolve_api_key(config.provider or "openai"),\n'
        '        model=config.model or None,\n'
        '    )\n\n'
        '    doc_service = _build_doc_service()\n\n'
        '    return Pipeline.from_config(\n'
        '        config,\n'
        '        doc_service=doc_service,\n'
        '        provider=provider,\n'
        '    )\n\n\n'
        'def _resolve_api_key(provider_name: str) -> str:\n'
        '    """provider 이름 → API key env 변수 lookup."""\n'
        '    env_map = {\n'
        '        "openai": "OPENAI_API_KEY",\n'
        '        "anthropic": "ANTHROPIC_API_KEY",\n'
        '        "google": "GEMINI_API_KEY",\n'
        '        "vllm": "VLLM_API_KEY",\n'
        '    }\n'
        '    env_key = env_map.get(provider_name, f"{provider_name.upper()}_API_KEY")\n'
        '    return os.environ.get(env_key, "")\n\n\n'
        'def _build_doc_service():\n'
        '    """Qdrant URL 박혀있으면 QdrantDocService, 아니면 None."""\n'
        '    qdrant_url = os.environ.get("QDRANT_URL")\n'
        '    if not qdrant_url:\n'
        '        return None\n'
        '    from xgen_harness.adapters import QdrantDocService\n'
        '    return QdrantDocService(\n'
        '        url=qdrant_url,\n'
        '        api_key=os.environ.get("QDRANT_API_KEY"),\n'
        '    )\n'
    )


def _render_main(*, module_name: str) -> str:
    return (
        '"""__main__ — CLI entry point.\n\n'
        f'사용: python -m {module_name} "사용자 입력"\n'
        '"""\n\n'
        'import asyncio\n'
        'import sys\n\n'
        'from xgen_harness import PipelineState\n\n'
        'from .flow import build_pipeline\n\n\n'
        'async def _run(user_input: str) -> str:\n'
        '    pipeline = build_pipeline()\n'
        '    state = PipelineState(user_input=user_input)\n'
        '    result = await pipeline.run(state)\n'
        '    return getattr(result, "final_text", "") or ""\n\n\n'
        'def cli_main() -> None:\n'
        '    user_input = sys.argv[1] if len(sys.argv) > 1 else ""\n'
        '    if not user_input:\n'
        '        print("Usage: python -m " + __package__ + " \\"사용자 입력\\"", file=sys.stderr)\n'
        '        sys.exit(1)\n'
        '    output = asyncio.run(_run(user_input))\n'
        '    print(output)\n\n\n'
        'if __name__ == "__main__":\n'
        '    cli_main()\n'
    )


def _render_mcp(*, package_name: str, module_name: str) -> str:
    return (
        '"""MCP 서버 entry — FastMCP stdio.\n\n'
        '사용:\n'
        f'    claude mcp add my-wf -- python -m {module_name}.mcp\n'
        '\n'
        'FastMCP 의존성:\n'
        f'    pip install {package_name}[mcp]\n'
        '"""\n\n'
        'from xgen_harness import PipelineState\n\n'
        'from .flow import build_pipeline\n\n\n'
        'def _build_server():\n'
        '    """FastMCP 서버 인스턴스 구축 — 도구 1 개 등록."""\n'
        '    try:\n'
        '        from fastmcp import FastMCP\n'
        '    except ImportError as e:\n'
        '        raise ImportError(\n'
        f'            "FastMCP 미설치. `pip install {package_name}[mcp]` 또는 `pip install fastmcp` 박으세요."\n'
        '        ) from e\n\n'
        f'    mcp = FastMCP("{package_name}")\n\n'
        '    @mcp.tool\n'
        '    async def run_workflow(user_input: str) -> str:\n'
        '        """xgen 워크플로우 실행. user_input 받아 final_text 반환."""\n'
        '        pipeline = build_pipeline()\n'
        '        state = PipelineState(user_input=user_input)\n'
        '        result = await pipeline.run(state)\n'
        '        return getattr(result, "final_text", "") or ""\n\n'
        '    return mcp\n\n\n'
        'def main() -> None:\n'
        '    server = _build_server()\n'
        '    server.run()\n\n\n'
        'if __name__ == "__main__":\n'
        '    main()\n'
    )


def _render_config_example(cluster_defaults: dict) -> str:
    """xgen-harness.toml 예시 — 사용자가 override 할 수 있는 키 보여줌."""
    stage_params = cluster_defaults.get("stage_params") or {}
    runtime_defaults = cluster_defaults.get("runtime_defaults") or {}

    lines: list[str] = [
        "# xgen-harness.toml — 외부 override 예시",
        "# 5 단계 resolution chain 의 3 번째 우선순위 (env > toml > cluster origin > sdk default)",
        "# 이 파일이 존재하면 자동 로드. 없으면 cluster origin 그대로 사용.",
        "",
    ]

    if stage_params:
        for stage_id, params in stage_params.items():
            if not isinstance(params, dict) or not params:
                continue
            lines.append(f"[stage_params.{stage_id}]")
            for key, value in params.items():
                lines.append(f"# {key} = {_toml_repr(value)}")
            lines.append("")

    if runtime_defaults:
        lines.append("[runtime_defaults]")
        for key, value in runtime_defaults.items():
            lines.append(f"# {key} = {_toml_repr(value)}")
        lines.append("")

    lines.extend([
        "# 외부 인프라 wire 는 env 변수 사용:",
        "#   QDRANT_URL=http://localhost:6333",
        "#   QDRANT_API_KEY=...",
        "#   OPENAI_API_KEY=sk-...",
        "",
    ])
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────
# JSON-safe + literal 변환
# ─────────────────────────────────────────────────────────


def _coerce_json_safe(obj: Any) -> Any:
    """set / tuple / 기타 비-JSON 객체 → JSON-safe 변환."""
    if isinstance(obj, set):
        return sorted(obj) if all(isinstance(x, str) for x in obj) else list(obj)
    if isinstance(obj, tuple):
        return [_coerce_json_safe(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _coerce_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_coerce_json_safe(x) for x in obj]
    return obj


def _dict_to_python_literal(obj: Any, *, indent: int = 4) -> str:
    """JSON-safe dict → Python literal string (json.dumps 후 Python 화).

    JSON: null/true/false/double quote
    Python: None/True/False/double quote (json.dumps 호환)
    → 단순 치환으로 충분.
    """
    text = json.dumps(obj, ensure_ascii=False, indent=indent, sort_keys=False)
    text = text.replace(": null", ": None")
    text = text.replace(": true", ": True")
    text = text.replace(": false", ": False")
    # 배열 안의 null/true/false (예: [null, true]) 도 변환
    text = re.sub(r"(?<=[\[,\s])null(?=[\],\s])", "None", text)
    text = re.sub(r"(?<=[\[,\s])true(?=[\],\s])", "True", text)
    text = re.sub(r"(?<=[\[,\s])false(?=[\],\s])", "False", text)
    return text


def _toml_repr(value: Any) -> str:
    """toml literal repr — 사용자에게 보이는 예시 값."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, default=str)
