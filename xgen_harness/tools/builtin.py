"""
Built-in Tools — 하네스 기본 제공 도구

discover_tools: Progressive Disclosure Level 2 — 도구 상세 스키마 조회
"""

from .base import Tool, ToolResult


class DiscoverToolsTool(Tool):
    """에이전트가 도구의 상세 스키마를 조회하는 빌트인 도구.

    Progressive Disclosure:
    - Level 1: 시스템 프롬프트에 이름+설명만 포함 (~40 tokens/tool)
    - Level 2: 이 도구로 상세 input_schema 조회
    - Level 3: 실제 도구 실행
    """

    def __init__(self, tool_definitions: list[dict]):
        self._tool_defs = {t["name"]: t for t in tool_definitions}

    @property
    def name(self) -> str:
        return "discover_tools"

    @property
    def description(self) -> str:
        return (
            "Get detailed information about available tools. "
            "Call with tool_name to get the full input schema, "
            "or without tool_name to list all tools."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "Name of the tool to get details for. Omit to list all.",
                },
            },
        }

    @property
    def category(self) -> str:
        return "system"

    async def execute(self, input_data: dict) -> ToolResult:
        tool_name = input_data.get("tool_name", "")

        if not tool_name:
            # 전체 목록
            lines = []
            for name, td in self._tool_defs.items():
                desc = td.get("description", "")[:100]
                lines.append(f"- {name}: {desc}")
            return ToolResult.success("\n".join(lines) if lines else "No tools available.")

        td = self._tool_defs.get(tool_name)
        if not td:
            return ToolResult.error(f"Tool '{tool_name}' not found.")

        import json
        return ToolResult.success(json.dumps(td, indent=2, ensure_ascii=False))
