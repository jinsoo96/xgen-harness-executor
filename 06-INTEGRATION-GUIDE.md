# xgen-harness 연동 가이드

## 이 문서는 뭔가

xgen-harness가 xgen 플랫폼과 어떻게 연결되는지 설명합니다.
**"어느 Stage에서 xgen의 어떤 서비스가 끼워지고, 어떤 데이터가 흐르는지"**를
처음 보는 사람도 이해할 수 있도록 씁니다.

---

## 1. 핵심 개념: 라이브러리는 인프라를 모른다

```
xgen-harness (PyPI 패키지)
├── 12개 Stage가 순서대로 실행
├── 각 Stage는 "서비스가 있으면 쓰고, 없으면 건너뜀"
└── 특정 URL, 특정 DB, 특정 프로바이더를 하드코딩하지 않음

XgenAdapter (xgen-workflow 안의 어댑터)
├── xgen 인프라를 하네스에 "끼워넣는" 유일한 접점
├── register_service("documents", "http://xgen-documents:8000")
├── set_execution_context(api_key=xgen에서 가져온 키)
└── 하네스 이벤트를 xgen SSE 포맷으로 변환
```

**다른 플랫폼에서 쓰고 싶으면?** XgenAdapter 대신 자기 어댑터를 만들면 됩니다.
`register_service("documents", "http://내-서버:8080")` 이렇게만 하면 끝.

---

## 2. 전체 실행 흐름

```
사용자 → /harness UI에서 실행 버튼
          │
          ▼
    POST /api/agentflow/execute/based-id/stream
          │
          ▼
    execution_core.py
          │
          ├── harness_config 없음 → 기존 캔버스 실행기 (LangGraph DAG)
          │                         기존 코드 수정 0줄
          │
          └── harness_config 있음 → XgenAdapter.execute()
                │
                │ ① 서비스 등록 (ServiceRegistry)
                │ ② API 키 주입 (ExecutionContext)  
                │ ③ Pipeline 12 Stage 순회
                │ ④ 이벤트 → xgen SSE 변환
                │
                ▼
          SSE 스트리밍 → 프론트엔드
```

---

## 3. Stage별 연동 상세

### Phase A: 준비 (1회 실행)

#### s01_input — 입력 처리 + LLM 프로바이더 생성

| 연동 대상 | xgen 서비스 | 어떻게 연결되나 |
|-----------|------------|----------------|
| **API 키** | xgen-core (`persistent_configs` 테이블) | `ExecutionContext.get_api_key()` → xgen-core API 조회 → 프로바이더에 주입 |
| **LLM 프로바이더** | 없음 (자체 httpx) | `create_provider("openai", api_key, model)` → 내장 AnthropicProvider / OpenAIProvider |
| **LangChain LLM** | xgen-workflow의 ChatAnthropic 등 | `wrap_langchain(llm)` → LangChainAdapter로 래핑 (선택사항) |

**API 키 해석 우선순위**:
```
1. ExecutionContext (contextvars — 동시 실행 안전)
2. ServiceProvider.config.get_api_key() (xgen-core persistent_configs)
3. os.environ (읽기 전용 폴백)
```

**확장**: `register_provider("vllm", VLLMProvider)` → 코드 수정 없이 프로바이더 추가

---

#### s02_memory — 대화 이력 로드

| 연동 대상 | xgen 서비스 | 어떻게 연결되나 |
|-----------|------------|----------------|
| **이전 대화** | xgen-workflow DB (`execution_io` 테이블) | `ServiceProvider.database` → 이전 interaction 메시지 조회 |

**동작**: `interaction_id`가 있으면 이전 대화 이력을 `state.messages`에 추가.
없으면 건너뜀 ("조건 미충족으로 건너뜀").

---

#### s03_system_prompt — 시스템 프롬프트 조립

| 연동 대상 | xgen 서비스 | 어떻게 연결되나 |
|-----------|------------|----------------|
| **RAG 보강** | xgen-documents | `get_service_url("documents")` → `/api/retrieval/documents/search` |
| **프롬프트 템플릿** | 없음 (config에서 직접) | `harness_config.system_prompt` 값 사용 |

