# xgen-harness-executor

**하네스 실행기** — 12단계 파이프라인 기반 에이전트 실행 엔진

## 하네스 엔지니어링이란

에이전트가 사용자 입력을 받아 최종 응답을 생성하기까지의 과정을 **구조적으로 제어**하는 엔지니어링.

기존 캔버스 방식은 노드를 드래그해서 연결하는 비구조적 접근이었다. 하네스 엔지니어링은 이를 **고정된 12단계 파이프라인**으로 대체한다. 각 단계는 명확한 입출력 계약을 갖고, 내부 로직만 Strategy로 갈아끼울 수 있다.

핵심 원칙:
- **단계는 고정, 로직은 교체** — Stage 인터페이스는 바뀌지 않고, 내부 Strategy만 선택
- **하드코딩 금지** — 모든 판단 로직은 Strategy로 분리. 실행기는 오케스트레이션만 담당
- **Progressive Disclosure** — 에이전트에게 모든 정보를 한 번에 주지 않고, 필요할 때 점진적으로 공개
- **Guard 체인** — 비용/토큰/반복 등 가드레일을 체인으로 구성. 런타임에 추가/제거 가능
- **확장성** — 새 Strategy를 만들어 등록하면 기존 코드 수정 없이 동작 변경

## 왜 이 구조인가

Anthropic의 에이전트 루프, OpenAI의 tool calling 패턴, Claude Code의 샌드박스 실행 — 이런 것들이 공통으로 갖는 구조가 있다:

```
입력 정규화 → 컨텍스트 조립 → LLM 호출 → 도구 실행 → 검증 → 반복 판단
```

이 흐름을 12개 Stage로 정형화한 것이 하네스 실행기다. 각 Stage는 단독으로 테스트 가능하고, Strategy를 교체하면 에이전트의 성격이 바뀐다.

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
│                    │Decide│ ←── Guard 체인        │
│                    │ s10  │     + 루프 판단       │
│                    └──────┘                      │
│                                                  │
│  Phase C: Egress (1회)                           │
│  ┌────┐ ┌────────┐                              │
│  │Save│→│Complete│                              │
│  └────┘ └────────┘                              │
└─────────────────────────────────────────────────┘
```

## Stage×Strategy 이중 추상화

```
Level 1 — Stage (인터페이스 고정)
  각 Stage는 뭘 받고(Input) 뭘 내보내는지(Output) 계약이 정해져 있다.
  이 계약은 바뀌지 않는다.

Level 2 — Strategy (로직 교체)
  같은 Stage 안에서 Strategy를 바꾸면 동작이 달라진다.
  예: Validate 스테이지에서 llm_judge → rule_based로 교체
