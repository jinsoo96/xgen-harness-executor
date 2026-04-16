# xgen-harness 이식 가이드

xgen-harness를 xgen-workflow에 연동하는 방법을 설명합니다.
처음 보는 사람도 "왜 이렇게 되어있고, 어떻게 끼워지는지" 이해할 수 있도록 작성했습니다.

---

## 1. 전체 구조

```
┌──────────────────────────────────────────────────────────┐
│ GitHub: xgen-harness-executor                            │
│ (PyPI: xgen-harness)                                     │
│                                                          │
│ 12-Stage Pipeline 엔진                                    │
│ - Stage, Strategy, Provider, Event 시스템                 │
│ - 특정 인프라에 의존하지 않음 (순수 라이브러리)              │
│ - pip install xgen-harness 로 어디서든 사용 가능            │
└────────────────────┬─────────────────────────────────────┘
                     │ pip install
                     ▼
┌──────────────────────────────────────────────────────────┐
│ GitLab: xgen-workflow (feature/harness-executor)         │
│                                                          │
│ XgenAdapter (어댑터)                                      │
│ - xgen 인프라를 하네스에 끼워넣는 유일한 접점               │
│ - 서비스 URL 등록, API 키 해석, 이벤트 포맷 변환           │
│                                                          │
│ execution_core.py (분기점)                                │
│ - harness_config 있으면 → 하네스                          │
│ - 없으면 → 기존 AsyncWorkflowExecutor (레거시)            │
│ - 기존 코드 수정 0줄                                      │
└──────────────────────────────────────────────────────────┘
```

**핵심 원칙**: 하네스 라이브러리는 xgen을 모른다. xgen도 하네스 내부를 모른다.
둘 사이를 **XgenAdapter**가 번역한다.

---

## 2. 연동 흐름 (요청 → 응답)

```
사용자가 /harness UI에서 실행 버튼 클릭
    │
    ▼
POST /api/agentflow/execute/based-id/stream
    │
    ▼
execution_core.py
    │
    ├─ workflow_data에 harness_config 없음?
    │   └─ 기존 AsyncWorkflowExecutor → LangGraph DAG → SSE 응답
    │
    └─ harness_config 있음?
        └─ XgenAdapter.execute()
            │
            ├─ 1. xgen 서비스 등록 (ServiceRegistry)
            │     register_service("documents", "http://xgen-documents:8000")
            │     register_service("mcp", "http://xgen-mcp-station:8000")
            │     register_service("config", "http://xgen-core:8000")
            │
            ├─ 2. API 키 해석 (ExecutionContext)
            │     xgen-core persistent_configs → 환경변수 → 폴백
            │     set_execution_context(api_key=key)  # 동시성 안전
            │
            ├─ 3. HarnessConfig 생성
            │     preset, provider, model, temperature, stage_params
            │
            ├─ 4. Pipeline.from_config(config, emitter)
            │     12 Stage 생성 → 3-Phase 배치
            │
            ├─ 5. Pipeline.run(state)
            │     s01→s02→...→s07(LLM)→s08(도구)→...→s12
            │     각 Stage에서 SSE 이벤트 발행
            │
            └─ 6. 이벤트 → xgen SSE 포맷 변환
                  {"type":"data","content":"..."} → 클라이언트
```

---

## 3. xgen 기능별 연동 방법

### 3.1 LLM 프로바이더

| xgen 기능 | 하네스 연동 | 코드 위치 |
|---|---|---|
| persistent_configs에 저장된 API 키 | XgenAdapter → xgen-core API 조회 → ExecutionContext 주입 | `adapters/xgen.py` |
| LangChain BaseChatModel | `wrap_langchain(llm)` → LLMProvider 인터페이스 | `providers/langchain_adapter.py` |
| 새 프로바이더 추가 | `register_provider("name", MyProvider)` | `providers/__init__.py` |

**API 키 해석 우선순위**:
```
ExecutionContext (contextvars, 동시성 안전)
    → ServiceProvider.config.get_api_key()  (xgen-core API)
    → os.environ (읽기 전용 폴백)
```

### 3.2 MCP 도구

| xgen 기능 | 하네스 연동 | 코드 위치 |
|---|---|---|
| MCP Station 세션 | s04_tool_index에서 `mcp_sessions` 파라미터로 세션 ID 전달 | `stages/s04_tool_index.py` |
| MCP 도구 디스커버리 | MCPClient → `get_service_url("mcp")` → list_tools | `tools/mcp_client.py` |
| MCP 도구 실행 | s08_execute → ToolSource 디스패치 → MCPClient.call_tool | `stages/s08_execute.py` |

**서비스 URL은 실행기가 등록**:
```python
# XgenAdapter가 부팅 시 등록
register_service("mcp", "http://xgen-mcp-station:8000")

# Stage에서 조회 — 등록 안 되어있으면 None → skip
url = get_service_url("mcp")
if not url:
    logger.info("MCP not available, skipping tool discovery")
```

