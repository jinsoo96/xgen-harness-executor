"""
스테이지별 설정 스키마 — UI에서 각 스테이지 클릭 시 보여줄 설정 항목

각 스테이지마다:
- 설명 (한국어/영어)
- 설정 가능한 필드 (type, label, options, default)
- 기술적 동작 설명
"""

import logging
from typing import Optional

_sc_logger = logging.getLogger("harness.core.stage_config")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Stage ID Alias — v0.11.0 리네이밍 하위호환 레이어
# 구 저장 워크플로우 / 외부 갤러리 wheel 이 구 id 를 보내와도 내부에서 새 id 로 변환.
# Phase 1 (v0.11.x): 양쪽 다 수용. Phase 2 (v0.12+): 구 id 경고 → 제거.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STAGE_ID_ALIASES: dict[str, str] = {
    "s02_memory":        "s02_history",
    "s03_system_prompt": "s03_prompt",
    "s04_tool_index":    "s04_tool",
    "s05_plan":          "s05_strategy",
    # v0.14.0 — s07_llm 삭제 + 번호 시프트 하위호환
    "s07_llm":           "s00_harness",   # 본문 LLM 호출은 s00 이 소유
    "s08_execute":       "s07_act",
    "s08_act":           "s07_act",
    "s09_validate":      "s08_judge",
    "s09_judge":         "s08_judge",
    "s10_decide":        "s09_decide",
    "s11_save":          "s10_save",
    "s12_finalize":      "s11_finalize",
    "s12_complete":      "s11_finalize",
}


def canonical_stage_id(sid: str) -> str:
    """구 stage_id 가 들어와도 새 id 로 정규화. 이미 새 id 면 그대로."""
    if not isinstance(sid, str):
        return sid
    return STAGE_ID_ALIASES.get(sid, sid)


def canonical_stage_id_map(d: dict) -> dict:
    """dict 의 key 를 stage_id 로 간주하고 canonical 로 정규화한 새 dict 반환.

    구 id / 새 id 가 충돌하면 새 id 값이 유지 (나중 병합 승리).
    """
    if not isinstance(d, dict):
        return d
    out: dict = {}
    for k, v in d.items():
        canonical = canonical_stage_id(k) if isinstance(k, str) else k
        out[canonical] = v
    return out


