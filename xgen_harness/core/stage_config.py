"""
스테이지별 설정 스키마 — UI에서 각 스테이지 클릭 시 보여줄 설정 항목

각 스테이지마다:
- 설명 (한국어/영어)
- 설정 가능한 필드 (type, label, options, default)
- 기술적 동작 설명
"""

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
    "s08_execute":       "s08_act",
    "s09_validate":      "s09_judge",
    "s12_complete":      "s12_finalize",
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
    "s01_input": {
        # PHILOSOPHY §2 s01: **사용자 입력 정규화 전용**. LLM provider/model/temperature
        # 선택은 하네스 상단 설정 또는 s07 관할 — s01 에서 필드로 노출하지 않음.
        "description_ko": "사용자 입력을 검증하고 첨부 파일을 content block 으로 정규화합니다.",
        "description_en": "Validates user input and normalizes attached files to content blocks.",
        "icon": "📥",
        "fields": [],
        "behavior": [
            "빈 입력 거부",
            "파일 첨부: base64 이미지 · 텍스트 블록 변환",
            "첫 user 메시지를 파이프라인에 push (단일 책임)",
        ],
    },
    "s02_history": {
        "description_ko": "이전 대화 이력, 실행 결과, 관련 문서를 로드합니다.",
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
            {
                "id": "memory_source",
                "label": "기억 소스",
                "type": "multi_select",
                "options": ["execution_log", "chat_history", "documents"],
                "default": ["execution_log"],
                "description": "어디서 기억을 가져올지 선택",
            },
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
                "options": ["off", "enabled", "strict"],
                "default": "off",
                "description": "off: 인용 지시 없음 / enabled: [DOC_n] 인용 권장 / strict: 참조 문서 밖 정보 답변 금지 (환각 방지)",
            },
        ],
        "behavior": [
            "섹션 우선순위: Identity → Rules → Tools → RAG → History",
            "컨텍스트 압축 시 낮은 우선순위부터 제거",
            "strict 모드: 제공 문서 밖 정보는 'not available' 로 응답",
        ],
    },
    "s04_tool": {
        "description_ko": "사용할 도구, MCP 세션, 문서 컬렉션을 선택합니다. Progressive Disclosure로 효율적으로 관리합니다.",
        "description_en": "Select tools, MCP sessions, and document collections.",
        "icon": "🔧",
        "fields": [
            {
                "id": "mcp_sessions",
                "label": "MCP 세션",
                "type": "multi_select",
                "options_source": "mcp_sessions",
                "default": [],
                "description": "연결할 MCP 세션 (GitHub, Slack, DB 등)",
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
        ],
        "behavior": [
            "Level 1: 도구 메타데이터만 프롬프트에 (~40 tokens/tool)",
            "Level 2: discover_tools로 상세 스키마 조회",
            "Level 3: 실제 도구 실행 (s08_act)",
            "RAG: Documents API로 벡터 검색 → 시스템 프롬프트에 주입",
            "MCP: MCP 서비스에서 도구 자동 디스커버리",
        ],
    },
    "s05_strategy": {
        "description_ko": "Chain-of-Thought 계획을 수립합니다. LLM이 단계별로 생각하도록 유도합니다.",
        "description_en": "Chain-of-Thought planning before execution.",
        "icon": "📋",
        "fields": [
            {
                "id": "planning_mode",
                "label": "계획 모드",
                "type": "select",
                "options": ["cot", "react", "none"],
                "default": "cot",
            },
        ],
        "behavior": [
            "첫 번째 루프에서만 실행",
            "시스템 프롬프트에 계획 지시 추가",
        ],
    },
    "s06_context": {
        "description_ko": "토큰 윈도우를 관리합니다. 예산 초과 시 자동으로 컨텍스트를 압축합니다.",
        "description_en": "Manages token window with automatic compaction.",
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
    "s07_llm": {
        "description_ko": "LLM API를 호출합니다. 실시간 스트리밍, 재시도, 모델 폴백을 지원합니다.",
        "description_en": "Calls LLM API with streaming, retry, and fallback.",
        "icon": "🤖",
        "fields": [
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
            "httpx SSE 스트리밍 (Anthropic/OpenAI)",
            "재시도: 429 → 10/20/40초, 529 → 1/2/4초",
            "모델 폴백: Anthropic → OpenAI",
            "Prompt Caching 활성화",
        ],
    },
    "s08_act": {
        "description_ko": "도구를 실행합니다. MCP 도구, 빌트인 도구를 호출하고 결과를 수집합니다.",
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
    "s09_judge": {
        "description_ko": "독립 LLM 호출로 응답 품질을 평가합니다. 기준 미달 시 재시도합니다.",
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
            "점수 미달 → s10_decide가 retry 결정",
        ],
    },
    "s10_decide": {
        "description_ko": "루프를 계속할지 완료할지 판단합니다.",
        "description_en": "Decides whether to continue, complete, or retry.",
        "icon": "🔀",
        "fields": [
            {
                "id": "max_iterations",
                "label": "최대 반복 횟수",
                "type": "number",
                "min": 1,
                "max": 50,
                "default": 10,
            },
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
    "s11_save": {
        "description_ko": "실행 결과를 데이터베이스에 저장합니다.",
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
    "s12_finalize": {
        "description_ko": "최종 출력을 확정하고 메트릭스를 수집합니다.",
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
    except Exception:
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
        except Exception:
            pass
    return cfg


def get_stage_config(stage_id: str) -> dict:
    """스테이지 설정 스키마 반환 — provider/model 옵션은 레지스트리에서 동적 주입.

    구 stage_id 가 들어와도 canonical 로 해석 (v0.11.0 alias 하위호환).
    """
    sid = canonical_stage_id(stage_id)
    cfg = STAGE_CONFIGS.get(sid, {})
    cfg = _inject_dynamic_options(cfg)
    cfg = _inject_stage_meta(sid, cfg)
    return cfg


def get_all_stage_configs() -> dict:
    """전체 스테이지 설정 스키마 — 각 스테이지에 dynamic options + meta 자동 주입."""
    out: dict = {}
    for sid, base in STAGE_CONFIGS.items():
        cfg = _inject_dynamic_options(base)
        cfg = _inject_stage_meta(sid, cfg)
        out[sid] = cfg
    return out
