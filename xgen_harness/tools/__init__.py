"""
xgen-harness 도구 패키지 — Tool source registry for extensible tool dispatch.

외부 패키지/코드에서 ToolSource를 등록하면 s08_act가 자동으로 인식한다.

Usage::

    from xgen_harness.tools import register_tool_source, ToolSource

    class MyToolSource:
        async def list_tools(self) -> list[dict]: ...
        async def call_tool(self, name: str, args: dict) -> dict: ...
        def has_tool(self, name: str) -> bool: ...

    register_tool_source(MyToolSource())
"""

import logging
from typing import Protocol, runtime_checkable

logger = logging.getLogger("harness.tools")

_TOOL_SOURCES: list = []
_ENTRY_POINTS_DISCOVERED = False
_MANIFEST_PRELOADED = False

# v0.16.3 자동 연동 — 환경 변수에 매니페스트 파일 경로가 지정되면, 첫 조회 시
# `compile.local_manifest.LocalManifest` 를 통해 SynthesizedTool 을 자동 등록.
# 여러 파일은 OS path separator(`:` on posix, `;` on windows) 또는 JSON list 로.
ENV_PRELOAD_MANIFEST = "XGEN_HARNESS_PRELOAD_MANIFEST"


@runtime_checkable
class ToolSource(Protocol):
    """도구 소스 프로토콜 — 이 인터페이스만 구현하면 등록 가능."""

    async def list_tools(self) -> list[dict]:
        """사용 가능한 도구 목록 반환. 각 dict는 name, description 포함."""
        ...

    async def call_tool(self, name: str, args: dict) -> dict:
        """도구 실행. 반환 dict는 최소 {"content": str}."""
        ...

    def has_tool(self, name: str) -> bool:
        """해당 이름의 도구를 이 소스가 가지고 있는지 확인."""
        ...


def register_tool_source(source: ToolSource) -> None:
    """도구 소스를 전역 레지스트리에 등록한다."""
    if not isinstance(source, ToolSource):
        raise TypeError(
            f"Tool source must implement ToolSource protocol (list_tools, call_tool, has_tool). "
            f"Got: {type(source).__name__}"
        )
    _TOOL_SOURCES.append(source)
    logger.info("Tool source registered: %s", type(source).__name__)


def get_tool_sources() -> list:
    """등록된 모든 도구 소스 반환 (복사본).

    v0.15.2 — 첫 호출 시 `xgen_harness.tool_sources` entry_points 자동 스캔.
    v0.16.3 — `XGEN_HARNESS_PRELOAD_MANIFEST` env 의 LocalManifest 파일도 자동 로드.
    외부 패키지가 `pyproject.toml` 에 선언만 해도 엔진 무수정 합류.
    """
    _discover_from_entry_points_once()
    _preload_manifest_once()
    return list(_TOOL_SOURCES)


def clear_tool_sources() -> None:
    """테스트용: 등록된 도구 소스 초기화."""
    global _ENTRY_POINTS_DISCOVERED
    _TOOL_SOURCES.clear()
    _ENTRY_POINTS_DISCOVERED = False


def _discover_from_entry_points_once() -> None:
    """외부 패키지의 `xgen_harness.tool_sources` entry_points 를 자동 스캔.

    entry_point 반환값 허용 형태:
      - ToolSource 인스턴스 (Protocol 구현체)
      - ToolSource 인스턴스의 factory (callable 0 인자)
      - list/iterable of ToolSource
    """
    global _ENTRY_POINTS_DISCOVERED
    if _ENTRY_POINTS_DISCOVERED:
        return
    _ENTRY_POINTS_DISCOVERED = True
    try:
        from importlib.metadata import entry_points
    except Exception:
        return
    try:
        eps = entry_points()
        group = "xgen_harness.tool_sources"
        if hasattr(eps, "select"):
            items = eps.select(group=group)
        else:
            items = eps.get(group, [])
        for ep in items:
            try:
                loaded = ep.load()
                result = loaded() if callable(loaded) else loaded
                _register_entry_point_result(result)
            except Exception as e:
                logger.debug("[tools] entry_point %s load failed: %s", ep.name, e)
    except Exception as e:
        logger.debug("[tools] entry_points scan failed: %s", e)


def _register_entry_point_result(result) -> None:
    """entry_point 결과를 허용 형태에 따라 register_tool_source."""
    if result is None:
        return
    if isinstance(result, ToolSource):
        register_tool_source(result)
        return
    if hasattr(result, "__iter__") and not isinstance(result, (str, bytes)):
        for item in result:
            if isinstance(item, ToolSource):
                register_tool_source(item)


def _preload_manifest_once() -> None:
    """`XGEN_HARNESS_PRELOAD_MANIFEST` env 에 지정된 LocalManifest 파일을 자동 로드.

    v0.16.3 자동 연동 자동 확장성 — 운영/이식 측이 매니페스트 파일 경로만
    환경변수로 주입하면 엔진 코드 변경 0. Tool Synthesis Loop 로 만든 도구
    번들을 다른 프로세스 / 다른 인스턴스에 그대로 주입 가능.

    여러 경로는 OS path separator (`os.pathsep`) 로 구분.
    """
    global _MANIFEST_PRELOADED
    if _MANIFEST_PRELOADED:
        return
    _MANIFEST_PRELOADED = True
    import os
    env_val = os.environ.get(ENV_PRELOAD_MANIFEST, "").strip()
    if not env_val:
        return
    paths = [p.strip() for p in env_val.split(os.pathsep) if p.strip()]
    try:
        from ..tools.synthesis import load_synthesized_from_gallery
    except Exception as e:
        logger.debug("[tools] preload: synthesis import failed: %s", e)
        return
    for p in paths:
        try:
            restored = load_synthesized_from_gallery(p)
            for tool in restored:
                register_tool_source(tool.as_source())
            logger.info("[tools] preload manifest %s: %d tools", p, len(restored))
        except Exception as e:
            logger.warning("[tools] preload %s failed: %s", p, e)
