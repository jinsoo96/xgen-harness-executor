"""
Built-in Tools — 하네스 기본 제공 도구

진정한 Progressive Disclosure (Anthropic 스타일):
  Level 0: search_tools(query)       — 키워드로 도구 검색 (큰 카탈로그용)
  Level 1: discover_tools()          — 메타 목록 (이름+설명)
  Level 2: discover_tools(tool_name) — 상세 input_schema
  Level 3: 실제 도구 호출

도구 카탈로그가 작으면 Level 1 만으로 충분, 100+ 개면 search_tools 부터 시작.
"""

import logging
import re
from typing import Callable

from .base import Tool, ToolResult

# v1.0.9 — Term expander 인프라는 term_expansion.py 단일 정의로 분리됨 (god-class 정리).
# 본 모듈에서는 하위 호환을 위한 re-export 만 유지. 외부 호출자는 신규 모듈 또는
# tools 패키지 (`from xgen_harness.tools import register_term_expander`) 사용 권장.
from .term_expansion import (
    TermExpander,
    register_term_expander,
    register_search_alias,
    list_term_expanders,
    list_search_aliases,
    expand_query_terms,
    _expand_query_terms,  # 구 private alias 호환
)

logger = logging.getLogger("harness.tools.search")


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

    @property
    def read_only_hint(self) -> bool:
        return True

    @property
    def idempotent_hint(self) -> bool:
        return True

    @property
    def open_world_hint(self) -> bool:
        return False  # 내부 카탈로그만 읽음

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
    def read_only_hint(self) -> bool:
        return True

    @property
    def idempotent_hint(self) -> bool:
        return True

    @property
    def open_world_hint(self) -> bool:
        return False

    async def execute(self, input_data: dict) -> ToolResult:
        q = (input_data.get("query") or "").strip().lower()
        limit = int(input_data.get("limit") or 8)
        category_filter = (input_data.get("category") or "").strip().lower()
        if not q:
            return ToolResult.error("'query' is required.")

        # v0.26.19 — 한국어 query 가 영문 도구명/description 과 매칭 안 되던 결함 fix.
        # raw terms 1차 매칭 후 0건이면 한국어→영문 alias 로 expand 후 2차 매칭.
        # 그래도 0건이면 LLM 이 "도구 없음" 결론 안 내도록 카테고리 hint + 전체
        # discover 안내. 라이브 사례: "네이버 뉴스" → mcp_naver_news_mcp 매치 실패.
        raw_terms = [t for t in re.split(r"\s+", q) if t]
        expanded_terms = _expand_query_terms(raw_terms)
        scored = self._score_terms(expanded_terms, category_filter)

        scored.sort(key=lambda x: -x[0])
        top = scored[:limit]
        if not top:
            return ToolResult.success(self._empty_match_hint(q, category_filter, limit))

        used_aliases = [t for t in expanded_terms if t not in raw_terms]
        header = f"Matched {len(top)} of {len(scored)} tools"
        if used_aliases:
            header += f" (expanded with: {', '.join(used_aliases)})"
        lines = [f"{header}:"]
        for s, td in top:
            n = td.get("name", "?")
            d = (td.get("description") or "")[:120]
            lines.append(f"- {n} (score={s}): {d}")
        # v1.4.0 — Claude Code 패턴 정합. 두 다음 단계 명확히 안내.
        # ToolSearch = schema 합류 (다음 turn 호출 가능) / discover_tools = schema 조회만.
        lines.append(
            "\nNext: ToolSearch(names=[...]) to load and make callable, "
            "or discover_tools(tool_name=...) to inspect schema only."
        )
        return ToolResult.success("\n".join(lines))

    def _score_terms(self, terms: list[str], category_filter: str) -> list[tuple[int, dict]]:
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
        return scored

    def _empty_match_hint(self, q: str, category_filter: str, limit: int) -> str:
        """매칭 0건일 때 LLM 이 즉시 포기하지 않도록 카테고리 후보 + 권유.

        총 도구 수 적으면 (≤ 20) 그냥 전체 목록 제공. 많으면 카테고리별 top 몇 개씩
        샘플링. 마지막에 명확한 다음 액션 (discover_tools / 다른 query) 안내.
        """
        if not self._tools:
            return f"No tools available."

        from collections import defaultdict
        by_cat: dict[str, list[dict]] = defaultdict(list)
        for td in self._tools:
            cat = (td.get("category") or td.get("metadata", {}).get("category") or "uncategorized").lower()
            by_cat[cat].append(td)

        lines = [f"No exact match for '{q}'. Showing available tools by category:"]
        # 카테고리당 limit/카테고리수 정도로 sample (최소 1, 최대 limit)
        per_cat = max(1, min(limit, 5))
        for cat in sorted(by_cat.keys()):
            if category_filter and category_filter not in cat:
                continue
            samples = by_cat[cat][:per_cat]
            lines.append(f"\n[{cat}] ({len(by_cat[cat])} tools)")
            for td in samples:
                n = td.get("name", "?")
                d = (td.get("description") or "")[:80]
                lines.append(f"- {n}: {d}")
            if len(by_cat[cat]) > per_cat:
                lines.append(f"  ... +{len(by_cat[cat]) - per_cat} more")
        lines.append(
            "\nNext: search_tools with English keyword (e.g. 'naver', 'news', 'search') "
            "or discover_tools() for full list. To load and call: ToolSearch(names=['<picked>'])."
        )
        return "\n".join(lines)


