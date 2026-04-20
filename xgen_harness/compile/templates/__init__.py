"""
Wheel templates — 순수 Python 문자열 템플릿 (Jinja 의존 제거).

각 상수는 ``str.format()`` 호환 placeholder 를 사용. `{` `}` 리터럴이 필요할 때는
``{{`` / ``}}`` 로 escape 되어 있음.
"""

# ──────────────────────────────────────────────────────────────
# pyproject.toml
# ──────────────────────────────────────────────────────────────

PYPROJECT_TOML = """\
[project]
name = "{dist_name}"
version = "{gallery_version}"
description = {description_toml}
requires-python = ">=3.10"
readme = "README.md"
dependencies = [
{dependencies_block}
]

[project.scripts]
{cli_name} = "{package_name}.cli:main"

[project.entry-points."xgen_harness.galleries"]
{entry_point_name} = "{package_name}:manifest"

[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["{package_name}*"]

[tool.setuptools.package-data]
"{package_name}" = ["snapshot.json", "env.example"]
"""


# ──────────────────────────────────────────────────────────────
# xgen_gallery_<name>/__init__.py
# ──────────────────────────────────────────────────────────────

PACKAGE_INIT = '''\
"""
{gallery_name} — xgen-harness 컴파일 산출물.

자동 생성됨. 수정 금지 (snapshot.json 이 결정적 진실 소스).
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Optional

from xgen_harness.core.config import HarnessConfig
from xgen_harness.core.pipeline import Pipeline
from xgen_harness.core.state import PipelineState
from xgen_harness.compile.snapshot import load_snapshot
from xgen_harness.compile.external_inputs import (
    parse_declared,
    collect_runtime_values,
    MissingExternalInputError,
)

__all__ = ["arun", "run", "manifest", "SNAPSHOT_PATH", "GALLERY_NAME", "GALLERY_VERSION"]

_PKG_DIR = Path(__file__).resolve().parent
SNAPSHOT_PATH = _PKG_DIR / "snapshot.json"

_snapshot = load_snapshot(str(SNAPSHOT_PATH))

GALLERY_NAME = _snapshot.gallery_name
GALLERY_VERSION = _snapshot.gallery_version


def _apply_external_inputs(overrides: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    specs = parse_declared(_snapshot.external_inputs or {{}})
    resolved = collect_runtime_values(specs, overrides=overrides)
    for name, value in resolved.items():
        if value is None:
            continue
        os.environ.setdefault(name, str(value))
    return resolved


async def arun(user_input: str, *, overrides: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """컴파일된 하네스 워크플로우를 1회 실행.

    Args:
        user_input: 사용자 입력.
        overrides: external_inputs 런타임 덮어쓰기 (env 보다 우선).

    Returns:
        {{"final_output": str, "usage": dict, "iterations": int, "messages": list}}
    """
    _apply_external_inputs(overrides)

    config = HarnessConfig.from_dict(_snapshot.harness_config)
    pipeline = Pipeline.from_config(config)
    state = PipelineState(user_input=user_input)
    result_state = await pipeline.run(state)

    usage = {{}}
    if getattr(result_state, "token_usage", None) is not None:
        tu = result_state.token_usage
        usage = {{
            "input_tokens": getattr(tu, "input_tokens", 0),
            "output_tokens": getattr(tu, "output_tokens", 0),
            "total_tokens": getattr(tu, "total_tokens", 0),
        }}

    return {{
        "final_output": getattr(result_state, "final_output", "") or "",
        "iterations": getattr(result_state, "loop_iteration", 0),
        "usage": usage,
        "messages": [
            {{"role": m.get("role"), "content": m.get("content")}}
            for m in getattr(result_state, "messages", []) or []
            if isinstance(m, dict)
        ],
    }}


def run(user_input: str, *, overrides: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """동기 호출 래퍼 — 스크립트 사용 편의."""
    return asyncio.run(arun(user_input, overrides=overrides))


def manifest() -> dict[str, Any]:
    """entry_point 로 노출되는 메타. UI 가 설치된 갤러리 카드 렌더."""
    return {{
        "name": GALLERY_NAME,
        "version": GALLERY_VERSION,
        "harness_version": _snapshot.harness_version,
        "description": _snapshot.metadata.get("description", ""),
        "external_inputs": _snapshot.external_inputs,
        "created_at": _snapshot.metadata.get("created_at_iso", ""),
    }}
'''


# ──────────────────────────────────────────────────────────────
# xgen_gallery_<name>/cli.py
# ──────────────────────────────────────────────────────────────

CLI_PY = '''\
"""CLI 엔트리 — `{cli_name}` 명령어."""

import argparse
import asyncio
import json
import sys

from . import arun, GALLERY_NAME, GALLERY_VERSION, manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="{cli_name}",
                                     description=f"{{GALLERY_NAME}} v{{GALLERY_VERSION}}")
    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="실행 (인풋은 --input 또는 stdin)")
    p_run.add_argument("--input", "-i", default=None, help="사용자 입력")
    p_run.add_argument("--json", action="store_true", help="결과를 JSON 으로 출력")
    p_run.add_argument("--override", "-o", action="append", default=[],
                       help="external_inputs 덮어쓰기 (KEY=VALUE)")

    sub.add_parser("info", help="갤러리 메타 출력")

    args = parser.parse_args(argv)

    if args.cmd == "info" or args.cmd is None:
        print(json.dumps(manifest(), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "run":
        user_input = args.input if args.input is not None else sys.stdin.read().strip()
        if not user_input:
            parser.error("--input 또는 stdin 입력 필요")
        overrides = {{}}
        for pair in args.override:
            if "=" not in pair:
                parser.error(f"--override 형식 KEY=VALUE: {{pair}}")
            k, v = pair.split("=", 1)
            overrides[k.strip()] = v
        result = asyncio.run(arun(user_input, overrides=overrides or None))
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(result.get("final_output", ""))
        return 0

    parser.error(f"unknown command: {{args.cmd}}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


# ──────────────────────────────────────────────────────────────
# env.example
# ──────────────────────────────────────────────────────────────

ENV_EXAMPLE_HEADER = """\
# External inputs for {gallery_name} v{gallery_version}
# 자동 생성됨. 실제 배포 전 채우기.
"""


# ──────────────────────────────────────────────────────────────
# README.md
# ──────────────────────────────────────────────────────────────

README_MD = """\
# {gallery_name}

xgen-harness 컴파일 산출물. `snapshot.json` 이 결정적 진실 소스.

- **gallery_name**: `{gallery_name}`
- **gallery_version**: `{gallery_version}`
- **harness_version**: `{harness_version}`

## 설치

```bash
pip install {dist_name}
```

## 사용

```python
import asyncio
from {package_name} import arun

async def main():
    result = await arun("질문 또는 입력")
    print(result["final_output"])

asyncio.run(main())
```

또는 CLI:

```bash
{cli_name} run --input "질문"
{cli_name} info
```

## External Inputs

{external_inputs_section}

## 자동 생성 안내

본 패키지는 `xgen_harness.compile()` 에 의해 자동 생성됐다.
수정하지 말고 원본 워크플로우를 다시 컴파일하라.
"""
