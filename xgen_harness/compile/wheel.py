"""
Wheel builder — 소스 트리 생성 + `python -m build --wheel` 호출.

외부 패키지 의존성 0 (순수 표준 라이브러리 + build 모듈). build 모듈은 Python
공식 PyPA 패키지 — 없으면 친절한 에러를 낸다.

결정 사항:
  - Jinja 미사용 (추가 의존성 회피). templates/__init__.py 의 ``str.format()``.
  - dist_name = "xgen-gallery-<sanitized_name>".
  - package_name = "xgen_gallery_<sanitized_name>".
  - entry_point_name = gallery_name (UI 가 이걸로 찾음).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from . import templates
from .deps import resolve_dependencies
from .snapshot import WorkflowSnapshot


GALLERY_DIST_PREFIX = "xgen-gallery-"
GALLERY_PKG_PREFIX = "xgen_gallery_"


@dataclass
class WheelBuildResult:
    wheel_path: Path
    sdist_path: Optional[Path]
    source_dir: Path
    dist_name: str
    package_name: str
    snapshot: WorkflowSnapshot


# ──────────────────────────────────────────────────────────────
# 공개 API
# ──────────────────────────────────────────────────────────────

def compile_workflow(
    *,
    harness_config: Any,
    workflow_data: Optional[dict[str, Any]] = None,
    gallery_name: str,
    gallery_version: str = "0.1.0",
    description: str = "",
    out_dir: str | os.PathLike[str] = "./dist",
    keep_source: bool = False,
    extra_metadata: Optional[dict[str, Any]] = None,
) -> WheelBuildResult:
    """하네스 워크플로우 → wheel (한 줄 API).

    Args:
        harness_config: HarnessConfig 인스턴스 혹은 dict.
        workflow_data: 캔버스 스냅샷 (없으면 빈 dict).
        gallery_name: 배포 이름 (소문자/숫자/언더스코어/하이픈).
        gallery_version: PEP 440 버전.
        description: 패키지 설명.
        out_dir: wheel 이 떨어질 디렉토리.
        keep_source: True 면 빌드용 소스 트리를 out_dir 에 유지.
        extra_metadata: snapshot.metadata 에 추가 기록.

    Returns:
        WheelBuildResult — wheel_path / sdist_path / source_dir.
    """
    snapshot = WorkflowSnapshot.from_config(
        harness_config=harness_config,
        workflow_data=workflow_data,
        gallery_name=gallery_name,
        gallery_version=gallery_version,
        extra_metadata={
            **(extra_metadata or {}),
            "description": description,
        },
    )
    return build_wheel(snapshot, out_dir=out_dir, keep_source=keep_source)


def build_wheel(
    snapshot: WorkflowSnapshot,
    *,
    out_dir: str | os.PathLike[str] = "./dist",
    keep_source: bool = False,
) -> WheelBuildResult:
    """스냅샷 → wheel 생성. `python -m build --wheel --sdist` 호출."""
    out_path = Path(out_dir).resolve()
    out_path.mkdir(parents=True, exist_ok=True)

    sanitized = _sanitize_name(snapshot.gallery_name)
    dist_name = f"{GALLERY_DIST_PREFIX}{sanitized}"
    package_name = f"{GALLERY_PKG_PREFIX}{sanitized}"

    # 의존성 계산 — snapshot 에 저장된 값이 없으면 resolver.
    if not snapshot.dependencies:
        snapshot.dependencies = resolve_dependencies(snapshot)

    # 소스 트리 생성 위치: keep_source 면 out_dir/_build_<sanitized>, 아니면 tempdir.
    if keep_source:
        src_root = out_path / f"_build_{sanitized}"
        if src_root.exists():
            shutil.rmtree(src_root)
        src_root.mkdir(parents=True)
        cleanup = False
    else:
        src_root = Path(tempfile.mkdtemp(prefix=f"xgen-compile-{sanitized}-"))
        cleanup = True

    try:
        _write_source_tree(
            src_root=src_root,
            dist_name=dist_name,
            package_name=package_name,
            snapshot=snapshot,
        )
        wheel_path, sdist_path = _invoke_build(src_root, out_path)
        return WheelBuildResult(
            wheel_path=wheel_path,
            sdist_path=sdist_path,
            source_dir=src_root,
            dist_name=dist_name,
            package_name=package_name,
            snapshot=snapshot,
        )
    finally:
        if cleanup and not keep_source:
            shutil.rmtree(src_root, ignore_errors=True)


# ──────────────────────────────────────────────────────────────
# 내부
# ──────────────────────────────────────────────────────────────

_NAME_SANITIZE_RE = re.compile(r"[^a-z0-9]+")


def _sanitize_name(name: str) -> str:
    s = _NAME_SANITIZE_RE.sub("_", name.lower()).strip("_")
    if not s:
        raise ValueError(f"gallery_name '{name}' 이 비정상 (sanitize 후 빈 문자열)")
    return s


def _quote_toml_str(s: str) -> str:
    """toml 문자열 안전 quote — json.dumps 로 이스케이프 처리."""
    return json.dumps(s, ensure_ascii=False)


def _render_dependencies_block(deps: dict[str, str]) -> str:
    """pyproject.toml 의 dependencies 배열 엔트리 렌더."""
    lines: list[str] = []
    for pkg, ver in sorted(deps.items()):
        spec = f"{pkg}{ver}" if ver else pkg
        lines.append(f"    {_quote_toml_str(spec)},")
    return "\n".join(lines)


def _render_env_example(snapshot: WorkflowSnapshot) -> str:
    header = templates.ENV_EXAMPLE_HEADER.format(
        gallery_name=snapshot.gallery_name,
        gallery_version=snapshot.gallery_version,
    )
    lines = [header]
    for name, spec in sorted((snapshot.external_inputs or {}).items()):
        desc = spec.get("description", "")
        required = spec.get("required", True)
        default = spec.get("default")
        t = spec.get("type", "string")
        tag = "required" if required else "optional"
        if desc:
            lines.append(f"# {desc}")
        lines.append(f"# type={t} ({tag})")
        value = "" if default is None else str(default)
        lines.append(f"{name}={value}")
        lines.append("")
    return "\n".join(lines)


def _render_external_inputs_md(snapshot: WorkflowSnapshot) -> str:
    if not snapshot.external_inputs:
        return "(없음)"
    rows = ["| Name | Type | Required | Default | Description |",
            "|------|------|----------|---------|-------------|"]
    for name, spec in sorted(snapshot.external_inputs.items()):
        rows.append(
            f"| `{name}` | {spec.get('type', 'string')} | "
            f"{'✓' if spec.get('required', True) else ''} | "
            f"{spec.get('default', '') or ''} | {spec.get('description', '')} |"
        )
    return "\n".join(rows)


def _write_source_tree(
    *,
    src_root: Path,
    dist_name: str,
    package_name: str,
    snapshot: WorkflowSnapshot,
) -> None:
    """src_root 아래 빌드 가능한 소스 트리 생성."""
    pkg_dir = src_root / package_name
    pkg_dir.mkdir(parents=True, exist_ok=True)

    cli_name = dist_name  # CLI 실행명은 dist_name 과 동일 (`xgen-gallery-foo`)
    entry_point_name = snapshot.gallery_name

    # pyproject.toml
    pyproject = templates.PYPROJECT_TOML.format(
        dist_name=dist_name,
        gallery_version=snapshot.gallery_version,
        description_toml=_quote_toml_str(
            snapshot.metadata.get("description") or f"{snapshot.gallery_name} (xgen-harness compiled)"
        ),
        dependencies_block=_render_dependencies_block(snapshot.dependencies or {}),
        cli_name=cli_name,
        package_name=package_name,
        entry_point_name=entry_point_name,
    )
    (src_root / "pyproject.toml").write_text(pyproject, encoding="utf-8")

    # README.md
    readme = templates.README_MD.format(
        gallery_name=snapshot.gallery_name,
        gallery_version=snapshot.gallery_version,
        harness_version=snapshot.harness_version,
        dist_name=dist_name,
        package_name=package_name,
        cli_name=cli_name,
        external_inputs_section=_render_external_inputs_md(snapshot),
    )
    (src_root / "README.md").write_text(readme, encoding="utf-8")

    # package/__init__.py
    init_py = templates.PACKAGE_INIT.format(gallery_name=snapshot.gallery_name)
    (pkg_dir / "__init__.py").write_text(init_py, encoding="utf-8")

    # package/cli.py
    cli_py = templates.CLI_PY.format(cli_name=cli_name)
    (pkg_dir / "cli.py").write_text(cli_py, encoding="utf-8")

    # package/snapshot.json
    (pkg_dir / "snapshot.json").write_text(snapshot.to_json(), encoding="utf-8")

    # package/env.example
    (pkg_dir / "env.example").write_text(_render_env_example(snapshot), encoding="utf-8")


def _invoke_build(src_root: Path, out_dir: Path) -> tuple[Path, Optional[Path]]:
    """`python -m build --wheel --sdist` 로 빌드 수행. wheel/sdist 경로 반환."""
    try:
        import build  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "wheel 빌드에 `build` 모듈이 필요합니다. "
            "`pip install build` 후 다시 시도하세요."
        ) from e

    env = os.environ.copy()
    cmd = [
        sys.executable, "-m", "build",
        "--wheel", "--sdist",
        "--outdir", str(out_dir),
        str(src_root),
    ]
    # build 의 격리 빌드는 네트워크 의존. 폐쇄망 대응 옵션 — `--no-isolation`
    # 을 기본으로 두면 빌드 환경의 setuptools/wheel 을 그대로 사용해 오프라인 OK.
    cmd.insert(4, "--no-isolation")

    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"wheel 빌드 실패 (exit={result.returncode})\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )

    wheels = sorted(out_dir.glob("*.whl"), key=lambda p: p.stat().st_mtime, reverse=True)
    sdists = sorted(out_dir.glob("*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not wheels:
        raise RuntimeError(f"wheel 생성 안 됨 (out_dir={out_dir})")
    return wheels[0], (sdists[0] if sdists else None)