**동작**: `harness_config.system_prompt`를 기본으로, RAG 컬렉션이 있으면 검색 결과를 프롬프트에 추가.
`documents` 서비스가 등록 안 되어 있으면 RAG skip.

---

#### s04_tool_index — 도구 색인

| 연동 대상 | xgen 서비스 | 어떻게 연결되나 |
|-----------|------------|----------------|
| **MCP 도구** | xgen-mcp-station | `get_service_url("mcp")` → `MCPClient.list_tools(session_id)` |
| **Gallery 도구** | PyPI 패키지 | `discover_gallery_tools()` → entry_points 자동 발견 |
| **커스텀 도구** | `register_tool_source()` | ToolSource Protocol 구현체 |

**프론트에서 설정하는 법**: 
s04 노드 클릭 → `mcp_sessions` 파라미터에서 MCP 세션 체크박스 선택

**동작 흐름**:
```
stage_params.s04_tool_index.mcp_sessions = ["session-abc"]
    → MCPClient("http://xgen-mcp-station:8000").list_tools("session-abc")
    → tool_definitions = [{name: "weather", input_schema: {...}}, ...]
    → state.tool_definitions에 저장
    → s07_llm에서 LLM에 도구 목록 전달
```

`mcp` 서비스가 등록 안 되어 있으면 MCP 도구 skip. 내장 도구(`discover_tools`)는 항상 사용 가능.

---

### Phase B: 에이전트 루프 (반복 실행)

#### s05_plan — 실행 계획

| 연동 대상 | 없음 | 자체 로직 |
|-----------|------|----------|

**동작**: 도구가 있으면 계획 수립 모드, 없으면 바로 LLM 호출. 순수 로직 Stage.

---

#### s06_context — RAG 검색 + 컨텍스트 주입

| 연동 대상 | xgen 서비스 | 어떻게 연결되나 |
|-----------|------------|----------------|
| **RAG 문서 검색** | xgen-documents | `get_service_url("documents")` → `/api/retrieval/documents/search` |
| **DB 스키마 조회** | xgen-workflow DB | `ServiceProvider.database` (TODO) |

**프론트에서 설정하는 법**:
s06 노드 클릭 → `rag_collections` 파라미터에서 컬렉션 체크박스 선택

**동작 흐름**:
```
stage_params.s06_context.rag_collections = ["assort_bb8b..."]
    → xgen-documents POST /api/retrieval/documents/search
      {collection_name: "assort_bb8b...", query_text: "사용자 질문", limit: 5}
    → 검색 결과를 state.messages의 system prompt에 추가
    → s07_llm이 이 컨텍스트를 참조하여 답변
```

**`documents` 서비스 미등록 시**: RAG 건너뜀, LLM이 자체 지식으로만 답변.

---

#### s07_llm — LLM 호출 (핵심)

| 연동 대상 | xgen 서비스 | 어떻게 연결되나 |
|-----------|------------|----------------|
| **LLM API** | OpenAI / Anthropic / Google 등 | `state.provider.chat(messages, tools)` → httpx SSE 스트리밍 |
| **토큰 추적** | 없음 (자체 계산) | `state.token_usage` += 사용량, `state.cost_usd` += 비용 |

**동작**: messages + tool_definitions → LLM API 호출 → 스트리밍 응답.
LLM이 tool_use를 반환하면 `state.pending_tool_calls`에 저장 → s08로 전달.

**확장**: `register_strategy("s07_llm", "retry", "custom", MyRetry)` → 리트라이 전략 교체

---

#### s08_execute — 도구 실행

| 연동 대상 | xgen 서비스 | 어떻게 연결되나 |
|-----------|------------|----------------|
| **MCP 도구 실행** | xgen-mcp-station | `MCPClient.call_tool(session_id, tool_name, args)` |
| **커스텀 도구** | `register_tool_source()` | ToolSource.call_tool(name, args) |
| **내장 도구** | 없음 (자체) | `discover_tools` 등 빌트인 |

