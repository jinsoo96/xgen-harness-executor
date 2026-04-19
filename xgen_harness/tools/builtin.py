"""
Built-in Tools — 하네스 기본 제공 도구

진정한 Progressive Disclosure (Anthropic 스타일):
  Level 0: search_tools(query)       — 키워드로 도구 검색 (큰 카탈로그용)
  Level 1: discover_tools()          — 메타 목록 (이름+설명)
  Level 2: discover_tools(tool_name) — 상세 input_schema
  Level 3: 실제 도구 호출

도구 카탈로그가 작으면 Level 1 만으로 충분, 100+ 개면 search_tools 부터 시작.
"""

import re
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


class SearchToolsTool(Tool):
    """도구 카탈로그를 키워드로 검색 — Progressive Disclosure Level 0.

    카탈로그가 큰 환경(100+ 도구) 에서 첫 호출에 모든 메타를 system_prompt 에
    싣는 비효율을 막음. Anthropic 의 sandbox 도구 패턴 차용 — 환경만 주고
    에이전트가 필요할 때 검색.

    호출 예:
      search_tools(query="email", limit=5)
      → [{"name":"mcp_gmail_send","description":"..."}, ...]
    """

    def __init__(self, tool_definitions: list[dict]):
        self._tools = list(tool_definitions)

    @property
    def name(self) -> str:
        return "search_tools"

    @property
    def description(self) -> str:
        return (
            "Search tools by keyword. Returns matching tools with name and short description. "
            "Call this BEFORE discover_tools when the catalog is large. "
            "After finding a tool, use discover_tools(tool_name) for the full schema."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword(s) to search for in tool name/description."},
                "limit": {"type": "integer", "description": "Max results (default 8).", "default": 8},
                "category": {"type": "string", "description": "Filter by category (optional)."},
            },
            "required": ["query"],
        }

    @property
    def category(self) -> str:
        return "system"

    @property
    def is_read_only(self) -> bool:
        return True

    async def execute(self, input_data: dict) -> ToolResult:
        q = (input_data.get("query") or "").strip().lower()
        limit = int(input_data.get("limit") or 8)
        category_filter = (input_data.get("category") or "").strip().lower()
        if not q:
            return ToolResult.error("'query' is required.")

        terms = [t for t in re.split(r"\s+", q) if t]
        scored: list[tuple[int, dict]] = []
        for td in self._tools:
            name = (td.get("name") or "").lower()
            desc = (td.get("description") or "").lower()
            cat = (td.get("category") or td.get("metadata", {}).get("category") or "").lower()
            if category_filter and category_filter not in cat:
                continue
            score = 0
            for t in terms:
                if t in name:
                    score += 3
                if t in desc:
                    score += 1
                if t == name:
                    score += 5
            if score > 0:
                scored.append((score, td))

        scored.sort(key=lambda x: -x[0])
        top = scored[:limit]
        if not top:
            return ToolResult.success(f"No tools matched '{q}'. Try discover_tools() to see all.")

        lines = [f"Matched {len(top)} of {len(scored)} tools:"]
        for s, td in top:
            n = td.get("name", "?")
            d = (td.get("description") or "")[:120]
            lines.append(f"- {n} (score={s}): {d}")
        lines.append("\nNext: discover_tools(tool_name=...) for full schema.")
        return ToolResult.success("\n".join(lines))