STAGE_CONFIGS: dict[str, dict] = {
    "s00_harness": {
        "description_ko": "전체 흐름을 지휘합니다. 어떤 Stage 를 어떤 순서로 돌릴지 LLM 이 직접 정하고 본문 응답까지 책임집니다.",
        "description_en": "Conducts the whole pipeline. The LLM itself decides which stages run in what order and produces the main response.",
        "when_to_use": "기본 활성. LLM 이 카탈로그 보고 Stage/Strategy/파라미터 자율 조립.",
        "when_to_skip": "harness_mode='off' 일 때 skip (단순 파이프라인 모드).",
        "cost_hint": "medium",
        "icon": "🎯",
        "fields": [
            {
                # v0.15.1 자동 연동 — options 리터럴 제거. options_source 로
                # HarnessStage.list_strategies() 결과를 동적 주입 (StrategyResolver
                # _REGISTRY 에서 실측). 외부 플러그인이 새 Transport 등록하면 즉시 UI
                # 드롭다운에 합류.
                "id": "strategy",
                "label": "Transport Strategy",
                "type": "select",
                "options_source": "s00_harness_transport_strategies",
                "default": "streaming",
                "description": "본문 LLM 호출 방식. 등록된 Transport Strategy 중 선택.",
            },
            {
                "id": "max_tokens",
                "label": "최대 출력 토큰",
                "type": "number",
                "min": 256,
                "max": 32768,
                "step": 256,
                "default": 8192,
            },
            {
                "id": "thinking_enabled",
                "label": "Extended Thinking",
                "type": "toggle",
                "default": False,
            },
            {
                "id": "thinking_budget",
                "label": "Thinking 토큰 예산",
                "type": "number",
                "min": 1000,
                "max": 100000,
                "step": 1000,
                "default": 10000,
                "depends_on": "thinking_enabled",
            },
        ],
        "behavior": [
            "카탈로그 자동 수집 (Stage/Capability/Tool/Resource)",
            "LLM 에게 submit_plan 도구 하나 노출 → 구조화된 Plan 수신",
            "Plan.params/strategies 를 state.config 에 병합 후 Pipeline 에 넘김",
            "Phase B 루프 안에서 main_call() 로 본문 LLM 호출 (streaming/batch)",
            "httpx SSE 스트리밍, 재시도 (429 → 10/20/40초, 529 → 1/2/4초)",
            "모델 폴백 (Anthropic → OpenAI), Prompt Caching",
            "PlanningEvent 방출로 프론트가 '왜 이 조합인지' 카드 렌더",
        ],
    },
    "s01_input": {
        # v0.14.0 s01: **사용자 입력 정규화 전용**. LLM provider/model/temperature
        # 선택은 s00_harness 통제탑 관할 — s01 에서 필드로 노출하지 않음.
        "description_ko": "사용자 입력을 검증하고 첨부 파일을 content block 으로 정규화합니다.",
        "description_en": "Validates user input and normalizes attached files to content blocks.",
        # v0.12.0 self-describing — Planner 가 이 세 필드만 보고 선택/제외/파라미터 조정.
        # 시스템 프롬프트 가이드 대신 "환경이 스스로 말한다" (REAL_HARNESS).
        "when_to_use": "항상 (필수). 빈 입력 거부 + 첨부 파일 정규화 + 첫 user 메시지 push.",
        "when_to_skip": "불가 (REQUIRED_STAGES).",
        "cost_hint": "low",
        "icon": "📥",
        # v0.26.0 — provider 필드 제거 (D1).
        # 이전엔 stage_param 으로 노출했으나 stage.py 자기 docstring 에 "s01 은 읽지도
        # 쓰지도 않는다" 라고 명시. provider 결정은 HarnessConfig top-level (ConfigPanel)
        # 가 단일 진실 소스. 두 곳 노출은 사용자 거짓말 (UI 클릭 → 환경 무반영).
        "fields": [],
        "behavior": [
            "빈 입력 거부",
            "파일 첨부: base64 이미지 · 텍스트 블록 변환",
            "첫 user 메시지를 파이프라인에 push (단일 책임)",
        ],
    },
    "s02_history": {
        "description_ko": "이전 대화 이력, 실행 결과, 관련 문서를 로드합니다.",
        "when_to_use": "멀티턴 대화 / 이전 실행 결과 재사용 / 관련 문서 상기 필요할 때.",
        "when_to_skip": "단일 턴 stateless 요청, 첫 대화, 이력 무관한 단발성 작업.",
        "cost_hint": "medium",
        "description_en": "Loads conversation history, results, and related documents.",
        "icon": "🧠",
        "fields": [
            {
                "id": "max_history",
                "label": "최대 이력 수",
                "type": "number",
                "min": 1,
                "max": 20,
                "default": 5,
            },
            # v0.26.0 — memory_source 필드 제거 (D2).
            # stage.py 가 이 필드를 한 번도 read 하지 않음 (grep 0 hit). 실제 동작은
            # strategy 분기 (default vs embedding_search) 와 ServiceProvider.documents
            # 주입 여부로만 결정. 빈 multi_select 가 사용자 거짓말이었음.
        ],
        "behavior": [
            "DB에서 최근 N개 실행 결과 조회",
            "harness_execution_log → execution_io → chat_session 폴백",
            "각 결과 2K자로 제한",
            "문서 소스: Documents 서비스 임베딩 검색 (선택 시)",
        ],
    },
    "s03_prompt": {
        "description_ko": "시스템 프롬프트를 조립합니다. 에이전트의 역할과 행동 규칙을 정의합니다.",
        "when_to_use": "시스템 프롬프트·인용 규칙·도구 힌트 조립이 필요한 일반 요청.",
        "when_to_skip": "state.system_prompt 가 이미 완성되어 있고 추가 조립 불필요할 때.",
        "cost_hint": "low",
        "description_en": "Assembles system prompt defining agent role and rules.",
        "icon": "📝",
        "fields": [
            {
                "id": "system_prompt",
                "label": "시스템 프롬프트",
                "type": "textarea",
                "placeholder": "에이전트의 역할을 정의하세요...",
                "default": "",
            },
            # v0.11.23 — prompt store / my-prompts 선택. 이전에 이식에 stage_id 수동 매핑.
            {
                "id": "prompt_id",
                "label": "Prompt Store / My Prompts",
                "type": "select",
                "options_source": "prompt-store",
                "default": "",
                "description": "저장된 프롬프트 템플릿을 선택하면 system_prompt 대신 사용. 비워두면 system_prompt 그대로.",
            },
            {
                "id": "include_rules",
                "label": "기본 규칙 포함",
                "type": "toggle",
                "default": True,
            },
            {
                "id": "citation_mode",
                "label": "인용 모드",
                "type": "select",
                "options": ["off", "enabled", "strict", "auto"],
                "default": "off",
                "description": "off: 인용 지시 없음 / enabled: [DOC_n] 인용 권장 / strict: 참조 문서 밖 정보 답변 금지 (환각 방지) / auto: RAG context 패턴 감지 후 자동 strict/off (v0.11.17+ 실험적)",
            },
            {
                "id": "citation_auto_doc_tokens",
                "label": "인용 auto — 문서형 토큰 (override)",
                "type": "multi_select",
                "options": [],
                "default": [],
                "description": "citation_mode=auto 휴리스틱에 추가할 문서형 collection 토큰. 기본값(doc/report/regulation/...) 에 OR 결합. 회사·언어 특화 명사 주입용.",
            },
            {
                "id": "citation_auto_prod_tokens",
                "label": "인용 auto — 상품형 토큰 (override)",
                "type": "multi_select",
                "options": [],
                "default": [],
                "description": "citation_mode=auto 휴리스틱에 추가할 상품형 collection 토큰. 기본값(product/stock/inventory/...) 에 OR 결합.",
            },
        ],
        "behavior": [
            "섹션 우선순위: Identity → Rules → Tools → RAG → History",
            "컨텍스트 압축 시 낮은 우선순위부터 제거",
            "strict 모드: 제공 문서 밖 정보는 'not available' 로 응답",
        ],
    },
    "s04_tool": {
        "description_ko": "에이전트가 사용할 도구를 고릅니다. MCP · Custom API · xgen 노드 등 모든 도구가 한 경로로 합류합니다.",
        "when_to_use": "외부 도구·RAG 컬렉션·capability 중 하나 이상 필요. 도구 공급 = ToolSource 단일 채널.",
        "when_to_skip": "LLM 내재 지식만으로 충분한 잡담·단순 QA·창작.",
        "cost_hint": "low",
        "description_en": "Picks the tools the agent will use. MCP, Custom API, xgen nodes, etc. all join through a single channel.",
        "icon": "🔧",
        "fields": [
            # v0.25.0 — 단일 도구 공급 경로. source_id → 허용 도구 이름 리스트.
            #   키 없음 = 해당 소스 전체 포함.
            #   빈 리스트 = 소스 비활성.
            #   이름 리스트 = 그 도구만 포함.
            # 프론트가 /api/harness/tool-sources 응답으로 Box 를 동적 렌더한 뒤,
            # 사용자가 Box 안 체크박스를 켠 도구 이름만 채워 준다.
            {
                "id": "selected_tools",
                "label": "Selected Tools (by source)",
                "type": "object",
                "default": {},
                "description": "source_id → 허용 도구 이름 리스트. 키 없음=소스 전체, 빈 리스트=소스 비활성.",
            },
            # 각 ToolSource 의 filter_schema 에 따라 프론트가 sub-UI 를 렌더하고,
            # 사용자가 선택한 값이 여기 저장된다. 예:
            #   {"mcp-sessions": {"session_ids": ["abc"]},
            #    "xgen-nodes":   {"tags": ["api", "database"]}}
            {
                "id": "tool_source_filters",
                "label": "Tool Source Filters",
                "type": "object",
                "default": {},
                "description": "소스별 list_tools 필터 파라미터 맵.",
            },
            {
                "id": "rag_collections",
                "label": "문서 컬렉션 (RAG)",
                "type": "multi_select",
                "options_source": "rag_collections",
                "default": [],
                "description": "검색할 문서 컬렉션을 선택하세요",
            },
            {
                "id": "rag_top_k",
                "label": "검색 결과 수 (Top-K)",
                "type": "number",
                "min": 1,
                "max": 20,
                "default": 4,
            },
            {
                "id": "builtin_tools",
                "label": "빌트인 도구",
                "type": "multi_select",
                "options": ["discover_tools", "calculator", "web_search"],
                "default": ["discover_tools"],
            },
            {
                "id": "force_tool_use",
                "label": "도구 호출 강제 (v0.11.19+)",
                "type": "toggle",
                "default": False,
                "description": "활성 시 LLM 이 반드시 tool 하나를 호출하게 강제 (OpenAI tool_choice=required, Anthropic type=any). tool_result 누적 → L3 microcompact 발동 조건.",
            },
            {
                "id": "capabilities",
                "label": "Capabilities",
                "type": "multi_select",
                "options_source": "capabilities",
                "default": [],
                "description": "Capability 카탈로그 선택 — 도구 자동 바인딩.",
            },
        ],
        "behavior": [
            "단일 공급 채널: 모든 도구는 ToolSource.list_tools() 로 수집",
            "Level 1: 도구 메타데이터만 프롬프트에 (~40 tokens/tool)",
            "Level 2: discover_tools로 상세 스키마 조회",
            "Level 3: 실제 도구 실행 (s07_act)",
            "RAG: Documents API로 벡터 검색 → 시스템 프롬프트에 주입",
            "확장: 외부 패키지가 entry_points(xgen_harness.tool_sources) 로 자기 소스 등록",
        ],
    },
    "s05_strategy": {
        "description_ko": "응답 전략을 수립합니다. CoT · ReAct · capability planner 중 한 가지로 LLM 이 단계별 추론하도록 유도합니다.",
        "when_to_use": "복잡한 추론 · 멀티에이전트 DAG · 단계별 계획이 이득이 될 때.",
        "when_to_skip": "단일 패스 직답형 요청 (인사·짧은 QA·단순 조회).",
        "cost_hint": "low",
        "description_en": "Sets the response strategy. Picks one of CoT / ReAct / capability planner so the LLM reasons step-by-step.",
        "icon": "📋",
        "fields": [
            {
                "id": "planning_mode",
                "label": "계획 모드",
                "type": "select",
                "options": ["cot", "react", "none"],
                "default": "cot",
            },
            {
                "id": "intent_rules",
                "label": "Intent Routing 규칙 (JSON)",
                "type": "textarea",
                "placeholder": '예: [{"keywords":["상품","product"],"filter":{"file_name":"products.csv"}}]',
                "default": "",
                "description": "쿼리 키워드 → metadata_filter 자동 매핑 규칙. 매칭되면 s06 이 auto_metadata_filter 로 사용 (명시 filter 없을 때). JSON 배열 문자열",
            },
        ],
        "behavior": [
            "첫 번째 루프에서만 실행",
            "시스템 프롬프트에 계획 지시 추가",
            "Intent Routing: 쿼리 의도 → auto_metadata_filter 자동 생성 (s06 에 전달)",
        ],
    },
    "s06_context": {
        "description_ko": "RAG · DB · 문서 · GraphRAG 같은 지식 리소스를 검색해 답변 직전 컨텍스트로 주입합니다. 토큰 예산을 넘으면 자동으로 압축합니다.",
        "when_to_use": "RAG 컨텍스트 주입 · 긴 대화 압축 · Progressive Disclosure (5-Level cascade) 가 필요할 때. s04_tool 이 RAG/문서 리소스를 고르면 거의 필수.",
        "when_to_skip": "짧은 단일 메시지 + 컨텍스트 자료 없음.",
        "cost_hint": "medium",
        "description_en": "Searches knowledge resources (RAG, DB, documents, GraphRAG) and injects them as context before the answer. Auto-compacts when over the token budget.",
        "icon": "📊",
        "fields": [
            {
                "id": "context_window",
                "label": "컨텍스트 윈도우",
                "type": "number",
                "min": 10000,
                "max": 1000000,
                "step": 10000,
                "default": 200000,
            },
            {
                "id": "compaction_threshold",
                "label": "압축 시작 (% 사용)",
                "type": "slider",
                "min": 50,
                "max": 95,
                "step": 5,
                "default": 80,
            },
            {
                "id": "score_threshold",
                "label": "RAG 유사도 임계값",
                "type": "slider",
                "min": 0,
                "max": 1,
                "step": 0.05,
                "default": 0.0,
                "description": "0 이면 필터링 없음. 임계 이상 점수의 검색 결과만 컨텍스트에 포함 (precision 도구)",
            },
            {
                "id": "rerank_top_k",
                "label": "리랭크 상위 K",
                "type": "number",
                "min": 1,
                "max": 20,
                "default": 4,
                "description": "reranker 가 활성일 때 재정렬 후 유지할 상위 청크 수 (미설정 시 rag_top_k 사용)",
            },
            {
                "id": "reranker",
                "label": "리랭커 활성",
                "type": "toggle",
                "default": False,
                "description": "켜면 xgen-documents 리랭커로 검색 결과 재정렬. provider 는 서버 기동 시 설정된 값 사용",
            },
            # v0.11.23 — 이식 options_source 5종 공식 선언. 이전에 harness_options_registry 수동 매핑.
            {
                "id": "ontology_collections",
                "label": "Ontology / GraphRAG",
                "type": "multi_select",
                "options_source": "ontology-collections",
                "default": [],
                "description": "GraphRAG / ontology 검색용 컬렉션.",
            },
            {
                "id": "folders",
                "label": "Folders (컬렉션 그룹)",
                "type": "multi_select",
                "options_source": "folders",
                "default": [],
                "description": "스토리지 폴더 선택 — 폴더 안 컬렉션 자동 확장.",
            },
            # v0.26.1 — files 필드 부활 (실 wiring 추가).
            # v0.26.0 에선 dead 라 제거했었음. 이제 stage.py 가 이 필드를 read 해서
            # metadata_filter 의 `file_name` 키로 자동 라우팅 (union with 사용자 textarea).
            # frontend 의 files multi_select UI 가 진짜 검색 범위 좁히기로 작동.
            {
                "id": "files",
                "label": "Files (업로드 파일)",
                "type": "multi_select",
                "options_source": "files",
                "default": [],
                "description": "업로드된 파일 개별 선택 — metadata_filter.file_name 으로 자동 라우팅되어 검색 범위 제한.",
            },
            {
                "id": "db_connections",
                "label": "DB Connections",
                "type": "multi_select",
                "options_source": "db-connections",
                "default": [],
                "description": "DB 연결 선택 — 스키마 요약이 system_prompt 에 주입.",
            },
            {
                "id": "enhance_prompt",
                "label": "응답 향상 프롬프트",
                "type": "textarea",
                "placeholder": "예: '가장 최신 데이터를 우선하여 요약하라' — RAG 컨텍스트 뒤에 덧붙여집니다",
                "default": "",
                "description": "RAG 컨텍스트 주입 후 이어 붙는 사용자 지정 지시. 비우면 적용 안함",
            },
            {
                "id": "metadata_filter",
                "label": "메타데이터 필터 (JSON)",
                "type": "textarea",
                "placeholder": '예: {"file_name": "products.csv"}  ← 해당 파일 청크만 검색',
                "default": "",
                "description": "DocumentSearchRequest.filter 로 전달. 특정 파일/폴더로 검색 범위를 좁혀 recall 향상. JSON 객체 문자열",
            },
            {
                "id": "rag_pd_mode",
                "label": "RAG Progressive Disclosure",
                "type": "select",
                "options": ["eager", "progressive"],
                "default": "eager",
                "description": "eager: 청크 본문 전체를 system_prompt 에 주입 (기존). progressive: 인덱스 한 줄 + snippet 만 주입, 본문은 pd_stores 에 보관 — LLM 이 fetch_pd(kind='rag', id=...) 로 필요한 것만 pull (Claude Code 패턴)",
            },
            {
                "id": "rag_ingestion_mode",
                "label": "RAG 주입 방식 (v0.11.18+)",
                "type": "select",
                "options": ["system_prompt", "tool_only", "both"],
                "default": "system_prompt",
                "description": "system_prompt (기본): RAG 를 system prompt 에 즉시 주입 / tool_only: system prompt 주입 skip, LLM 은 rag_search 도구로만 접근 → tool_result 누적 → L3 microcompact 발동 조건 / both: 둘 다. rag_tool_mode=tool 이면 자동 tool_only 전환.",
            },
            {
                "id": "chars_per_token",
                "label": "토큰 당 문자 수 (언어별 조정, v0.11.20+)",
                "type": "number",
                "min": 1,
                "max": 10,
                "default": 3,
                "description": "estimated_tokens = total_chars / chars_per_token. 기본 3 은 혼합 언어 평균. 영어 위주면 4, 한국어 위주면 2 권장.",
            },
            {
                "id": "rag_pd_snippet_size",
                "label": "RAG PD snippet 크기 (자)",
                "type": "number",
                "min": 40,
                "max": 500,
                "step": 20,
                "default": 120,
                "description": "progressive 모드에서 인덱스 한 줄에 노출할 청크 앞부분 미리보기 크기",
            },
            {
                "id": "strategy",
                "label": "압축 전략",
                "type": "select",
                "options": ["token_budget", "sliding_window", "microcompact", "context_collapse_overlay", "autocompact_llm", "cascade"],
                "default": "token_budget",
                "description": "token_budget: 기본 파괴적 3단계 / sliding_window: 최근 N 개 / microcompact (L3): 오래된 tool_result 만 placeholder (Claude Code L3) / context_collapse_overlay (L4): 비파괴 overlay (Claude Code L4) / autocompact_llm (L5): child LLM 9-section summary (Claude Code L5) / cascade: 임계별 L3→L4→L5 자동 선택 (Claude Code Cascade)",
            },
            {
                "id": "cascade_l3_threshold",
                "label": "Cascade L3 발동 임계 (%)",
                "type": "slider",
                "min": 50,
                "max": 90,
                "step": 5,
                "default": 80,
                "description": "cascade 전략에서 이 비율 이상이면 L3 microcompact 먼저 시도 (tool_result 교체, 경량). v0.11.16 이후 기본 80 — Pilot #11 에서 조기 발동(70) 의 -19% 품질 악화 관측",
            },
            {
                "id": "cascade_l4_threshold",
                "label": "Cascade L4 발동 임계 (%)",
                "type": "slider",
                "min": 60,
                "max": 95,
                "step": 5,
                "default": 90,
                "description": "cascade 전략에서 이 비율 이상이면 L4 context_collapse_overlay 추가 발동 (비파괴 overlay, 중량). v0.11.16 기본 90",
            },
            {
                "id": "cascade_l5_threshold",
                "label": "Cascade L5 발동 임계 (%)",
                "type": "slider",
                "min": 70,
                "max": 99,
                "step": 1,
                "default": 97,
                "description": "cascade 전략에서 이 비율 이상이면 최후 수단 L5 autocompact_llm 발동 (child LLM 요약). v0.11.16 기본 97",
            },
            {
                "id": "context_collapse_threshold",
                "label": "L4 Collapse 임계 (%)",
                "type": "slider",
                "min": 50,
                "max": 95,
                "step": 5,
                "default": 90,
                "description": "context_collapse_overlay 전략에서 이 비율 이상 토큰 사용 시 collapse 발동 (Claude Code 기본 90%)",
            },
            {
                "id": "context_collapse_keep_tail",
                "label": "L4 보존 tail 메시지 수",
                "type": "number",
                "min": 1,
                "max": 10,
                "default": 3,
                "description": "collapse 후 messages 말미에 온전히 남길 최근 메시지 개수",
            },
            {
                "id": "microcompact_threshold",
                "label": "L3 Microcompact 임계 (%)",
                "type": "slider",
                "min": 50,
                "max": 95,
                "step": 5,
                "default": 75,
                "description": "microcompact 전략에서 이 비율 이상 토큰 사용 시 오래된 tool_result 를 placeholder 로 교체 (원본은 pd_stores['tool_result'] 에서 복원 가능)",
            },
            {
                "id": "microcompact_keep_recent",
                "label": "L3 유지 tool_result 개수",
                "type": "number",
                "min": 1,
                "max": 20,
                "default": 5,
                "description": "messages 내 최근 N 개 tool_result 만 원본 유지, 나머지는 placeholder",
            },
            {
                "id": "autocompact_threshold",
                "label": "L5 Autocompact 임계 (%)",
                "type": "slider",
                "min": 50,
                "max": 95,
                "step": 5,
                "default": 87,
                "description": "autocompact_llm 전략에서 이 비율 이상 토큰 사용 시 child LLM 요약 발동 (Claude Code 기본 87%). 연속 실패 3회 시 회로 차단",
            },
            {
                "id": "autocompact_keep_tail",
                "label": "L5 보존 tail 메시지 수",
                "type": "number",
                "min": 1,
                "max": 10,
                "default": 3,
                "description": "autocompact 후 messages 말미에 온전히 남길 최근 메시지 개수",
            },
        ],
        "behavior": [
            "3단계 압축: 오래된 메시지 제거 → 저우선순위 섹션 삭제 → 요약",
            "~3 chars/token 추정",
            "RAG: DocumentService.search 에 score_threshold / filter / rerank / rerank_top_k 전달",
            "서버 rerank 요청 단위 활성 (xgen-documents DocumentSearchRequest 지원)",
            "metadata_filter: 파일/폴더 기반 범위 제한으로 정답 청크 도달률 개선",
            "enhance_prompt: <enhance_prompt> 블록으로 system_prompt 말미에 추가",
        ],
    },
    # v0.14.0 — s07_llm 삭제됨. 본문 LLM 호출 관련 필드는 s00_harness 로 이관.
    "s07_act": {
        "description_ko": "도구를 실행합니다. MCP 도구, 빌트인 도구를 호출하고 결과를 수집합니다.",
        "when_to_use": "s00 본문 호출이 도구를 생성했을 때 (tool_use). Pipeline 이 자동 판단하므로 chosen 에 포함해두면 충분.",
        "when_to_skip": "도구 사용 없이 텍스트 응답만으로 종결되는 요청.",
        "cost_hint": "variable",
        "description_en": "Executes tool calls from LLM response.",
        "icon": "⚡",
        "fields": [
            {
                "id": "timeout",
                "label": "도구 실행 타임아웃 (초)",
                "type": "number",
                "min": 5,
                "max": 300,
                "default": 60,
            },
            {
                "id": "result_budget",
                "label": "누적 결과 문자 예산",
                "type": "number",
                "min": 5000,
                "max": 200000,
                "step": 5000,
                "default": 50000,
                "description": "여러 도구 결과의 합계 상한 (2차 방어). 개별 결과는 preview_threshold 로 통제",
            },
            {
                "id": "tool_result_preview_threshold",
                "label": "PD preview 전환 임계 (자)",
                "type": "number",
                "min": 1000,
                "max": 200000,
                "step": 1000,
                "default": 50000,
                "description": "개별 결과가 이 크기를 넘으면 preview 만 messages 에 흘리고 원본은 pd_stores 로 보존 (Claude Code L1 패턴)",
            },
            {
                "id": "tool_result_preview_size",
                "label": "PD preview 크기 (자)",
                "type": "number",
                "min": 256,
                "max": 16384,
                "step": 256,
                "default": 2048,
                "description": "preview 로 남길 첫 N 자. LLM 은 fetch_pd(kind='tool_result', id=<tool_use_id>) 로 원본 조회",
            },
        ],
        "behavior": [
            "순차 실행 (에러 허용)",
            "L1 Tool Result Budget: 개별 결과 > preview_threshold 면 preview + pd_stores 보존 (원본 소실 없음)",
            "누적 result_budget 초과 시 추가 축약 (2차 방어)",
            "fetch_pd 빌트인으로 preview 원본 재접근",
            "MCP → MCP 서비스 HTTP 호출",
            "discover_tools 빌트인 (Progressive Disclosure L2)",
        ],
    },
    "s08_judge": {
        "description_ko": "독립 LLM 호출로 응답 품질을 평가합니다. 기준 미달 시 재시도합니다.",
        "when_to_use": "품질 검증 필수 (규제·법무·의료·금융·정확도 민감). 기준 미달 시 s09_decide 가 retry 판단.",
        "when_to_skip": "창작·브레인스토밍·잡담 — 정답이 없어 평가 기준이 모호한 경우.",
        "cost_hint": "medium",
        "description_en": "Evaluates response quality with independent LLM call.",
        "icon": "✅",
        "fields": [
            {
                "id": "threshold",
                "label": "통과 기준 점수",
                "type": "slider",
                "min": 0,
                "max": 1,
                "step": 0.05,
                "default": 0.7,
            },
            {
                "id": "criteria",
                "label": "평가 기준",
                "type": "multi_select",
                "options": ["relevance", "completeness", "accuracy", "clarity"],
                "default": ["relevance", "completeness", "accuracy", "clarity"],
            },
        ],
        "behavior": [
            "관련성(0.3) + 완전성(0.3) + 정확성(0.2) + 명확성(0.2)",
            "독립 LLM 호출 (temperature=0)",
            "점수 미달 → s09_decide가 retry 결정",
        ],
    },
    # s05_policy: dict 박제 대신 PolicyGateStage.describe_config() self-describing.
    # get_stage_config() 가 Stage 의 describe_config() 를 먼저 조회 — 새 Stage 는
    # 중앙 dict 수정 없이 자체 선언만으로 UI 에 합류.

    "s09_decide": {
        "description_ko": "루프를 계속할지 완료할지 판단합니다.",
        "when_to_use": "항상 (필수). 에이전틱 루프 종료 판정.",
        "when_to_skip": "불가 (REQUIRED_STAGES).",
        "cost_hint": "low",
        "description_en": "Decides whether to continue, complete, or retry.",
        "icon": "🔀",
        # v0.26.0 — max_iterations stage_param 제거 (D4).
        # Pipeline 은 top-level `state.config.max_iterations` 만 read. s09 의 stage_param
        # 으로 노출하면 ConfigPanel 의 글로벌 max_iterations 와 이중 노출되어
        # 사용자가 어느 값이 박히는지 헷갈림. 단일 진실 소스 = HarnessConfig top-level.
        # max_retries 는 그대로 유지 (s09 가 직접 read).
        "fields": [
            {
                "id": "max_retries",
                "label": "최대 재시도 횟수",
                "type": "number",
                "min": 0,
                "max": 10,
                "default": 3,
            },
        ],
        "behavior": [
            "비용 초과 → complete",
            "반복 한도 → complete",
            "도구 호출 대기 → continue",
            "검증 미달 → retry",
            "텍스트 응답 → complete",
        ],
    },
    "s10_save": {
        "description_ko": "실행 결과를 데이터베이스에 저장합니다.",
        "when_to_use": "실행 로그 영구화 필요 (감사·리플레이·KPI 측정·세션 공유).",
        "when_to_skip": "테스트·ephemeral·개인 비공유 세션.",
        "cost_hint": "low",
        "description_en": "Persists execution results to database.",
        "icon": "💾",
        "fields": [
            {
                "id": "save_enabled",
                "label": "DB 저장 활성화",
                "type": "toggle",
                "default": True,
            },
        ],
        "behavior": [
            "harness_execution_log 테이블에 기록",
            "메트릭스: 토큰, 비용, 시간, 모델",
            "DB 없으면 graceful skip",
        ],
    },
    "s11_finalize": {
        "description_ko": "최종 출력을 확정하고 메트릭스를 수집합니다.",
        "when_to_use": "항상 (필수). MetricsEvent 방출 + final_output 확정.",
        "when_to_skip": "불가 (REQUIRED_STAGES).",
        "cost_hint": "low",
        "description_en": "Finalizes output and collects metrics.",
        "icon": "🏁",
        "fields": [
            {
                "id": "output_format",
                "label": "출력 포맷",
                "type": "select",
                "options": ["text", "markdown", "json"],
                "default": "text",
            },
        ],
        "behavior": [
            "state.final_output 확정",
            "MetricsEvent 발행 (토큰, 비용, 시간)",
            "DoneEvent로 스트리밍 종료",
        ],
    },
}


