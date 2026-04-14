# xgen-harness-executor

**xgen 하네스 실행기** — 12단계 파이프라인 기반 에이전트 실행 엔진

기존 캔버스 워크플로우(노드 연결 방식)를 대체하는 구조적 실행 엔진.
각 Stage가 고정된 인터페이스를 갖고, 내부 Artifact/Strategy를 갈아끼워서 동작을 커스텀하는 **Stage×Strategy 이중 추상화** 아키텍처.

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
│                              ↓                   │
│                          ┌──────┐                │
│                          │Decide│ ←── continue?  │
│                          │ s10  │                │
│                          └──────┘                │
│                                                  │
│  Phase C: Egress (1회)                           │
│  ┌────┐ ┌────────┐                              │
│  │Save│→│Complete│                              │
│  │ s11│ │  s12   │                              │
│  └────┘ └────────┘                              │
└─────────────────────────────────────────────────┘
```

## 12개 Stage

| # | Stage | 역할 | Phase | xgen 리소스 |
|---|-------|------|-------|-------------|
| 1 | **Input** | 입력 검증 + Provider 초기화 | Ingress | Provider/Model 선택 |
| 2 | **Memory** | 대화 이력 주입 | Ingress | interaction 이력 |
| 3 | **System Prompt** | 시스템 프롬프트 조립 | Ingress | 프롬프트 스토어 |
| 4 | **Tool Index** | 도구 색인 (Progressive Disclosure) | Ingress | MCP 세션 + 도구 저장소 |
| 5 | **Plan** | 실행 계획 수립 | Loop | - |
| 6 | **Context** | RAG 검색 + 토큰 예산 관리 | Loop | 문서 컬렉션 + DB 연결 |
| 7 | **LLM** | LLM 호출 (스트리밍) | Loop | - |
| 8 | **Execute** | 도구 실행 (MCP/API/빌트인) | Loop | - |
| 9 | **Validate** | 응답 품질 검증 | Loop | - |
| 10 | **Decide** | 루프 계속/완료 판단 | Loop | - |
| 11 | **Save** | 실행 결과 저장 | Egress | - |
| 12 | **Complete** | 메트릭 수집 + 종료 | Egress | - |

## Stage×Strategy 이중 추상화

```
Level 1 — Stage (인터페이스 고정)
  "Input 스테이지는 user_input을 받아서 provider를 초기화한다"

Level 2 — Strategy (갈아끼기)
  "Validate 스테이지에서 llm_judge 대신 rule_based로 검증한다"
```

### 등록된 Strategy (17개)

| Stage | Strategy | 설명 |
|-------|----------|------|
| s04 Tool Index | `progressive_3level` | 3단계 점진적 디스커버리 (기본) |
| | `eager_load` | 모든 도구 스키마 즉시 로드 |
| s06 Context | `token_budget` | 토큰 예산 기반 3단계 압축 |
| | `sliding_window` | 슬라이딩 윈도우 (최근 N개) |
| s07 LLM | `exponential_backoff` | 429/529 지수 백오프 재시도 |
| | `no_retry` | 재시도 없음 |
| s08 Execute | `sequential` | 순차 도구 실행 |
| | `parallel` | 병렬 도구 실행 |
| | `composite` / `mcp` / `builtin` | 도구 라우팅 |
| s09 Validate | `llm_judge` | 독립 LLM 4가지 기준 평가 |
| | `rule_based` | 규칙 기반 (길이/키워드) |
| | `none` | 검증 비활성화 |
| s10 Decide | `threshold` | 도구 호출 + 점수 기반 판단 |
| | `always_pass` | 항상 완료 (루프 없음) |

## Stage I/O 계약

각 Stage는 Input/Output을 정형화하여 선언합니다.

```python
# s07_llm
input:  requires=["provider", "messages"]
        optional=["system_prompt", "tool_definitions"]
output: produces=["last_assistant_text"]
        modifies=["messages", "pending_tool_calls", "token_usage"]
        events=["MessageEvent", "ThinkingEvent"]
```

Pipeline이 실행 전에 Input을 검증하고, 누락된 필드를 로깅합니다.

## Artifact 시스템

```python
# default Artifact를 복사해서 커스텀 Artifact 생성
store = ArtifactStore()
custom = store.clone("s07_llm", "default", "my_streaming_v2")
custom.config["temperature"] = 0.3
store.register(custom)

