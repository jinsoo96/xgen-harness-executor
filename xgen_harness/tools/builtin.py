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


# v0.26.19 — 한국어 query ↔ 영문 도구명/description 의 cross-language 매칭
# 라이브 결함: 사용자가 "네이버 뉴스" 검색 → search_tools 가 영문 도구명
# (mcp_naver_news_mcp 등) 4개 후보 다 매치 실패 → "No tools matched" → LLM 포기.
# 한국어 일반 도메인 단어를 영문 alias 로 보강해 추가 매칭 라운드 제공.
# 외부 기여자가 도메인 alias 추가 가능하도록 dict 노출 — 신규 키 등록 = 한 줄.
_BUILTIN_KO_EN_ALIASES: dict[str, list[str]] = {
    "네이버": ["naver"],
    "다음": ["daum"],
    "구글": ["google"],
    "유튜브": ["youtube", "yt"],
    "뉴스": ["news"],
    "검색": ["search"],
    "쇼핑": ["shopping", "shop"],
    "지도": ["map", "maps"],
    "메일": ["mail", "email", "gmail"],
    "캘린더": ["calendar"],
    "달력": ["calendar"],
    "날씨": ["weather"],
    "시간": ["time", "clock"],
    "번역": ["translate", "translation"],
    "이미지": ["image", "img"],
    "사진": ["image", "photo"],
    "동영상": ["video"],
    "비디오": ["video"],
    "음성": ["audio", "voice", "speech"],
    "파일": ["file", "files"],
    "폴더": ["folder", "dir", "directory"],
    "데이터": ["data"],
    "데이터베이스": ["db", "database"],
    "디비": ["db", "database"],
    "주문": ["order"],
    "결제": ["payment", "pay"],
    "고객": ["customer", "client"],
    "회원": ["user", "member"],
    "재고": ["inventory", "stock"],
    "분석": ["analysis", "analytics", "analyze"],
    "통계": ["stats", "statistics"],
    "보고서": ["report"],
    "리포트": ["report"],
    "차트": ["chart"],
    "그래프": ["graph"],
    "트렌드": ["trend", "trends"],
    "알림": ["notify", "notification", "alert"],
    "메시지": ["message", "msg"],
    "채팅": ["chat"],
    "대화": ["chat", "conversation"],
}


def register_search_alias(ko: str, en_terms: list[str]) -> None:
    """search_tools 한국어→영문 alias 외부 등록 (확장 지점).

    예: 도메인 특화 단어 추가 — register_search_alias("물류", ["logistics", "shipping"]).
    이미 등록된 ko 키는 덮어씀. 외부 패키지 / 호스트 코드에서 사용.
    """
    _BUILTIN_KO_EN_ALIASES[ko] = list(en_terms)


def _expand_query_terms(terms: list[str]) -> list[str]:
    """한국어 term 을 영문 alias 로 확장. 영문 term 은 그대로.

    "네이버" → ["네이버", "naver"]
    "naver" → ["naver"]
    """
    expanded: list[str] = []
    seen: set[str] = set()
    for t in terms:
        if t in seen:
            continue
        expanded.append(t)
        seen.add(t)
        for alias in _BUILTIN_KO_EN_ALIASES.get(t, []):
            if alias not in seen:
                expanded.append(alias)
                seen.add(alias)
    return expanded


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
        lines.append("\nNext: discover_tools(tool_name=...) for full schema.")
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
            "or discover_tools() for full list, or pick a tool above and call discover_tools(tool_name=...) "
            "for schema."
        )
        return "\n".join(lines)


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