class ToolSearchTool(Tool):
    """v1.2.0 — Claude Code 스타일 deferred tools 승격 빌트인.

    s04_tool 이 selected_tools 화이트리스트 외 도구를 모두 deferred 로 보내고,
    full schema 는 ``state.tool_schemas`` 캐시에만 둔다. LLM 은 system_prompt
    의 [deferred] 섹션에서 도구 이름을 보고, 필요한 도구만 이 빌트인으로 명시
    승격해 호출 가능 상태로 만든다.

    호출 패턴:
      ToolSearch(names=["mcp_notion_search", "brave_web_search"])
        → state.tool_schemas[name] 을 state.tool_definitions 에 합류
        → 다음 llm_call 의 tools= 인자에 자동 누적
        → LLM 이 그 도구를 직접 호출 가능

    keyword 검색은 별도 search_tools 빌트인 (token-grep) 이 담당.
    """

    def __init__(self, state_ref):
        # state_ref: PipelineState 인스턴스. 매 턴 같은 인스턴스라 schemas / definitions
        # 가 live 하게 공유된다 (FetchPDTool 와 같은 패턴).
        self._state = state_ref

    @property
    def name(self) -> str:
        return "ToolSearch"

    @property
    def description(self) -> str:
        return (
            "Load full schemas for deferred tools so they become callable. "
            "Pass `names` to load specific tools by name (e.g. select:tool1,tool2). "
            "Loaded tools become callable in subsequent turns."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Exact tool names to load (from the [deferred] list).",
                },
            },
            "required": ["names"],
        }

    @property
    def category(self) -> str:
        return "system"

    @property
    def read_only_hint(self) -> bool:
        return True

    @property
    def idempotent_hint(self) -> bool:
        return True

    @property
    def open_world_hint(self) -> bool:
        return False  # 캐시(state.tool_schemas) 만 읽음

    async def execute(self, input_data: dict) -> ToolResult:
        names_in = input_data.get("names") or []
        if isinstance(names_in, str):
            # "select:a,b" 형태 또는 단일 문자열도 허용
            raw = names_in.split(":", 1)[1] if names_in.startswith("select:") else names_in
            names_in = [n.strip() for n in raw.split(",") if n.strip()]
        if not isinstance(names_in, list) or not names_in:
            return ToolResult.error("'names' must be a non-empty array of tool names.")

        schemas = self._state.tool_schemas or {}
        existing = {td.get("name") for td in self._state.tool_definitions}
        loaded: list[str] = []
        not_found: list[str] = []
        already: list[str] = []

        for nm in names_in:
            if not isinstance(nm, str) or not nm.strip():
                continue
            nm = nm.strip()
            if nm in existing:
                already.append(nm)
                continue
            schema = schemas.get(nm)
            if not schema:
                not_found.append(nm)
                continue
            self._state.tool_definitions.append(schema)
            self._state.tool.loaded_names.add(nm)
            loaded.append(nm)

        # ToolLoadedEvent emit (best-effort).
        try:
            from ..events.types import ToolLoadedEvent
            await self._state.emit_verbose(ToolLoadedEvent(
                names=loaded,
                total_loaded=len(self._state.tool.loaded_names),
            ))
        except Exception as _e:
            logger.debug("[ToolSearch] event emit failed: %s", _e)

        lines = []
        if loaded:
            lines.append(f"Loaded {len(loaded)} tool(s): {', '.join(loaded)}")
            lines.append("These tools are now callable in your next turn.")
        if already:
            lines.append(f"Already loaded: {', '.join(already)}")
        if not_found:
            available = list(schemas.keys())[:20]
            lines.append(
                f"Not found: {', '.join(not_found)}. "
                f"Available deferred tools (first 20): {', '.join(available) or '(none)'}"
            )
        if not lines:
            lines.append("Nothing to load.")
        return ToolResult.success("\n".join(lines))


