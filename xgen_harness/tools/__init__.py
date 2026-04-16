"""
xgen-harness 도구 패키지 — Tool source registry for extensible tool dispatch.

외부 패키지/코드에서 ToolSource를 등록하면 s08_execute가 자동으로 인식한다.

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
    """등록된 모든 도구 소스 반환 (복사본)."""
    return list(_TOOL_SOURCES)


def clear_tool_sources() -> None:
    """테스트용: 등록된 도구 소스 초기화."""
    _TOOL_SOURCES.clear()
