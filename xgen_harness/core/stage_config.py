"""
스테이지별 설정 스키마 — UI에서 각 스테이지 클릭 시 보여줄 설정 항목

각 스테이지마다:
- 설명 (한국어/영어)
- 설정 가능한 필드 (type, label, options, default)
- 기술적 동작 설명
"""

STAGE_CONFIGS: dict[str, dict] = {
    "s01_input": {
        "description_ko": "사용자 입력을 검증하고 LLM 프로바이더를 초기화합니다.",
        "description_en": "Validates user input and initializes LLM provider.",
        "icon": "📥",
        "fields": [
            {
                "id": "provider",
                "label": "LLM 프로바이더",
                "type": "select",
                "options": ["anthropic", "openai", "google", "bedrock", "vllm"],
                "default": "anthropic",
            },
            {
                "id": "model",
                "label": "모델",
                "type": "select",
                "options": [
                    "claude-sonnet-4-20250514",
                    "claude-opus-4-20250514",
                    "claude-haiku-4-5-20251001",
                    "gpt-4o",
                    "gpt-4o-mini",
                    "gemini-2.5-flash",
                ],
                "default": "claude-sonnet-4-20250514",
            },
            {
                "id": "temperature",
                "label": "Temperature",
                "type": "slider",
                "min": 0,
                "max": 1,
                "step": 0.1,
                "default": 0.7,
            },
        ],
        "behavior": [
            "API 키 해석: 환경변수 → Config 서비스 → 폴백",
            "MCP 도구 자동 수집 (워크플로우 MCP 노드에서)",
            "멀티모달 입력 지원 (텍스트 + 이미지 + 파일)",
        ],
    },
    "s02_memory": {
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
    "s03_system_prompt": {
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
        ],
        "behavior": [
            "섹션 우선순위: Identity → Rules → Tools → RAG → History",
            "컨텍스트 압축 시 낮은 우선순위부터 제거",
        ],
    },
    "s04_tool_index": {
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
            "Level 3: 실제 도구 실행 (s08_execute)",
            "RAG: Documents API로 벡터 검색 → 시스템 프롬프트에 주입",
            "MCP: MCP 서비스에서 도구 자동 디스커버리",
        ],
    },
    "s05_plan": {
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
        ],
        "behavior": [
            "3단계 압축: 오래된 메시지 제거 → 저우선순위 섹션 삭제 → 요약",
            "~3 chars/token 추정",
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
    "s08_execute": {
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
                "label": "결과 문자 예산",
                "type": "number",
                "min": 5000,
                "max": 200000,
                "step": 5000,
                "default": 50000,
            },
        ],
        "behavior": [
            "순차 실행 (에러 허용)",
            "50K 문자 예산 초과 시 결과 축약",
            "MCP → MCP 서비스 HTTP 호출",
            "discover_tools 빌트인 (Progressive Disclosure L2)",
        ],
    },
    "s09_validate": {
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
    "s12_complete": {
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
    # provider 기반 모델 목록 — 중복 제거 유지 순서
    default_models: list[str] = []
    seen = set()
    for p in providers:
        m = get_default_model(p)
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


def get_stage_config(stage_id: str) -> dict:
    """스테이지 설정 스키마 반환 — provider/model 옵션은 레지스트리에서 동적 주입."""
    cfg = STAGE_CONFIGS.get(stage_id, {})
    return _inject_dynamic_options(cfg)


def get_all_stage_configs() -> dict:
    """전체 스테이지 설정 스키마"""
    return STAGE_CONFIGS