class FetchPDTool(Tool):
    """Progressive Disclosure 원본 조회 — messages 에 preview 만 노출한 리소스의 전체 내용 반환.

    Push-side 압축 (L1 tool result budget, L3 microcompact) 또는 pull-side 출력 지연
    (RAG 청크, DB 스키마 등) 으로 원본이 state.pd_stores 에 보관된 경우, 에이전트가
    이 도구로 id 를 명시해 원본을 당겨올 수 있습니다.

    호출 예:
      fetch_pd(kind="tool_result", id="toolu_01abc...")  → 50KB+ 도구 결과 원본
      fetch_pd(kind="rag",          id="0")              → 첫 번째 RAG 청크 본문
      fetch_pd(kind="db_schema",    id="products")       → 특정 테이블 스키마 상세

    종류 목록만 궁금하면 `kind` 만 주고 id 를 생략해 id 리스트를 받습니다.
    """

    def __init__(self, state_ref):
        # state_ref: PipelineState 인스턴스. s04 가 주입.
        # 매 턴마다 s04 가 FetchPDTool 을 재생성하거나, __init__ 에서 받은 ref 가
        # 같은 PipelineState 인스턴스이므로 pd_stores 는 live 하게 공유됩니다.
        self._state = state_ref

    @property
    def name(self) -> str:
        return "fetch_pd"

    @property
    def description(self) -> str:
        return (
            "Fetch the full content of a resource whose preview is in the conversation. "
            "Use when a tool result or retrieved chunk was truncated to a preview. "
            "Provide both `kind` and `id`. Omit `id` to list available ids for a kind."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": "Resource type (tool_result | rag | history | db_schema | gallery | ...)",
                },
                "id": {
                    "type": "string",
                    "description": "Resource id within the kind. Omit to list available ids.",
                },
            },
            "required": ["kind"],
        }

    @property
    def category(self) -> str:
        return "system"

    @property
    def read_only_hint(self) -> bool:
        return True

    @property
    def idempotent_hint(self) -> bool:
        return True

    @property
    def open_world_hint(self) -> bool:
        return False  # pd_stores 는 프로세스 내부 state

    async def execute(self, input_data: dict) -> ToolResult:
        kind = (input_data.get("kind") or "").strip()
        rid = input_data.get("id")
        if not kind:
            return ToolResult.error("'kind' is required.")
        if rid is None or rid == "":
            ids = self._state.pd_list(kind)
            if not ids:
                return ToolResult.success(f"No stored resources for kind={kind!r}.")
            return ToolResult.success(
                f"kind={kind!r} has {len(ids)} resources:\n" +
                "\n".join(f"- {i}" for i in ids[:50]) +
                ("\n..." if len(ids) > 50 else "")
            )
        entry = self._state.pd_fetch(kind, str(rid))
        if entry is None:
            return ToolResult.error(
                f"No resource for kind={kind!r} id={rid!r}. "
                f"Available ids: {self._state.pd_list(kind)[:10]}"
            )
        full = entry.get("full", "")
        meta = entry.get("meta", {})
        header = f"[pd:{kind}:{rid}] meta={meta}" if meta else f"[pd:{kind}:{rid}]"
        return ToolResult.success(f"{header}\n\n{full}")