```

### 12개 Stage

| # | Stage | 역할 | Phase | I/O |
|---|-------|------|-------|-----|
| 1 | **Input** | 입력 검증 + Provider 초기화 | Ingress | IN: user_input → OUT: provider |
| 2 | **Memory** | 대화 이력 주입 | Ingress | MOD: messages |
| 3 | **System Prompt** | 시스템 프롬프트 조립 + 캐싱 | Ingress | OUT: system_prompt |
| 4 | **Tool Index** | 도구 색인 (Progressive Disclosure) | Ingress | OUT: tool_index, tool_schemas |
| 5 | **Plan** | 실행 계획 수립 (선택적, 기본 bypass) | Loop | MOD: system_prompt |
| 6 | **Context** | 컨텍스트 수집 + 토큰 예산 관리 | Loop | MOD: system_prompt, messages |
| 7 | **LLM** | LLM 호출 (스트리밍/토큰추적/파싱/thinking) | Loop | OUT: last_assistant_text |
| 8 | **Execute** | 도구 실행 (MCP/API/빌트인) | Loop | OUT: tool_results |
| 9 | **Validate** | 응답 품질 검증 | Loop | OUT: validation_score |
| 10 | **Decide** | Guard 체인 + 루프 판단 | Loop | OUT: loop_decision |
| 11 | **Save** | 실행 결과 저장 | Egress | - |
| 12 | **Complete** | 메트릭 수집 + 종료 | Egress | OUT: final_output |

### 26개 Strategy

| Stage | Slot | Strategy | 하네스 엔지니어링 관점 |
|-------|------|----------|----------------------|
| s03 | cache | `anthropic_cache` | 동일 프롬프트 재사용 시 비용 90% 절감 |
| | | `no_cache` | 캐싱 비활성화 |
| s04 | discovery | `progressive_3level` | 에이전트가 필요한 도구만 점진적으로 발견 |
| | | `eager_load` | 전체 도구 스키마 즉시 로드 |
| s06 | compactor | `token_budget` | 컨텍스트 윈도우 초과 방지 3단계 압축 |
| | | `sliding_window` | 최근 N개 메시지만 유지 |
| s07 | retry | `exponential_backoff` | 429/529 에러 시 지수 백오프 재시도 |
| | | `no_retry` | 재시도 없음 |
| | token_tracker | `default` | API 응답에서 토큰 사용량 추출 |
| | cost_calculator | `model_pricing` | 프로바이더/모델별 USD 비용 계산 (12개 모델) |
| | thinking | `default` | extended thinking block 처리 + 이벤트 발행 |
| | | `disabled` | thinking 비활성화 |
| | parser | `anthropic` | Anthropic 응답 파싱 (content blocks) |
| | | `openai` | OpenAI 응답 파싱 (choices/tool_calls) |
| | completion_detector | `default` | end_turn/stop 완료 신호 감지 |
| s08 | executor | `sequential` | 순차 도구 실행 |
| | | `parallel` | 병렬 도구 실행 (읽기 병렬, 쓰기 순차) |
| | router | `composite` / `mcp` / `builtin` | 도구 이름 → 실행 경로 라우팅 |
| s09 | evaluation | `llm_judge` | 독립 LLM으로 4가지 기준 평가 |
| | | `rule_based` | 규칙 기반 빠른 검증 (LLM 비용 없음) |
| | | `none` | 검증 비활성화 |
| s10 | decide | `threshold` | Guard 체인 + 도구/점수 기반 판단 |
| | | `always_pass` | 항상 완료 (1회 실행, 루프 없음) |
| 공통 | scorer | `weighted` | 가중평균 점수 계산 |

## Guard 체인

실행 중 안전장치. 하드코딩이 아니라 **체인으로 구성**되어 런타임에 추가/제거 가능.

```python
chain = GuardChain()
chain.add(IterationGuard())    # 반복 횟수 초과 → 강제 종료
chain.add(CostBudgetGuard())   # 비용 예산 초과 → 강제 종료
chain.add(TokenBudgetGuard())  # 토큰 예산 95% 초과 → 강제 종료, 80% → 경고
chain.add(ContentGuard())      # 콘텐츠 필터링 (PII, 금지어 등 — 확장용)

results = chain.check_all(state)  # 첫 차단에서 short-circuit
```

Guard는 Strategy ABC를 구현하므로 커스텀 Guard를 만들어서 체인에 추가 가능. 예: `PermissionGuard`, `RateLimitGuard`, `TopicGuard`.

## Progressive Disclosure

에이전트에게 모든 도구를 한 번에 주면 토큰 낭비 + 혼란. 3단계로 점진 공개:

```
Level 1: 도구 메타데이터 (이름 + 설명) → 시스템 프롬프트에 삽입
         에이전트가 "이런 도구가 있구나" 인지

Level 2: discover_tools 빌트인 도구 → 에이전트가 호출하면 상세 스키마 반환
         에이전트가 "이 도구는 이런 파라미터를 받는구나" 파악

Level 3: 실제 도구 실행 (s08 Execute)
         에이전트가 구체적 파라미터로 호출
```

이 패턴은 Anthropic의 computer use, Claude Code의 도구 탐색과 동일한 접근.

## Stage I/O 계약

각 Stage가 뭘 받고 뭘 내보내는지 정형화. Pipeline이 실행 전에 검증.

```python
# s07_llm의 I/O 계약
input:  requires=["provider", "messages"]
        optional=["system_prompt", "tool_definitions"]
