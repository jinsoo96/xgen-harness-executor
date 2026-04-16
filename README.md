# xgen-harness-executor

[![PyPI](https://img.shields.io/pypi/v/xgen-harness?color=blue&label=PyPI)](https://pypi.org/project/xgen-harness/)
[![Python](https://img.shields.io/pypi/pyversions/xgen-harness)](https://pypi.org/project/xgen-harness/)
[![License](https://img.shields.io/pypi/l/xgen-harness)](https://pypi.org/project/xgen-harness/)

```bash
pip install xgen-harness
```

**하네스 실행기** — 12단계 파이프라인 기반 에이전트 실행 엔진

xgen 생태계의 모든 자산(LLM, MCP 도구, API 도구, DB 도구, RAG, Gallery)을 **어댑터 패턴**으로 끌어와 파이프라인에 끼울 수 있는 구조.

## xgen-workflow 연동 (한 줄)

```python
from xgen_harness.adapters.xgen import XgenAdapter

adapter = XgenAdapter(db_manager=db_manager)
async for event in adapter.execute(workflow_data, input_data, user_id=user_id):
    yield event  # 이미 xgen SSE 포맷
```

XgenAdapter가 알아서 처리하는 것:
- workflow_data에서 harness_config, 에이전트 노드, MCP 세션, API 도구, DB 도구, 파일 추출
- ServiceProvider 생성 (DB/Config/MCP/Documents)
- API 키 해석 (환경변수 → xgen-core → 프로바이더 폴백)
- ResourceRegistry로 모든 도구를 통합 로드
- 파이프라인 실행 + 이벤트를 xgen SSE 포맷으로 변환

## xgen 자산 통합 — ResourceRegistry

모든 xgen 자산을 한 곳에서 로드하고 실행하는 통합 레지스트리.

```python
from xgen_harness.adapters.resource_registry import ResourceRegistry

registry = ResourceRegistry(services)
await registry.load_all(workflow_data, harness_config)

# Stage에서 사용
state.tool_definitions = registry.get_tool_definitions()  # LLM에 전달
result = await registry.execute_tool("weather_api", {...})  # 도구 실행
rag_text = await registry.search_rag("질문", ["docs"])      # RAG 검색
infos = registry.get_resource_infos()                       # UI 선택 목록
```

| 자산 | 로드 방식 | 실행 방식 |
|------|----------|----------|
| MCP 도구 | ServiceProvider.mcp.list_tools() | ServiceProvider.mcp.call_tool() |
| API 도구 | 워크플로우 노드(api_calling_tool) 자동 추출 | httpx POST/GET + response_filter |
| DB 도구 | 워크플로우 노드(postgresql_query 등) 추출 | ServiceProvider.database |
| Gallery 도구 | pip 패키지 + entry_points 자동 발견 | call_tool() 디스패처 |
| RAG 컬렉션 | ServiceProvider.documents.list_collections() | ServiceProvider.documents.search() |

## LLM 프로바이더 — xgen 친화적

### xgen LLM 그대로 끼우기 (LangChain 호환)

```python
from langchain_anthropic import ChatAnthropic
from xgen_harness.providers import wrap_langchain

llm = ChatAnthropic(model="claude-sonnet-4-20250514", api_key=api_key)
state.provider = wrap_langchain(llm)  # 끝
```

### XgenAdapter에 팩토리 주입

```python
adapter = XgenAdapter(
    db_manager=db_manager,
    llm_factory=my_create_llm_function,  # xgen 기존 함수 그대로
)
```

### 프로바이더 레지스트리 (5종 빌트인 + 커스텀)

```python
from xgen_harness.providers import register_provider, create_provider, list_providers

list_providers()  # ['anthropic', 'openai', 'google', 'bedrock', 'vllm']

# 커스텀 프로바이더 등록
register_provider("my_provider", MyProviderClass)
provider = create_provider("my_provider", api_key, model)
```

| 프로바이더 | 구현 | 비고 |
|-----------|------|------|
| anthropic | AnthropicProvider (httpx SSE) | Messages API, prompt caching, extended thinking |
| openai | OpenAIProvider (httpx SSE) | Chat Completions, tool_calls |
| google | OpenAI 호환 | Gemini via OpenAI endpoint |
| bedrock | OpenAI 호환 | AWS Bedrock via proxy |
| vllm | OpenAI 호환 | vLLM local endpoint |
| *LangChain* | LangChainAdapter | ChatAnthropic/ChatOpenAI/ChatBedrock 등 래핑 |

## 도구 개발 — Gallery Tool 표준

외부 개발자가 도구를 만들어 하네스에 끼우는 표준 포맷. 자세한 건 [TOOL_GUIDE.md](TOOL_GUIDE.md) 참고.

```python
# my_tool/__init__.py

TOOL_DEFINITIONS = [
    {"name": "search", "description": "검색", "input_schema": {...}},
]

def call_tool(name: str, args: dict) -> dict:
    return {"content": "결과", "is_error": False}
```

```python
# 하네스에서 로드
from xgen_harness.tools.gallery import load_tool_package
tools = load_tool_package("my_tool")
```

entry_points 등록하면 `pip install`만으로 자동 발견:
```toml
[project.entry-points."xgen_harness.tools"]
my_tool = "my_tool:get_tool_spec"
```

## 아키텍처

```
┌─────────────────────────────────────────────────┐
│                  Pipeline                        │
│                                                  │
│  Phase A: Ingress (1회)                          │
│  ┌──────┐ ┌──────┐ ┌───────────┐ ┌──────────┐  │
│  │Input │→│Memory│→│Sys Prompt │→│Tool Index│  │
│  │  s01 │ │  s02 │ │    s03    │ │   s04    │  │
│  └──────┘ └──────┘ └───────────┘ └──────────┘  │
│                                                  │
│  Phase B: Agentic Loop (N회 반복)                │
│  ┌────┐ ┌───────┐ ┌───┐ ┌───────┐ ┌────────┐  │
│  │Plan│→│Context│→│LLM│→│Execute│→│Validate│  │
│  │ s05│ │  s06  │ │s07│ │  s08  │ │  s09   │  │
│  └────┘ └───────┘ └───┘ └───────┘ └────────┘  │
│                        ↓                         │
│                    ┌──────┐                      │
│                    │Decide│ ←── Guard 체인        │
│                    │ s10  │     + 루프 판단       │
│                    └──────┘                      │
│                                                  │
│  Phase C: Egress (1회)                           │
│  ┌────┐ ┌────────┐                              │
│  │Save│→│Complete│                              │
│  │ s11│ │  s12   │                              │
│  └────┘ └────────┘                              │
└─────────────────────────────────────────────────┘
```

## 확장 포인트

| 확장 대상 | 방법 | 코드 수정 필요 |
|----------|------|-------------|
| 새 프로바이더 | `register_provider("name", Class)` | 없음 |
| 새 서비스 | ServiceProvider 프로토콜 추가 | 프로토콜만 |
| 새 도구 타입 | ResourceRegistry에 로더 추가 | 로더만 |
| 새 외부 시스템 | `adapters/` 아래 어댑터 추가 | 어댑터만 |
| 새 Strategy | StrategyResolver에 등록 | 없음 |
| 새 Preset | presets.py에 추가 | 1곳만 |
| 새 Guard | GuardChain에 추가 | 없음 |
| Gallery 도구 | `TOOL_DEFINITIONS` + `call_tool()` → pip install | 없음 |

## ServiceProvider — 플러거블 서비스

```python
from xgen_harness import ServiceProvider, NullServiceProvider
from xgen_harness.integrations.xgen_services import XgenServiceProvider

# xgen 환경 (DB/Config/MCP/Documents 자동 연결)
services = XgenServiceProvider.create(db_manager=db_manager)

# 독립 실행 (서비스 없이)
services = NullServiceProvider()
```

| 서비스 | 프로토콜 | xgen 구현체 | 용도 |
|--------|---------|-----------|------|
| DatabaseService | insert/find/upsert | XgenDatabaseService | s11_save, 세션 저장 |
| ConfigService | get_value/get_api_key | XgenConfigService | API 키, 설정 조회 |
| MCPService | list_sessions/list_tools/call_tool | XgenMCPService | MCP 도구 디스커버리/실행 |
| DocumentService | search/list_collections | XgenDocumentService | RAG 검색 |

## 12개 Stage / 27개 Strategy

| # | Stage | 역할 | Phase |
|---|-------|------|-------|
| 1 | **Input** | 입력 검증 + Provider 초기화 + MCP 도구 탐색 | Ingress |
| 2 | **Memory** | 대화 이력/이전 결과 주입 | Ingress |
| 3 | **System Prompt** | 시스템 프롬프트 조립 + RAG + 캐싱 | Ingress |
| 4 | **Tool Index** | 도구 색인 (Progressive Disclosure) | Ingress |
| 5 | **Plan** | 실행 계획 수립 (선택적) | Loop |
| 6 | **Context** | 컨텍스트 수집 + 토큰 예산 관리 | Loop |
| 7 | **LLM** | LLM 호출 (스트리밍/재시도/비용추적/thinking) | Loop |
| 8 | **Execute** | 도구 실행 (ResourceRegistry 통합) | Loop |
| 9 | **Validate** | 응답 품질 검증 (LLM Judge / Rule-based) | Loop |
| 10 | **Decide** | Guard 체인 + 루프 판단 | Loop |
| 11 | **Save** | 실행 결과 저장 | Egress |
| 12 | **Complete** | 메트릭 수집 + 종료 이벤트 | Egress |

## Preset 시스템

| Preset | 용도 | 활성 스테이지 |
|--------|------|-------------|
| `minimal` | 단순 질의응답 | s01,s03,s07,s10,s12 |
| `chat` | 멀티턴 대화 | + s02 |
| `agent` | 에이전트 | 전체 12개 |
| `evaluator` | 품질 검증 | 전체 + LLM Judge |
| `rag` | 문서 검색 | s01,s02,s03,s06,s07,s10,s11,s12 |

## 디렉토리 구조

```
xgen_harness/
├── core/                    # 핵심 엔진
│   ├── pipeline.py          # 3-Phase 실행 엔진
│   ├── stage.py             # Stage ABC + I/O 계약
│   ├── state.py             # PipelineState (40+ 필드)
│   ├── config.py            # HarnessConfig
│   ├── services.py          # ServiceProvider 프로토콜
│   ├── strategy_resolver.py # 27개 Strategy 레지스트리
│   ├── presets.py           # 5개 Preset
│   ├── builder.py           # PipelineBuilder (Fluent API)
│   ├── session.py           # 멀티턴 세션 + DB 저장
│   ├── registry.py          # ArtifactRegistry
│   └── artifact.py          # Artifact 시스템
│
├── stages/                  # 12개 스테이지 구현체
│   ├── s01_input.py ~ s12_complete.py
│   ├── interfaces.py        # Strategy ABC 7종
│   └── strategies/          # 27개 Strategy 구현체
│
├── providers/               # LLM 프로바이더
│   ├── __init__.py          # 레지스트리 (register/create/wrap_langchain)
│   ├── base.py              # LLMProvider ABC + ProviderEvent
│   ├── anthropic.py         # Anthropic (httpx SSE)
│   ├── openai.py            # OpenAI (httpx SSE)
│   └── langchain_adapter.py # LangChain BaseChatModel 래핑
│
├── adapters/                # 외부 시스템 어댑터
│   ├── xgen.py              # XgenAdapter (xgen-workflow ↔ 하네스)
│   └── resource_registry.py # ResourceRegistry (MCP/API/DB/Gallery/RAG 통합)
│
├── tools/                   # 도구 시스템
│   ├── base.py              # Tool ABC + ToolResult
│   ├── builtin.py           # discover_tools (Progressive Disclosure)
│   ├── mcp_client.py        # MCP 서버 통신
│   └── gallery.py           # Gallery Tool 표준 (ToolPackageSpec + 자동 로더)
│
├── integrations/            # xgen 생태계 연동
│   ├── xgen_services.py     # XgenServiceProvider (DB/Config/MCP/Documents)
│   ├── workflow_bridge.py   # execute_via_python_pipeline()
│   └── xgen_streaming.py    # HarnessEvent → xgen SSE 변환
│
├── events/                  # 이벤트 스트리밍 (10종)
├── errors/                  # 에러 계층 (10개 서브클래스)
├── orchestrator/            # DAG 멀티에이전트
└── api/                     # FastAPI 라우터
```

## 빠른 시작

### 독립 실행

```python
from xgen_harness import Pipeline, PipelineState, HarnessConfig, EventEmitter
from xgen_harness.core.presets import apply_preset

config = HarnessConfig(provider="openai", model="gpt-4o-mini")
apply_preset(config, "minimal")

emitter = EventEmitter()
pipeline = Pipeline.from_config(config, emitter)
state = PipelineState(user_input="안녕하세요")

await pipeline.run(state)
print(state.final_output)
```

### xgen-workflow 연동

```python
from xgen_harness.adapters.xgen import XgenAdapter

adapter = XgenAdapter(db_manager=db_manager)
async for event in adapter.execute(workflow_data, input_data, user_id=1):
    yield event
```

### xgen LLM 사용

```python
from xgen_harness.providers import wrap_langchain
from langchain_anthropic import ChatAnthropic

llm = ChatAnthropic(model="claude-sonnet-4-20250514", api_key="...")
state.provider = wrap_langchain(llm)
```

## 의존성

```
httpx >= 0.27
```

Pure Python. LLM SDK/LangChain 의존 없음 (LangChainAdapter는 선택적).