def _inject_dynamic_options(cfg: dict) -> dict:
    """fields 내 provider/model select 옵션을 레지스트리에서 동적으로 주입.

    STAGE_CONFIGS 에 하드코딩된 options 리스트를 providers 레지스트리의 실제
    등록된 값으로 교체. 새 provider/model 등록 시 UI 가 자동 반영됨.
    """
    if not cfg or not isinstance(cfg, dict):
        return cfg
    try:
        from ..providers import list_providers, get_default_model
    except Exception as e:
        _sc_logger.debug("providers registry import 실패, stage_config provider 옵션 정적 유지: %s", e)
        return cfg

    fields = cfg.get("fields")
    if not isinstance(fields, list):
        return cfg

    providers = list_providers()
    # provider 기반 모델 목록 — PROVIDER_MODELS 레지스트리에서 조회 (기본 + 추가 모델).
    # 기존 get_default_model 은 provider 당 1개만 반환 → get_provider_models 로 교체.
    try:
        from ..providers import get_provider_models
    except ImportError:
        get_provider_models = None

    default_models: list[str] = []
    seen = set()
    for p in providers:
        if get_provider_models is not None:
            candidates = get_provider_models(p)
        else:
            m = get_default_model(p)
            candidates = [m] if m else []
        for m in candidates:
            if m and m not in seen:
                default_models.append(m)
                seen.add(m)

    updated_fields = []
    for f in fields:
        if not isinstance(f, dict):
            updated_fields.append(f)
            continue
        fid = f.get("id")
        if fid == "provider" and providers:
            f = {**f, "options": providers}
        elif fid == "model" and default_models:
            # 기존 options 와 합집합 (하드코딩 보존 + 신규 추가)
            existing = f.get("options", [])
            merged = list(dict.fromkeys([*default_models, *existing]))
            f = {**f, "options": merged}
        updated_fields.append(f)

    return {**cfg, "fields": updated_fields}