# 워크플로우에서 Artifact 선택
harness_config.artifacts = {"s07_llm": "my_streaming_v2"}
```

- `is_verified=False` — 새로 만든 Artifact는 검증 후 사용
- `parent_artifact` — 원본 추적
- DB 저장/로드 지원

## Preset 시스템

| Preset | 설명 | 활성 Stage |
|--------|------|-----------|
| `minimal` | 최소 채팅 | s01, s03, s07, s10, s12 |
| `chat` | 대화형 (메모리 포함) | s01, s02, s03, s07, s10, s12 |
| `agent` | 에이전트 (전체) | 12개 전부 |
| `evaluator` | 평가형 (LLM Judge) | 12개 전부 + llm_judge |
| `rag` | RAG 전용 | s01, s02, s03, s06, s07, s10, s12 |

## Progressive Disclosure

에이전트에게 모든 도구를 한 번에 주지 않고, 3단계로 점진적 공개:

```
Level 1: 도구 메타데이터 (이름 + 설명) → 시스템 프롬프트에 삽입
Level 2: discover_tools 빌트인 도구 → 에이전트가 호출하면 상세 스키마 반환
Level 3: 실제 도구 실행 (s08 Execute)
```

## DAG Orchestrator (멀티 에이전트)

```python
orch = DAGOrchestrator()
orch.add_node(AgentNode(node_id="researcher", config=...))
orch.add_node(AgentNode(node_id="writer", config=...))
orch.add_edge(DAGEdge(source="researcher", target="writer"))
result = await orch.run("사용자 질문")
```

- 토폴로지 정렬 (Kahn's algorithm)
- 같은 레벨 에이전트 병렬 실행
- 이전 출력 → 다음 입력 자동 연결
- 실시간 이벤트 스트리밍

## xgen 생태계 통합

각 Stage에서 xgen 플랫폼의 기존 리소스를 선택하여 사용:

| Stage | xgen 리소스 | API |
|-------|------------|-----|
| s01 Input | Provider/Model | 내장 |
| s03 System Prompt | 프롬프트 스토어 | `/api/prompt/list` |
| s04 Tool Index | MCP 세션 + 도구 저장소 | `/api/mcp/sessions` |
| s06 Context | 문서 컬렉션 + DB 연결 | `/api/retrieval/collections` |

스테이지를 클릭 → 리소스 선택 → Save → 실행.

## 디렉토리 구조

```
xgen_harness/
├── core/                    # 핵심 엔진
│   ├── pipeline.py          # 3-Phase 실행 엔진
│   ├── stage.py             # Stage ABC + I/O + Strategy resolve
│   ├── stage_io.py          # 12개 스테이지 I/O 계약
│   ├── state.py             # PipelineState (실행 컨텍스트)
│   ├── config.py            # HarnessConfig
│   ├── registry.py          # ArtifactRegistry
│   ├── artifact.py          # Artifact Store (clone/register)
│   ├── strategy_resolver.py # Strategy 이름 → 인스턴스 매핑
│   ├── presets.py           # 5개 Preset
│   ├── builder.py           # PipelineBuilder
│   ├── session.py           # 멀티턴 세션
│   └── stage_config.py      # 스테이지별 UI 설정 스키마
│
├── stages/                  # 12개 스테이지 구현체
│   ├── s01_input.py         # 입력 검증 + Provider 초기화
│   ├── s02_memory.py        # 대화 이력
│   ├── s03_system_prompt.py # 시스템 프롬프트 조립
│   ├── s04_tool_index.py    # Progressive Disclosure
│   ├── s05_plan.py          # 실행 계획
│   ├── s06_context.py       # RAG 검색 + 토큰 관리
│   ├── s07_llm.py           # LLM 호출 (스트리밍)
│   ├── s08_execute.py       # 도구 실행 (MCP/API/빌트인)
│   ├── s09_validate.py      # 응답 품질 검증
│   ├── s10_decide.py        # 루프 계속/완료 판단
│   ├── s11_save.py          # 실행 결과 저장
│   ├── s12_complete.py      # 메트릭 수집 + 종료
│   ├── interfaces.py        # Strategy ABC 7종
│   └── strategies/          # Strategy 구현체 (14개)
│       ├── retry.py         # ExponentialBackoff, NoRetry
│       ├── tool_router.py   # Composite, MCP, Builtin Router
│       ├── tool_executor.py # Sequential, Parallel Executor
│       ├── evaluation.py    # LLMJudge, RuleBased, NoValidation
│       ├── scorer.py        # WeightedScorer
│       ├── discovery.py     # Progressive, EagerLoad Discovery
│       ├── compactor.py     # TokenBudget, SlidingWindow Compactor
│       └── _decide.py       # Threshold, AlwaysPass Decide
│
├── providers/               # LLM 프로바이더
│   ├── base.py              # Provider ABC
│   ├── anthropic.py         # Anthropic (httpx SSE)
│   └── openai.py            # OpenAI 호환
│
├── tools/                   # 도구 시스템
│   ├── base.py              # Tool ABC
│   ├── builtin.py           # discover_tools 빌트인
│   └── mcp_client.py        # MCP 서버 통신
│
├── events/                  # 이벤트 시스템
│   ├── emitter.py           # EventEmitter (AsyncIO Queue)
│   └── types.py             # 이벤트 타입 정의
│
├── errors/                  # 에러 계층
│   └── hierarchy.py         # HarnessError, ConfigError, ...
│
├── orchestrator/            # 멀티 에이전트
│   ├── dag.py               # DAG Orchestrator
│   └── multi_agent.py       # 워크플로우 → DAG 자동 변환
│
├── integrations/            # xgen 생태계 연동
│   ├── config_client.py     # xgen-core config API
│   ├── workflow_bridge.py   # 워크플로우 데이터 변환
│   ├── xgen_streaming.py    # SSE 이벤트 변환
│   └── harness_router_patch.py
│
├── api/                     # FastAPI 라우터
│   └── router.py
│
└── web/                     # 독립 실행 데모
    ├── backend/main.py      # FastAPI + WebSocket (8088)
    └── frontend/            # React + Vite + Zustand
```

## 빠른 시작

```python
from xgen_harness import Pipeline, PipelineState, HarnessConfig, EventEmitter

config = HarnessConfig(
    provider="openai",
    model="gpt-4o-mini",
    active_strategies={"s10_decide": "always_pass"},
)

emitter = EventEmitter()
pipeline = Pipeline.from_config(config, emitter)
state = PipelineState(user_input="안녕하세요")

# 이벤트 스트리밍
async for event in emitter.stream():
    print(event)

await pipeline.run(state)
print(state.final_output)
```

## 의존성

```
httpx >= 0.27
```

그 외 없음. Pure Python. LLM/LangChain 의존 없음.

## 라이선스

Internal — Plateer Inc.
