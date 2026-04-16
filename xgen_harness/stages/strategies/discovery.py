"""ToolDiscoveryStrategy 구현체들"""

from typing import Any
from ..interfaces import ToolDiscoveryStrategy


# Level 1 description 최대 길이 (~40 tokens ≈ 120 chars)
_MAX_DESC_CHARS = 120


class ProgressiveDiscovery(ToolDiscoveryStrategy):
    """Progressive Disclosure 3단계 -- 기본 전략.

    Level 1: 도구 메타데이터만 시스템 프롬프트에 (~40 tokens/tool)
             tool_index에는 name + short description만 저장
             full schemas는 tool_schemas에 캐시
    Level 2: discover_tools 빌트인으로 상세 스키마 조회 (LLM이 필요할 때 호출)
    Level 3: 실제 도구 실행 (s08_execute에서 처리)
    """

    @property
    def name(self) -> str:
        return "progressive_3level"

    @property
    def description(self) -> str:
        return "3단계 점진적 디스커버리 (메타데이터->스키마->실행)"

    async def discover(
        self,
        tool_definitions: list[dict],
        state: Any,
    ) -> tuple[list[dict], list[dict]]:
        # Level 1: 메타데이터 인덱스 (name + trimmed description only)
        tool_index = []
        tool_schemas = {}
        for td in tool_definitions:
            name = td.get("name", "unknown")
            raw_desc = td.get("description", "")
            # 120자로 트림 (~40 tokens)
            short_desc = raw_desc[:_MAX_DESC_CHARS]
            if len(raw_desc) > _MAX_DESC_CHARS:
                short_desc = short_desc.rsplit(" ", 1)[0] + "..."

            tool_index.append({
                "name": name,
                "description": short_desc,
                "category": td.get("category", "tool"),
            })
            # Level 2 캐시에는 전체 스키마 보관
            tool_schemas[name] = td

        # discover_tools 빌트인 추가 (Level 2 게이트웨이)
        from ...tools.builtin import DiscoverToolsTool
        discover = DiscoverToolsTool(tool_definitions)
        augmented = list(tool_definitions)
        augmented.append(discover.to_api_format())

        # state에 스키마 캐시 저장 (s08 discover_tools 핸들러에서 사용)
        if hasattr(state, 'tool_schemas'):
            state.tool_schemas = tool_schemas

        return tool_index, augmented


class EagerLoadDiscovery(ToolDiscoveryStrategy):
    """모든 도구 스키마를 즉시 로드 — 도구 수가 적을 때."""

    @property
    def name(self) -> str:
        return "eager_load"

    @property
    def description(self) -> str:
        return "모든 도구 스키마를 즉시 로드"

    async def discover(
        self,
        tool_definitions: list[dict],
        state: Any,
    ) -> tuple[list[dict], list[dict]]:
        tool_index = []
        for td in tool_definitions:
            tool_index.append({
                "name": td.get("name", "unknown"),
                "description": td.get("description", ""),
                "category": "tool",
                "schema": td.get("input_schema"),
            })
        return tool_index, list(tool_definitions)