**동작 흐름**:
```
s07_llm이 tool_use 반환
    → state.pending_tool_calls = [{tool_name: "weather", tool_input: {city: "Seoul"}}]
    → s08_execute가 도구 디스패치:
        1. 등록된 ToolSource 순회 → has_tool("weather")? → call_tool()
        2. MCP 매핑 확인 → MCPClient.call_tool()
        3. 내장 도구 확인
    → 결과를 state.tool_results에 저장
    → state.messages에 tool_result 추가
    → s10_decide가 "계속" 판단하면 s05로 루프백
```

**`pending_tool_calls`가 없으면**: "조건 미충족으로 건너뜀".

---

#### s09_validate — 응답 품질 검증

| 연동 대상 | xgen 서비스 | 어떻게 연결되나 |
|-----------|------------|----------------|
| **LLM Judge** | 동일 LLM 프로바이더 | `state.provider`로 평가 프롬프트 실행 |

**동작**: LLM 응답을 별도 평가 프롬프트로 0.0~1.0 점수화.
`score >= 0.7`이면 pass, 아니면 retry 가능.

**확장**: `register_strategy("s09_validate", "evaluation", "strict", StrictJudge)` → 평가 로직 교체

---

#### s10_decide — 계속/종료 판단

| 연동 대상 | 없음 | 자체 로직 |
|-----------|------|----------|

**동작**: 
- `pending_tool_calls`가 있으면 → "continue" (s05로 루프)
- `validation_score < threshold`이면 → "retry" (s05로 루프)
- 그 외 → "complete" (s11로 진행)
- `max_iterations` 초과 → 강제 "complete"

---

### Phase C: 마무리 (1회 실행)

#### s11_save — 실행 이력 저장

| 연동 대상 | xgen 서비스 | 어떻게 연결되나 |
|-----------|------------|----------------|
| **실행 이력 DB** | xgen-workflow DB | `ServiceProvider.database.insert_record(table, record)` |

**저장 데이터**: execution_id, workflow_id, provider, model, token_usage, cost_usd, duration_ms
**테이블명**: `stage_params.s11_save.table_name` (기본: `harness_execution_log`)
**DB 미연결 시**: 로그만 남기고 skip.

---

#### s12_complete — 메트릭스 집계 + 스트림 종료

| 연동 대상 | 없음 | 자체 로직 |
|-----------|------|----------|

**동작**: 전체 실행 시간, 토큰 사용량, 비용을 집계하여 `MetricsEvent` 발행.
프론트 EventLog에 "1736ms | 397 tokens | $0.0001" 형태로 표시.

---

## 4. xgen 서비스 연결 요약 (한눈에 보기)

```
┌─────────────────────────────────────────────────────────┐
│ xgen-core (Config 서비스)                                │
│ register_service("config", "http://xgen-core:8000")     │
│                                                         │
│ 연결 Stage: s01_input                                    │
│ 용도: API 키 조회 (persistent_configs 테이블)             │
│ 미등록 시: 환경변수 폴백                                  │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ xgen-documents (RAG 서비스)                              │
│ register_service("documents", "http://xgen-documents:8000")│
│                                                         │
│ 연결 Stage: s03_system_prompt, s06_context               │
│ 용도: 문서 컬렉션 벡터 검색 → 프롬프트/컨텍스트 보강      │
│ API: POST /api/retrieval/documents/search                │
│ 미등록 시: RAG 없이 LLM 자체 지식으로 답변                │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ xgen-mcp-station (MCP 도구 서비스)                       │
│ register_service("mcp", "http://xgen-mcp-station:8000") │
│                                                         │
│ 연결 Stage: s04_tool_index, s08_execute                  │
│ 용도: MCP 세션의 도구 목록 조회 + 도구 실행               │
│ API: GET /api/mcp/sessions/{id}/tools                    │
│      POST /api/mcp/sessions/{id}/call-tool               │
│ 미등록 시: MCP 도구 없이 내장 도구만 사용                  │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ xgen-workflow DB (DatabaseClient)                        │
│ XgenAdapter(db_manager=db_manager) 으로 주입              │
│                                                         │
│ 연결 Stage: s02_memory, s11_save                         │
│ 용도: 대화 이력 조회 + 실행 결과 저장                     │
│ 미주입 시: 이력/저장 없이 단발 실행                       │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ LLM Provider (OpenAI / Anthropic / Google 등)            │
│ API 키: ExecutionContext.set_execution_context(api_key)  │
│                                                         │
│ 연결 Stage: s01_input(생성), s07_llm(호출), s09_validate │
│ 용도: 텍스트 생성 + 도구 호출 + 응답 평가                 │
│ 확장: register_provider("name", MyProvider)              │
└─────────────────────────────────────────────────────────┘
```

