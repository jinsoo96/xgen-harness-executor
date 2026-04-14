# xgen-harness-executor

**하네스 실행기** — 12단계 파이프라인 기반 에이전트 실행 엔진

Stage가 고정된 인터페이스를 갖고, 내부 Strategy를 갈아끼워서 동작을 커스텀하는 **Stage×Strategy 이중 추상화** 아키텍처.

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
│  ┌───────┐ ┌───┐ ┌───────┐ ┌────────┐          │
│  │Context│→│LLM│→│Execute│→│Validate│          │
│  │  s06  │ │s07│ │  s08  │ │  s09   │          │
│  └───────┘ └───┘ └───────┘ └────────┘          │
│                        ↓                         │
│                    ┌──────┐                      │
│                    │Decide│ ←── continue?         │
│                    │ s10  │                      │
│                    └──────┘                      │
│                                                  │
│  Phase C: Egress (1회)                           │
│  ┌────┐ ┌────────┐                              │
│  │Save│→│Complete│                              │
│  │ s11│ │  s12   │                              │
│  └────┘ └────────┘                              │
└─────────────────────────────────────────────────┘
```

## 12개 Stage

| # | Stage | 역할 | Phase |
|---|-------|------|-------|
| 1 | **Input** | 입력 검증 + Provider 초기화 | Ingress |
| 2 | **Memory** | 대화 이력 주입 | Ingress |
| 3 | **System Prompt** | 시스템 프롬프트 조립 | Ingress |
| 4 | **Tool Index** | 도구 색인 (Progressive Disclosure) | Ingress |
| 5 | **Plan** | 실행 계획 수립 (선택적) | Loop |
| 6 | **Context** | 컨텍스트 수집 + 토큰 예산 관리 | Loop |
| 7 | **LLM** | LLM 호출 (스트리밍) | Loop |
| 8 | **Execute** | 도구 실행 (MCP/API/빌트인) | Loop |
| 9 | **Validate** | 응답 품질 검증 | Loop |
| 10 | **Decide** | 루프 계속/완료 판단 | Loop |
| 11 | **Save** | 실행 결과 저장 | Egress |
| 12 | **Complete** | 메트릭 수집 + 종료 | Egress |

> s05 Plan은 선택적 스테이지로, 기본적으로 bypass됩니다. CoT 계획이 필요한 경우에만 활성화합니다. geny-harness에는 없는 스테이지이며, 필요 시 제거 가능합니다.

## Stage×Strategy 이중 추상화

```
Level 1 — Stage (인터페이스 고정)
  "Input 스테이지는 user_input을 받아서 provider를 초기화한다"

Level 2 — Strategy (갈아끼기)
  "Validate 스테이지에서 llm_judge 대신 rule_based로 검증한다"
```

### 등록된 Strategy (26개)

geny-harness의 Guard/Cache/Token/Think/Parse 패턴을 차용하여 확장.

| Stage | Slot | Strategy | 설명 |
|-------|------|----------|------|
| s03 System Prompt | cache | `anthropic_cache` | Anthropic prompt caching 마커 적용 |
| | | `no_cache` | 캐싱 비활성화 |
| s04 Tool Index | discovery | `progressive_3level` | 3단계 점진적 디스커버리 (기본) |
| | | `eager_load` | 모든 도구 스키마 즉시 로드 |
| s06 Context | compactor | `token_budget` | 토큰 예산 기반 3단계 압축 |
| | | `sliding_window` | 슬라이딩 윈도우 (최근 N개) |
| s07 LLM | retry | `exponential_backoff` | 429/529 지수 백오프 재시도 |
| | | `no_retry` | 재시도 없음 |
| | token_tracker | `default` | API 응답에서 토큰 사용량 추적 |
| | cost_calculator | `model_pricing` | 12개 모델 가격 테이블 기반 비용 계산 |
| | thinking | `default` | extended thinking block 처리 + 이벤트 |
| | | `disabled` | thinking 비활성화 |
| | parser | `anthropic` | Anthropic 응답 파싱 (content blocks) |
| | | `openai` | OpenAI 응답 파싱 (choices/tool_calls) |
| | completion_detector | `default` | end_turn/stop 완료 신호 감지 |
| s08 Execute | executor | `sequential` | 순차 도구 실행 |
| | | `parallel` | 병렬 도구 실행 |
| | router | `composite` / `mcp` / `builtin` | 도구 라우팅 |
| s09 Validate | evaluation | `llm_judge` | 독립 LLM 4가지 기준 평가 |
| | | `rule_based` | 규칙 기반 (길이/키워드) |
| | | `none` | 검증 비활성화 |
| s10 Decide | decide | `threshold` | Guard 체인 + 점수 기반 판단 |
| | | `always_pass` | 항상 완료 (루프 없음) |
| 공통 | scorer | `weighted` | 가중평균 점수 계산 |

### Guard 체인 (geny s04_guard 차용)

s10 Decide에서 하드코딩 가드레일 대신 Guard 체인을 사용:

```python
chain = GuardChain()
chain.add(IterationGuard())    # 반복 횟수 초과
chain.add(CostBudgetGuard())   # 비용 예산 초과
chain.add(TokenBudgetGuard())  # 토큰 예산 초과
chain.add(ContentGuard())      # 콘텐츠 필터링 (확장용)