class CompactTool(Tool):
    """대화 이력·도구 결과를 LLM 이 스스로 축약해 컨텍스트 비용을 줄임.

    자동 threshold(s07_act 의 50KB per-tool-result 컷) 는 **개별 결과** 만 자름.
    여러 턴·여러 도구 결과가 누적해 부풀면 자동으로 못 잡는다. CompactTool 은
    LLM 이 "지금 무엇을 버려도 되는지" 판단하고 직접 호출해 버림 (Anthropic·
    Cursor 류 long-running agent 표준 패턴).

    scope:
      - `tool_results_before:N` — N번째 이전 turn 들의 도구 결과 메시지 요약
      - `history_before:N`      — N번째 이전 대화 턴 요약
      - `pd_store:<kind>`       — pd_stores 의 특정 kind 원본을 요약으로 대체

    summary_hint:
      LLM 이 "어떤 정보를 남길지" 힌트 (예: "주문번호와 금액만", "에러 스택만").
      비워두면 일반 요약.

    summarizer 주입 방식:
      s04_tool 이 Tool 인스턴스 생성 시 state 와 summarizer(콜러블) 를 바인딩.
      summarizer 는 `(texts: list[str], hint: str) -> str` 시그니처.
      기본 summarizer 는 state.provider 로 짧은 LLM 호출 수행 — provider 없으면
      length-based truncate 로 폴백 (의미론 손실 있지만 동작 유지).

    파괴적 호출 — `destructive_hint=True`. HITLGuard 가 트리거 대상으로 잡으면
    사용자 승인 후 실행. 프로덕션에선 일반적으로 `trigger_destructive=True`
    기본이라 compact 도 한 번 확인을 거치게 됨. dev 환경은 auto-approve 권장.
    """

    def __init__(self, state_ref, summarizer=None):
        self._state = state_ref
        self._summarizer = summarizer   # async callable or None

    @property
    def name(self) -> str:
        return "compact"

    @property
    def description(self) -> str:
        return (
            "Compact older tool results or conversation turns into a short summary "
            "to reduce context size. Use when prior turns have large tool outputs "
            "you no longer need verbatim. Provide `scope` (e.g. "
            "'tool_results_before:5') and optional `summary_hint` for what to keep."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "description": (
                        "Target to compact. One of: "
                        "'tool_results_before:<N>' | 'history_before:<N>' | 'pd_store:<kind>'"
                    ),
                },
                "summary_hint": {
                    "type": "string",
                    "description": "Optional. What to preserve in the summary "
                                   "(e.g. 'order numbers and amounts only').",
                },
            },
            "required": ["scope"],
        }

    @property
    def category(self) -> str:
        return "system"

    @property
    def read_only_hint(self) -> bool:
        return False   # 메시지 치환 — state 변경

    @property
    def destructive_hint(self) -> bool:
        return True    # 원본 문자열 손실 (요약 치환)

    @property
    def idempotent_hint(self) -> bool:
        return False   # 두 번 부르면 요약의 요약

    @property
    def open_world_hint(self) -> bool:
        return False   # 프로세스 내부 state 만

    async def execute(self, input_data: dict) -> ToolResult:
        scope = (input_data.get("scope") or "").strip()
        hint = (input_data.get("summary_hint") or "").strip()
        if not scope:
            return ToolResult.error("'scope' is required.")

        kind, _, arg = scope.partition(":")
        kind = kind.strip()
        arg = arg.strip()

        if kind == "tool_results_before":
            return await self._compact_tool_results_before(arg, hint)
        if kind == "history_before":
            return await self._compact_history_before(arg, hint)
        if kind == "pd_store":
            return await self._compact_pd_store(arg, hint)
        return ToolResult.error(
            f"Unknown scope kind {kind!r}. Supported: "
            "tool_results_before:<N> | history_before:<N> | pd_store:<kind>"
        )

    async def _compact_tool_results_before(self, arg: str, hint: str) -> ToolResult:
        try:
            n = int(arg)
        except Exception:
            return ToolResult.error(f"tool_results_before:<N> expects integer, got {arg!r}")

        # messages 에서 user 역할의 tool_result content block 을 가진 메시지를 역순 스캔
        targets_idx: list[int] = []
        messages = self._state.messages or []
        for i, m in enumerate(messages):
            content = m.get("content") if isinstance(m, dict) else None
            if not isinstance(content, list):
                continue
            if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                targets_idx.append(i)

        if len(targets_idx) <= n:
            return ToolResult.success(
                f"only {len(targets_idx)} tool_result messages present; nothing before index {n}."
            )

        victims = targets_idx[:-n] if n > 0 else targets_idx
        texts: list[str] = []
        for i in victims:
            content = messages[i]["content"]
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content")
                    if isinstance(c, str):
                        texts.append(c)
                    elif isinstance(c, list):
                        for sub in c:
                            if isinstance(sub, dict) and sub.get("type") == "text":
                                texts.append(sub.get("text", ""))

        summary = await self._summarize(texts, hint or "preserve key facts for later reference")
        replacement = f"[compacted {len(victims)} tool_result messages — hint: {hint or '-'}]\n\n{summary}"

        # 첫 victim 위치에 요약 메시지 하나로 치환, 나머지 삭제.
        first = victims[0]
        new_msg = {"role": "user", "content": replacement}
        new_messages = []
        replaced = False
        victim_set = set(victims)
        for i, m in enumerate(messages):
            if i in victim_set:
                if not replaced:
                    new_messages.append(new_msg)
                    replaced = True
                continue
            new_messages.append(m)
        self._state.messages = new_messages

        return ToolResult.success(
            f"compacted {len(victims)} tool_result messages → 1 summary "
            f"(~{len(replacement):,} chars)",
            victims=len(victims),
            chars=len(replacement),
        )

    async def _compact_history_before(self, arg: str, hint: str) -> ToolResult:
        try:
            n = int(arg)
        except Exception:
            return ToolResult.error(f"history_before:<N> expects integer, got {arg!r}")

        # 전체 messages 를 두 구간으로 분리: 보존(마지막 N 턴) + 희생(그 이전)
        msgs = list(self._state.messages or [])
        if len(msgs) <= n:
            return ToolResult.success(
                f"only {len(msgs)} messages present; nothing before index {n}."
            )
        victims = msgs[:-n] if n > 0 else msgs
        keep = msgs[-n:] if n > 0 else []

        texts: list[str] = []
        for m in victims:
            content = m.get("content") if isinstance(m, dict) else m
            if isinstance(content, str):
                texts.append(f"[{m.get('role','?')}] {content}")
            elif isinstance(content, list):
                for b in content:
                    if isinstance(b, dict):
                        t = b.get("text") or b.get("content") or ""
                        if isinstance(t, str) and t:
                            texts.append(f"[{m.get('role','?')}] {t}")

        summary = await self._summarize(texts, hint or "preserve intent, decisions, and key facts")
        summary_msg = {"role": "user", "content": f"[compacted history — {hint or '-'}]\n\n{summary}"}
        self._state.messages = [summary_msg] + keep

        return ToolResult.success(
            f"compacted {len(victims)} messages → 1 summary "
            f"(kept last {len(keep)})",
            victims=len(victims),
            kept=len(keep),
        )

    async def _compact_pd_store(self, kind: str, hint: str) -> ToolResult:
        bucket = self._state.pd_stores.get(kind) if hasattr(self._state, "pd_stores") else None
        if not bucket:
            return ToolResult.error(f"pd_store kind {kind!r} empty or missing.")

        ids = list(bucket.keys())
        texts = [bucket[rid].get("full", "") for rid in ids]
        summary = await self._summarize(texts, hint or f"summarize {kind} entries")

        # 각 entry 의 full 을 요약 한 줄로 대체 (preview 는 유지).
        for rid in ids:
            bucket[rid]["full"] = f"[compacted — hint: {hint or '-'}]\n{summary}"
            bucket[rid].setdefault("meta", {})["compacted"] = True

        return ToolResult.success(
            f"compacted pd_store[{kind}] {len(ids)} entries",
            entries=len(ids),
        )

    async def _summarize(self, texts: list[str], hint: str) -> str:
        """summarizer 주입 우선, 없으면 길이 기반 폴백."""
        joined = "\n\n---\n\n".join(t for t in texts if t)
        if not joined:
            return "(empty)"

        if self._summarizer is not None:
            try:
                result = self._summarizer(joined, hint)
                if hasattr(result, "__await__"):
                    result = await result
                if isinstance(result, str) and result.strip():
                    return result.strip()
            except Exception as e:
                import logging as _logging
                _logging.getLogger("harness.tools.compact").warning(
                    "summarizer failed, falling back to truncate: %s", e,
                )

        # 폴백: 앞부분 N자만 유지. 의미론 손실 있지만 동작 유지.
        limit = 2000
        if len(joined) <= limit:
            return joined
        return joined[:limit] + f"\n... [truncated from {len(joined):,} chars — no summarizer available]"