---

## 5. 프론트엔드 UI에서 설정하는 법

### 5.1 프로바이더/모델 선택
**s01_input 노드 클릭** → Provider 드롭다운 (OpenAI/Anthropic/Google) → Model 선택

### 5.2 MCP 도구 연결
**s04_tool_index 노드 클릭** → MCP Sessions 체크박스에서 활성 세션 선택
→ 선택한 세션의 도구가 자동 디스커버리되어 LLM에 전달

### 5.3 RAG 문서 컬렉션 연결
**s06_context 노드 클릭** → RAG Collections 체크박스에서 컬렉션 선택
→ 사용자 질문으로 해당 컬렉션 벡터 검색 → 결과가 시스템 프롬프트에 주입

### 5.4 Preset으로 한 번에 설정
상단 ConfigPanel → Preset 선택:
- **minimal**: 도구/RAG/검증 없이 바로 대화
- **chat**: 이전 대화 이력 유지, 멀티턴
- **agent**: 도구 사용 + RAG + 계획 + 검증 + 루프
- **rag**: 문서 검색 기반 답변, 도구 없음
- **evaluator**: LLM Judge로 엄격한 품질 검증

---

## 6. 확장 포인트 (코드 수정 없이)

| 뭘 하고 싶은지 | 어떻게 하는지 | 예시 |
|---------------|-------------|------|
| LLM 프로바이더 추가 | `register_provider("name", Class)` | vLLM, Bedrock, 로컬 LLM |
| 전략 교체 | `register_strategy(stage, slot, name, Class)` | 리트라이, 캐시, 평가 |
| Stage 플러그인 | `register_stage(id, artifact, Class)` | s99_custom |
| 도구 소스 추가 | `register_tool_source(source)` | DB 쿼리 도구, API 도구 |
| 서비스 연결 | `register_service(name, url)` | 자체 RAG, 자체 MCP |
| Preset 추가 | `PRESETS["name"] = {...}` | 기업 전용 설정 |
| DB 테이블 변경 | `stage_params.s11_save.table_name` | 커스텀 로그 테이블 |

**전부 `register_*()` 패턴** — 라이브러리 코드를 건드리지 않고 실행기 측에서 등록만 하면 됩니다.

---

## 7. 아키텍처 원칙

| 원칙 | 설명 |
|------|------|
| **라이브러리 ≠ 인프라** | 하네스 코드에 xgen URL, AWS 엔드포인트 등 없음 |
| **실행기가 끼운다** | XgenAdapter가 `register_service()`로 xgen 인프라 등록 |
| **미등록 = skip** | 서비스 없으면 에러 아닌 건너뜀 (graceful degradation) |
| **레거시 무침범** | `harness_config` 유무로만 분기, 기존 코드 수정 0줄 |
| **동시성 안전** | `contextvars`로 API 키 격리, `os.environ` 쓰기 0개 |
| **Stage × Strategy** | 단계는 고정, 로직은 교체 가능 |
| **PyPI 배포** | `pip install xgen-harness` → 어디서든 독립 사용 가능 |

---

## 8. 버전 이력

| 버전 | 주요 변경 |
|------|----------|
| v0.1.0 | 12스테이지 파이프라인 초기 구현 |
| v0.2.0 | ServiceProvider + workflow_bridge |
| v0.3.0 | XgenAdapter + Provider Registry + Gallery Tools |
| v0.4.0 | ResourceRegistry (MCP/API/DB/Gallery/RAG 통합) |
| v0.5.0 | ServiceRegistry + ExecutionContext + Plugin System |
| v0.5.1 | ServiceRegistry 완전 분리 — 라이브러리에 인프라 가정 제거 |