def _inject_stage_meta(stage_id: str, cfg: dict) -> dict:
    """스테이지별 추가 메타 주입 — UI 배지/라이브 카운터용.

    현재: s04_tool 에 progressive_threshold.
    외부 전략 교체 시에도 일관된 키로 노출되도록 단일 지점 관리.
    """
    if not cfg or not isinstance(cfg, dict):
        return cfg
    if stage_id == "s04_tool":
        try:
            from ..stages.strategies.discovery import get_progressive_threshold
            cfg = {**cfg, "progressive_threshold": get_progressive_threshold()}
        except Exception as e:
            _sc_logger.debug("progressive_threshold 조회 실패, 기본값 유지: %s", e)
    return cfg


def _resolve_stage_self_describe(stage_id: str) -> Optional[dict]:
    """Stage 클래스의 self-describing 설정 조회.

    조회 순서:
      1. **Stage 가 `describe_config()` 를 override 했으면 그 결과 사용** —
         명시적 i18n(description_ko/en) 분리, machine meta 명시 등 세밀 제어가
         필요할 때 (예: s05_policy). auto-compose 보다 우선.
      2. class attribute (when_to_use/when_to_skip/cost_hint) + param_schema()
         자동 조립 — docstring 첫 단락을 description_ko/en 양쪽에 박는 폴백.
      3. None — 중앙 STAGE_CONFIGS dict 폴백.

    1 번이 2 번보다 우선이어야 하는 이유: auto-compose 는 docstring 을 영문/한글
    구분 없이 그대로 박는다 → override 가 있어도 가려져서 영문 locale 에서 한글이
    노출되는 회귀가 있었다 (s05_policy v0.17.0).
    """
    try:
        from .registry import _get_default_registry
        reg = _get_default_registry()
        cls = reg.get(stage_id, "default")
    except Exception:
        return None

    # 1. 명시적 describe_config() override 우선 — Stage 베이스의 기본 구현은 None 반환.
    try:
        explicit = cls.describe_config() if hasattr(cls, "describe_config") else None
    except Exception:
        explicit = None
    if isinstance(explicit, dict):
        return explicit

    # 2. STAGE_CONFIGS dict 에 항목이 있으면 그쪽 우선 (description_ko/en 명시 분리됨).
    #    class attr (when_to_use 등) 가 있어도 dict 의 설명문이 영문 locale 에서 정확.
    #    auto-compose 는 dict 항목이 없는 외부 Stage 용 폴백.
    if stage_id in STAGE_CONFIGS:
        return None  # 호출자가 STAGE_CONFIGS 폴백을 쓴다 (or 패턴).

    # 3. class attribute 자동 조립 (docstring 폴백 — 외부 Stage 용)
    return _compose_from_class_attrs(cls)