results = chain.check_all(state)
# → [GuardResult(passed=True/False, guard_name, reason, severity)]
```

Guard는 Strategy ABC를 구현하므로 커스텀 Guard를 만들어서 체인에 추가 가능.

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

## Artifact 시스템

```python
store = ArtifactStore()
custom = store.clone("s07_llm", "default", "my_streaming_v2")
custom.config["temperature"] = 0.3
store.register(custom)
```

- `is_verified=False` — 새로 만든 Artifact는 검증 후 사용
- `parent_artifact` — 원본 추적

## Preset 시스템

| Preset | 설명 | 비활성 Stage |
|--------|------|-------------|
| `minimal` | 최소 채팅 | s02, s04, s05, s06, s08, s09, s11 |
| `chat` | 대화형 (메모리 포함) | s04, s05, s06, s08, s09, s11 |
| `agent` | 에이전트 (전체) | 없음 |
| `evaluator` | 평가형 (LLM Judge) | 없음 |
| `rag` | RAG 전용 | s04, s05, s08, s09 |

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

## 디렉토리 구조

```
xgen_harness/
├── core/                    # 핵심 엔진
│   ├── pipeline.py          # 3-Phase 실행 엔진
│   ├── stage.py             # Stage ABC + I/O + Strategy
│   ├── stage_io.py          # 12개 스테이지 I/O 계약
│   ├── state.py             # PipelineState
│   ├── config.py            # HarnessConfig
│   ├── artifact.py          # Artifact Store
│   ├── strategy_resolver.py # Strategy Resolver (26개)
│   ├── presets.py           # 5개 Preset
│   ├── registry.py          # ArtifactRegistry
│   ├── builder.py           # PipelineBuilder
│   └── session.py           # 멀티턴 세션
│
├── stages/                  # 12개 스테이지 구현체
│   ├── s01_input.py ~ s12_complete.py
│   ├── interfaces.py        # Strategy ABC 7종
│   └── strategies/          # Strategy 구현체 (26개)
│       ├── guard.py         # GuardChain + 4 Guards (geny s04)
│       ├── cache.py         # Prompt Caching (geny s05)
│       ├── token_tracker.py # Token Tracker + Cost Calculator (geny s07)
│       ├── thinking.py      # Thinking Processor (geny s08)
│       ├── parser.py        # Response Parser (geny s09)
│
├── providers/               # LLM 프로바이더
│   ├── anthropic.py         # Anthropic (httpx SSE)
│   └── openai.py            # OpenAI 호환
│
├── tools/                   # 도구 시스템
│   ├── builtin.py           # discover_tools
│   └── mcp_client.py        # MCP 서버 통신
│
├── events/                  # 이벤트 스트리밍
│   ├── emitter.py           # EventEmitter (AsyncIO)
│   └── types.py             # 이벤트 타입
│
├── errors/                  # 에러 계층
│   └── hierarchy.py
│
├── orchestrator/            # 멀티 에이전트
│   ├── dag.py               # DAG Orchestrator
│   └── multi_agent.py       # 워크플로우 → DAG
│
└── integrations/            # 외부 연동 (이식 시 사용)
    ├── config_client.py
    ├── workflow_bridge.py
    └── xgen_streaming.py
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

await pipeline.run(state)
print(state.final_output)
```

## 의존성

```
httpx >= 0.27
```

Pure Python. LLM SDK/LangChain 의존 없음.