### 3.3 RAG (문서 검색)

| xgen 기능 | 하네스 연동 | 코드 위치 |
|---|---|---|
| xgen-documents 컬렉션 | s06_context에서 `rag_collections` 파라미터로 컬렉션 이름 전달 | `stages/s06_context.py` |
| 벡터 검색 | `get_service_url("documents")` → `/api/retrieval/documents/search` | `stages/s06_context.py` |
| 시스템 프롬프트 RAG | s03에서도 RAG 검색 가능 (프롬프트 보강용) | `stages/s03_system_prompt.py` |

### 3.4 DB (실행 이력)

| xgen 기능 | 하네스 연동 | 코드 위치 |
|---|---|---|
| workflow DatabaseClient | XgenAdapter → XgenServiceProvider.create(db_manager) | `integrations/xgen_services.py` |
| 실행 이력 저장 | s11_save → DatabaseService.insert_record() | `stages/s11_save.py` |
| 테이블명 | `stage_params.s11_save.table_name` (기본: harness_execution_log) | 설정 가능 |

---

## 4. 확장 포인트 (코드 수정 없이)

```python
# 1. LLM 프로바이더 추가
from xgen_harness import register_provider
register_provider("my_llm", MyLLMProvider)

# 2. Strategy 교체 (검증, 리트라이, 캐시 등)
from xgen_harness import register_strategy
register_strategy("s09_validate", "evaluation", "strict_judge", StrictJudge)

# 3. Stage 플러그인
from xgen_harness import register_stage
register_stage("s99_custom", "default", MyCustomStage)

# 4. Tool 소스 추가
from xgen_harness import register_tool_source
register_tool_source(MyToolSource())  # ToolSource Protocol 구현

# 5. 서비스 엔드포인트 등록
from xgen_harness import register_service
register_service("my_service", "http://my-service:8080")

# 6. Preset 추가
from xgen_harness.core.presets import PRESETS
PRESETS["enterprise"] = {"disabled_stages": [...], "temperature": 0.2}
```

---

## 5. 이식 절차 (다른 프로젝트에 하네스 적용)

### Step 1: 설치
```bash
pip install xgen-harness>=0.5.1
```

### Step 2: 서비스 등록 (자기 환경에 맞게)
```python
from xgen_harness.core.service_registry import register_service

# 문서 서비스가 있으면 등록, 없으면 안 하면 됨 (자동 skip)
register_service("documents", "http://my-rag-server:8000")
register_service("mcp", "http://my-tool-server:8000")
```

### Step 3: API 키 주입
```python
from xgen_harness.core.execution_context import set_execution_context
set_execution_context(api_key="sk-...", provider="openai", model="gpt-4o-mini")
```

### Step 4: 파이프라인 실행
```python
from xgen_harness.core.config import HarnessConfig
from xgen_harness.core.pipeline import Pipeline
from xgen_harness.events.emitter import EventEmitter

config = HarnessConfig(provider="openai", model="gpt-4o-mini", preset="agent")
emitter = EventEmitter()
pipeline = Pipeline.from_config(config, emitter)

state = PipelineState(user_input="질문")
result = await pipeline.run(state)
```

### Step 5: (선택) xgen 환경이면 XgenAdapter 사용
```python
from xgen_harness.adapters.xgen import XgenAdapter

adapter = XgenAdapter(db_manager=db_manager)
async for event in adapter.execute(workflow_data, input_data, user_id=1):
    yield event  # 이미 xgen SSE 포맷
```

---

## 6. 아키텍처 원칙

| 원칙 | 설명 |
|---|---|
| **라이브러리는 인프라를 모른다** | xgen URL, AWS 엔드포인트 등이 라이브러리 코드에 없음 |
| **실행기가 인프라를 끼운다** | XgenAdapter, AWSAdapter 등이 register_service()로 등록 |
| **등록 안 하면 skip** | 서비스 없으면 해당 기능 건너뜀 (에러 아님) |
| **레거시 무침범** | execution_core.py에서 harness_config 분기만 추가, 기존 코드 수정 0줄 |
| **동시성 안전** | contextvars로 API 키 격리, os.environ 쓰기 0개 |
| **Stage × Strategy** | 단계(Stage)는 고정, 로직(Strategy)은 교체 가능 |

---

## 7. 버전 이력

| 버전 | 내용 |
|---|---|
| v0.1.0 | 12스테이지 파이프라인 초기 구현 |
| v0.2.0 | ServiceProvider + workflow_bridge |
| v0.3.0 | XgenAdapter + Provider Registry + Gallery Tools |
| v0.4.0 | ResourceRegistry (MCP/API/DB/Gallery/RAG 통합) |
| v0.5.0 | ServiceRegistry + ExecutionContext + Plugin System |
| v0.5.1 | ServiceRegistry 완전 분리 (라이브러리에 인프라 가정 제거) |