output: produces=["last_assistant_text"]
        modifies=["messages", "pending_tool_calls", "token_usage"]
        events=["MessageEvent", "ThinkingEvent"]
```

Artifact를 갈아끼워도 I/O 계약은 동일. 이게 확장성의 핵심.

## Artifact 시스템

Stage의 구현체를 복사해서 커스텀 버전을 만드는 시스템.

```python
store = ArtifactStore()
custom = store.clone("s07_llm", "default", "low_temp_v2")
custom.config["temperature"] = 0.1
store.register(custom)  # is_verified=False — 검증 후 사용
```

새로 만든 Artifact는 바로 프로덕션에 못 씀. `is_verified=True`로 승인 후 사용.

## Preset 시스템

Stage 활성/비활성 + Strategy 조합을 일괄 적용.

| Preset | 용도 | 핵심 차이 |
|--------|------|----------|
| `minimal` | 단순 질의응답 | 도구/RAG/검증 없음, 1회 실행 |
| `chat` | 멀티턴 대화 | 메모리 포함, 도구 없음 |
| `agent` | 에이전트 | 전체 12단계 활성, 도구 루프 |
| `evaluator` | 품질 검증 | LLM Judge 활성, 엄격 threshold |
| `rag` | 문서 검색 | RAG 활성, 도구 없음 |

## DAG Orchestrator (멀티 에이전트)

단일 파이프라인이 아니라 여러 에이전트를 DAG로 연결:

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
- loop_decision에 `escalate` 옵션 — 상위 오케스트레이터에 에스컬레이션

## 디렉토리 구조

```
xgen_harness/
├── core/                    # 핵심 엔진
│   ├── pipeline.py          # 3-Phase 실행 엔진
│   ├── stage.py             # Stage ABC (I/O 계약 + Strategy resolve)
│   ├── stage_io.py          # 12개 스테이지 I/O 선언
│   ├── state.py             # PipelineState (실행 컨텍스트)
│   ├── config.py            # HarnessConfig
│   ├── artifact.py          # Artifact Store (clone/register/verify)
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
│       ├── guard.py         # Guard 체인 (Iteration/Cost/Token/Content)
│       ├── cache.py         # Prompt Caching (Anthropic)
│       ├── token_tracker.py # Token Tracker + Cost Calculator
│       ├── thinking.py      # Extended Thinking Processor
│       ├── parser.py        # Response Parser (Anthropic/OpenAI)
│       ├── retry.py         # Retry (ExponentialBackoff)
│       ├── tool_router.py   # Tool Router (Composite/MCP/Builtin)
│       ├── tool_executor.py # Tool Executor (Sequential/Parallel)
│       ├── evaluation.py    # Evaluation (LLMJudge/RuleBased)
│       ├── discovery.py     # Tool Discovery (Progressive/Eager)
│       ├── compactor.py     # Context Compactor (TokenBudget/SlidingWindow)
│       └── scorer.py        # Quality Scorer (Weighted)
│
├── providers/               # LLM 프로바이더
│   ├── anthropic.py         # Anthropic (httpx SSE)
│   └── openai.py            # OpenAI 호환
│
├── tools/                   # 도구 시스템
│   ├── builtin.py           # discover_tools (Progressive Disclosure Level 2)
│   └── mcp_client.py        # MCP 서버 통신
│
├── events/                  # 이벤트 스트리밍
│   ├── emitter.py           # EventEmitter (AsyncIO Queue)
│   └── types.py             # 이벤트 타입
│
├── errors/                  # 에러 계층
│   └── hierarchy.py         # HarnessError + 5 서브클래스
│
├── orchestrator/            # 멀티 에이전트
│   ├── dag.py               # DAG Orchestrator (토폴로지 정렬 + 병렬)
│   └── multi_agent.py       # 워크플로우 데이터 → DAG 자동 변환
│
└── integrations/            # 외부 연동 (이식 시 사용)
    ├── config_client.py     # 외부 config API 연동
    ├── workflow_bridge.py   # 워크플로우 데이터 변환
    └── xgen_streaming.py    # SSE 이벤트 변환
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
