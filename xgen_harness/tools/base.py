"""
Tool ABC — 도구 인터페이스

모든 도구(MCP, 빌트인, 노드 브릿지)가 구현하는 기반 인터페이스.
Anthropic tool 정의 포맷과 호환.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """도구 실행 결과"""
    content: str
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_api_format(self, tool_use_id: str) -> dict:
        """Anthropic tool_result 포맷으로 변환"""
        result = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": self.content,
        }
        if self.is_error:
            result["is_error"] = True
        return result

    @classmethod
    def success(cls, content: str, **metadata) -> "ToolResult":
        return cls(content=content, is_error=False, metadata=metadata)

    @classmethod
    def error(cls, message: str) -> "ToolResult":
        return cls(content=message, is_error=True)


class Tool(ABC):
    """도구 기반 인터페이스"""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        ...

    @property
    def input_schema(self) -> dict:
        """JSON Schema for tool input"""
        return {"type": "object", "properties": {}}

    @property
    def category(self) -> str:
        return "general"

    @property
    def is_read_only(self) -> bool:
        """읽기 전용 도구는 병렬 실행 가능"""
        return True

    @abstractmethod
    async def execute(self, input_data: dict) -> ToolResult:
        ...

    def to_api_format(self) -> dict:
        """Anthropic tool 정의 포맷"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def to_index_entry(self) -> dict:
        """Progressive Disclosure Level 1: 메타데이터만"""
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
        }