# ────────────────────────────────────────────────────────────────────────────
# v1.6 신규 빌트인 — Pack 3 (사용자 PD 정신: policy / prompt / collection 도구화)
# ────────────────────────────────────────────────────────────────────────────


class CheckPolicyTool(Tool):
    """v1.6 — Policy self-check 빌트인.

    LLM 이 민감 도구 호출 전 정책 (가드 / 예산 / 콘텐츠) 통과 여부 사전 검증.
    s05_policy guards 와 동일 검증 로직 재사용 — registry 의 Guard 인스턴스에
    같은 인터페이스로 위임.

    호출 예:
      check_policy(action="mcp_database_loader", args={"connection": "prod_postgres"})
        → {"allowed": true, "reasons": []}  또는  {"allowed": false, "reasons": ["..."]}
    """

    def __init__(self, state_ref):
        self._state = state_ref

    @property
    def name(self) -> str:
        return "check_policy"

    @property
    def description(self) -> str:
        return (
            "Self-check: 호출하려는 action 이 활성 정책 (가드 / 예산 / 콘텐츠) "
            "통과하는지 사전 검증. 민감 도구 호출 전 (e.g. 외부 API / DB write) "
            "정책 위반 risk 확인용."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "호출하려는 도구 이름"},
                "args": {"type": "object", "description": "호출 인자 (정책 검증용)"},
            },
            "required": ["action"],
        }

    @property
    def category(self) -> str:
        return "policy"

    @property
    def read_only_hint(self) -> bool:
        return True

    @property
    def idempotent_hint(self) -> bool:
        return True

    @property
    def open_world_hint(self) -> bool:
        return False

    async def execute(self, input_data: dict) -> ToolResult:
        action = input_data.get("action", "")
        args = input_data.get("args") or {}

        # state.config 의 stage_params.s05_policy.guards 받아 PRE_TOOL hook 시뮬레이션.
        # Guard 의 진짜 인터페이스 = check(state, context) → GuardResult.
        # PRE_TOOL hook 지원하는 Guard 만 평가 (TokenBudget / Iteration LOOP_BOUNDARY 만 = skip).
        import logging as _logging
        _log = _logging.getLogger("harness.tools.check_policy")

        config = getattr(self._state, "config", None)
        if not config or not hasattr(config, "stage_params"):
            return ToolResult.success(
                "no policy configured", allowed=True, reasons=[],
            )
        guards_cfg = (config.stage_params or {}).get("s05_policy", {}).get("guards") or []
        guard_names = [g.get("name") for g in guards_cfg if isinstance(g, dict) and g.get("name")]
        if not guard_names:
            return ToolResult.success(
                "no guards active", allowed=True, reasons=[],
            )

        try:
            from ..stages.strategies.guard import (
                _GUARD_REGISTRY, GuardChain, HookPoint,
            )
        except Exception as e:
            return ToolResult.success(
                f"guard registry unavailable: {e}",
                allowed=True, reasons=[],
            )

        # Guard 인스턴스 빌드 — params 박음 + configure() 호출
        instances = []
        for g in guards_cfg:
            if not isinstance(g, dict):
                continue
            gname = g.get("name")
            if not gname:
                continue
            cls = _GUARD_REGISTRY.get(gname)
            if not cls:
                continue
            try:
                params = g.get("params") or {}
                # 빌트인 Guard 들은 keyword args 또는 빈 ctor — 양쪽 시도
                try:
                    inst = cls(**params) if isinstance(params, dict) and params else cls()
                except TypeError:
                    inst = cls()
                # Strategy.configure() 표준 hook
                if hasattr(inst, "configure"):
                    try:
                        inst.configure(params if isinstance(params, dict) else {})
                    except Exception:
                        pass
                instances.append(inst)
            except Exception as e:
                _log.warning("guard %s instantiate failed: %s", gname, e)
                continue

        if not instances:
            return ToolResult.success(
                "no instantiable guards", allowed=True, reasons=[],
                active_guards=guard_names,
            )

        # PRE_TOOL hook + pending_tool_call 빌드 → GuardChain.invoke (모든 결과 수집)
        chain = GuardChain(instances)
        pending_tool_call = {"name": action, "input": args}
        try:
            results = chain.invoke(
                HookPoint.PRE_TOOL, self._state,
                pending_tool_call=pending_tool_call,
                short_circuit=False,  # 모든 reason 수집
            )
        except Exception as e:
            return ToolResult.success(
                f"guard chain invoke failed: {e}",
                allowed=True, reasons=[],
                active_guards=guard_names,
            )

        reasons: list[str] = []
        for r in results:
            if not r.passed and r.severity == "block":
                reasons.append(f"{r.guard_name}: {r.reason or 'blocked'}")

        return ToolResult.success(
            f"policy check: {len(results)} guard(s) evaluated at PRE_TOOL hook",
            allowed=not reasons,
            reasons=reasons,
            active_guards=guard_names,
            evaluated=len(results),
        )