def _compose_from_class_attrs(cls) -> Optional[dict]:
    """v0.17.0 — Stage 의 class attribute + param_schema() 를 UI 용 dict 로 조립.

    Stage 가 `when_to_use` / `when_to_skip` / `cost_hint` 중 하나라도 명시적으로
    override 했거나 `param_schema` 클래스메서드를 override 했으면 새 모델로 판단.
    한국어 리터럴(description_ko/behavior/icon) 은 엔진에 없음 — UI 가 필요하면
    docstring 첫 줄을 보조 설명으로 사용.
    """
    # 새 모델 판별 — 기본값 외에 선언이 있는지
    has_machine_meta = (
        bool(getattr(cls, "when_to_use", "")) or
        bool(getattr(cls, "when_to_skip", "")) or
        (hasattr(cls, "param_schema") and callable(getattr(cls, "param_schema")) and
         cls.param_schema.__qualname__.split(".")[0] != "Stage")  # Stage 기본 구현 아님
    )
    if not has_machine_meta:
        return None

    # param_schema() → UI fields
    fields: list[dict] = []
    try:
        schema = cls.param_schema() if hasattr(cls, "param_schema") else []
        for f in schema or []:
            if hasattr(f, "to_dict"):
                fields.append(f.to_dict())
            elif isinstance(f, dict):
                fields.append(f)
    except Exception as e:
        _sc_logger.debug("[stage_config] %s.param_schema() 호출 실패: %s", cls.__name__, e)

    # docstring 첫 문단 — 인간 UI 보조 설명 (선택). 없어도 Stage 작동.
    doc = (cls.__doc__ or "").strip()
    if doc:
        first_para = doc.split("\n\n", 1)[0].strip()
    else:
        first_para = ""

    return {
        # 인간 UI 보조 — Stage 가 남기는 docstring 기반. 없으면 빈 문자열.
        "description_ko": first_para,
        "description_en": first_para,  # i18n 없음. 필요 시 추후 gettext.
        # 여기부터는 LLM 도 읽는 machine meta
        "when_to_use": getattr(cls, "when_to_use", "") or "",
        "when_to_skip": getattr(cls, "when_to_skip", "") or "",
        "cost_hint": getattr(cls, "cost_hint", "medium") or "medium",
        "fields": fields,
        "behavior": [],  # 엔진에 저장 안 함 — UI 필요하면 docstring 참조
    }