class DiscoverPromptTool(Tool):
    """v1.6 — Prompt template lazy load 빌트인.

    s03_prompt 의 DEFAULT_IDENTITIES / DEFAULT_RULES / THINKING_MODE_TEMPLATES 외
    register_*() / entry_points 로 등록된 외부 prompt template 의 본문 + 메타를
    LLM 이 lazy load 가능. progressive_3level 의 prompt isomorphic.

    호출 예:
      discover_prompt(template_type="identity", name="legal_advisor")
        → {"name": ..., "template": "..."}
    """

    @property
    def name(self) -> str:
        return "discover_prompt"

    @property
    def description(self) -> str:
        return (
            "외부 등록 prompt template (identity / rules / thinking_mode) 의 본문 + "
            "메타 lazy load. name 미박으면 등록된 모든 template 의 list 반환."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "template_type": {
                    "type": "string",
                    "enum": ["identity", "rules", "thinking_mode"],
                    "description": "어떤 종류의 template",
                },
                "name": {"type": "string", "description": "template 이름. 비워두면 list."},
            },
            "required": ["template_type"],
        }

    @property
    def category(self) -> str:
        return "prompt"

    @property
    def read_only_hint(self) -> bool:
        return True

    @property
    def idempotent_hint(self) -> bool:
        return True

    @property
    def open_world_hint(self) -> bool:
        return False

    async def execute(self, input_data: dict) -> ToolResult:
        ttype = (input_data.get("template_type") or "").strip()
        name = (input_data.get("name") or "").strip()

        try:
            from ..stages.s03_prompt.stage import (
                DEFAULT_IDENTITIES, DEFAULT_RULES, THINKING_MODE_TEMPLATES,
                _discover_prompt_templates_from_entry_points,
            )
            _discover_prompt_templates_from_entry_points()
        except Exception as e:
            return ToolResult.error(f"prompt registry import failed: {e}")

        registry_map = {
            "identity": DEFAULT_IDENTITIES,
            "rules": DEFAULT_RULES,
            "thinking_mode": THINKING_MODE_TEMPLATES,
        }
        reg = registry_map.get(ttype)
        if reg is None:
            return ToolResult.error(
                f"unknown template_type: {ttype}. "
                f"valid: {list(registry_map.keys())}"
            )

        if not name:
            # v1.7.5 — list 모드 정보량 보강. 이름만 X → name + description (본문 첫 줄,
            # 120자) + length. system_prompt 의 <available_prompt_templates> 섹션과
            # isomorphic — LLM 이 "어떤 게 적합한지" 자율 판단할 정보 박음.
            entries = []
            for k, v in reg.items():
                first_line = ""
                if isinstance(v, str):
                    s = v.strip()
                    if s:
                        first_line = s.split("\n", 1)[0][:120]
                entries.append({
                    "name": k,
                    "description": first_line,
                    "length": len(v) if isinstance(v, str) else 0,
                })
            return ToolResult.success(
                f"{len(reg)} {ttype} templates — discover_prompt(template_type='{ttype}', "
                f"name=...) 로 본문 fetch",
                templates=entries,
            )

        if name not in reg:
            return ToolResult.error(
                f"template not found: {ttype}/{name}. "
                f"available: {list(reg.keys())}"
            )

        return ToolResult.success(
            f"loaded {ttype}/{name}",
            name=name,
            template_type=ttype,
            content=reg[name],
        )


class DiscoverCollectionTool(Tool):
    """v1.6 — RAG 컬렉션 sample documents / metadata lazy fetch 빌트인.

    progressive_4level 의 컬렉션 isomorphic — 도구의 progressive_3level 정신을
    자원 (컬렉션) 에 그대로:
    - L1: <reference_resources> 의 메타 (name + description + total_documents)
    - L2: discover_collection(name) → sample documents + 통계 (이 도구!)
    - L3: rag_search(collection, query) → 인덱스 + snippet
    - L4: fetch_pd(kind='rag', id=...) → 본문

    호출 예:
      discover_collection(name="assort", sample_size=3)
        → {"name": ..., "description": ..., "total": ..., "samples": [...]}
    """

    def __init__(self, state_ref):
        self._state = state_ref

    @property
    def name(self) -> str:
        return "discover_collection"

    @property
    def description(self) -> str:
        return (
            "Lazy load: RAG 컬렉션의 sample documents + 통계 메타. <reference_resources> "
            "의 메타가 부족하면 이 도구로 더 깊이 — sample 보고 적합도 판단 후 rag_search 호출."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "컬렉션 이름"},
                "sample_size": {
                    "type": "integer",
                    "description": "sample 개수 (default 3, max 10)",
                    "default": 3,
                },
            },
            "required": ["name"],
        }

    @property
    def category(self) -> str:
        return "retrieval"

    @property
    def read_only_hint(self) -> bool:
        return True

    @property
    def idempotent_hint(self) -> bool:
        return True

    @property
    def open_world_hint(self) -> bool:
        return False

    async def execute(self, input_data: dict) -> ToolResult:
        name = (input_data.get("name") or "").strip()
        if not name:
            return ToolResult.error("name 필수")
        sample_size = max(1, min(int(input_data.get("sample_size") or 3), 10))

        services = self._state.metadata.get("services") if hasattr(self._state, "metadata") else None
        doc_service = getattr(services, "documents", None) if services else None
        if not doc_service:
            return ToolResult.error("DocumentService 미주입")

        # 메타 cache 우선
        meta = (self._state.metadata.get("rag_collections_meta") or {}).get(name) if hasattr(self._state, "metadata") else None
        result_meta = dict(meta) if meta else {"name": name}
        result_meta["name"] = name

        # sample 검색 — 빈 query 또는 generic word 로 top_k=sample_size
        try:
            samples = await doc_service.search(
                "", name, limit=sample_size, score_threshold=0.0,
            ) or []
        except Exception as e:
            return ToolResult.success(
                f"sample fetch failed but meta returned",
                meta=result_meta,
                samples_error=str(e)[:120],
            )

        sample_summaries = []
        for r in samples[:sample_size]:
            if not isinstance(r, dict):
                continue
            text = r.get("chunk_text") or r.get("text") or ""
            sample_summaries.append({
                "source": r.get("file_name") or r.get("source", ""),
                "preview": text[:200],
            })

        return ToolResult.success(
            f"collection {name}: {len(sample_summaries)} samples",
            meta=result_meta,
            samples=sample_summaries,
        )


# ────────────────────────────────────────────────────────────────────────────
# v1.6 — collection description enricher registry (default OFF)
# ────────────────────────────────────────────────────────────────────────────

_COLLECTION_ENRICHERS: list = []


def register_collection_enricher(fn):
    """컬렉션 description 빈 칸일 때 자동 생성하는 enricher 등록.

    fn 시그니처: async def enrich(name: str, sample_docs: list[str]) -> str | None
    여러 enricher 등록 가능 — 첫 비빈 결과 반환.

    entry_points group: ``xgen_harness.collection_enrichers``
    default OFF — 사용자가 명시 ON 시만 발동 (config.enrich_empty_descriptions=True
    또는 register 호출).
    """
    if fn not in _COLLECTION_ENRICHERS:
        _COLLECTION_ENRICHERS.append(fn)


async def enrich_collection_description(name: str, sample_docs: list[str]) -> str | None:
    """등록된 enricher 들 순서대로 호출. 첫 비빈 description 반환."""
    for fn in _COLLECTION_ENRICHERS:
        try:
            result = fn(name, sample_docs)
            if hasattr(result, "__await__"):
                result = await result
            if isinstance(result, str) and result.strip():
                return result.strip()
        except Exception:
            continue
    return None