def get_stage_config(stage_id: str) -> dict:
    """스테이지 설정 스키마 반환 — provider/model 옵션은 레지스트리에서 동적 주입.

    구 stage_id 가 들어와도 canonical 로 해석 (v0.11.0 alias 하위호환).
    v0.17.0 — Stage.describe_config() 가 dict 를 반환하면 우선 사용 (self-describing).
    """
    sid = canonical_stage_id(stage_id)
    cfg = _resolve_stage_self_describe(sid) or STAGE_CONFIGS.get(sid, {})
    cfg = _inject_dynamic_options(cfg)
    cfg = _inject_stage_meta(sid, cfg)
    return cfg


def get_all_stage_configs() -> dict:
    """전체 스테이지 설정 스키마 — 각 스테이지에 dynamic options + meta 자동 주입.

    v0.17.0 — STAGE_CONFIGS dict 에 없어도 registry 에 등록된 Stage 가
    `describe_config()` 를 가지고 있으면 결과에 포함.
    """
    out: dict = {}
    seen: set[str] = set()

    # 1. 중앙 dict
    for sid, base in STAGE_CONFIGS.items():
        self_described = _resolve_stage_self_describe(sid)
        cfg = self_described if self_described is not None else base
        cfg = _inject_dynamic_options(cfg)
        cfg = _inject_stage_meta(sid, cfg)
        out[sid] = cfg
        seen.add(sid)

    # 2. registry 에만 있고 self-describe 하는 Stage 들 병합 — dict 박제 없이 UI 합류.
    try:
        from .registry import _get_default_registry
        reg = _get_default_registry()
        for sid in reg.list_stages():
            if sid in seen:
                continue
            self_described = _resolve_stage_self_describe(sid)
            if self_described is None:
                continue
            cfg = _inject_dynamic_options(self_described)
            cfg = _inject_stage_meta(sid, cfg)
            out[sid] = cfg
    except Exception as e:
        _sc_logger.debug("[stage_config] registry self-describe 병합 skip: %s", e)

    return out
