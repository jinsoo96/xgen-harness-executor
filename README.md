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
| 1 | **Input** | 입력 검증 + Provider 초기화 + MCP 도구 탐색 | Ingress | IN: user_input → OUT: provider |
| 2 | **Memory** | 대화 이력/이전 결과 주입 | Ingress | MOD: messages |
| 3 | **System Prompt** | 시스템 프롬프트 조립 + 캐싱 | Ingress | OUT: system_prompt |
| 4 | **Tool Index** | 도구 색인 (Progressive Disclosure) | Ingress | OUT: tool_index, tool_schemas |
| 5 | **Plan** | 실행 계획 수립 (선택적, 기본 bypass) | Loop | MOD: system_prompt |
| 6 | **Context** | 컨텍스트 수집 + 토큰 예산 관리 | Loop | MOD: system_prompt, messages |
| 7 | **LLM** | LLM 호출 (스트리밍/토큰추적/파싱/thinking) | Loop | OUT: last_assistant_text |
| 8 | **Execute** | 도구 실행 (MCP/빌트인/레지스트리) | Loop | OUT: tool_results |
| 9 | **Validate** | 응답 품질 검증 | Loop | OUT: validation_score |
| 10 | **Decide** | Guard 체인 + 루프 판단 | Loop | OUT: loop_decision |
| 11 | **Save** | 실행 결과 저장 | Egress | - |
| 12 | **Complete** | 메트릭 수집 + 종료 이벤트 | Egress | OUT: final_output |

### 27개 Strategy (StrategyResolver 등록 기준)

| Stage | Slot | Strategy | 설명 |
|-------|------|----------|------|
| s03 | cache | `anthropic_cache` | Anthropic prompt caching (비용 90% 절감) |
| | | `no_cache` | 캐싱 비활성화 |
| s04 | discovery | `progressive_3level` | 에이전트가 필요한 도구만 점진적으로 발견 |
| | | `eager_load` | 전체 도구 스키마 즉시 로드 |
| s06 | compactor | `token_budget` | 컨텍스트 윈도우 초과 방지 3단계 압축 |
| | | `sliding_window` | 최근 N개 메시지만 유지 |
| s07 | retry | `exponential_backoff` | 429/529/5xx 에러 시 지수 백오프 재시도 |
| | | `no_retry` | 재시도 없음 |
| | token_tracker | `default` | API 응답에서 토큰 사용량 추출 |
| | cost_calculator | `model_pricing` | 프로바이더/모델별 USD 비용 계산 |
| | thinking | `default` | extended thinking block 처리 + 이벤트 발행 |
| | | `disabled` | thinking 비활성화 |
| | parser | `anthropic` | Anthropic 응답 파싱 (content blocks) |
| | | `openai` | OpenAI 응답 파싱 (choices/tool_calls) |
| | completion_detector | `default` | end_turn/stop 완료 신호 감지 |
| s08 | executor | `sequential` | 순차 도구 실행 |
| | | `parallel` | 병렬 도구 실행 (읽기 병렬, 쓰기 순차) |
| | router | `composite` | MCP + 빌트인 + 레지스트리 통합 라우팅 |
| | | `mcp` | MCP 서버 통신 전용 |
| | | `builtin` | 빌트인 도구 전용 (discover_tools 등) |
| s09 | evaluation | `llm_judge` | 독립 LLM으로 4가지 기준 평가 |
| | | `rule_based` | 규칙 기반 검증 (LLM 비용 없음) |
| | | `none` | 검증 비활성화 |
| s10 | decide | `threshold` | Guard 체인 + 도구/점수 기반 판단 |
| | | `always_pass` | 항상 완료 (1회 실행, 루프 없음) |
| 공통 | scorer | `weighted` | 가중평균 점수 계산 (와일드카드, 모든 스테이지에서 사용 가능) |

### Strategy 인터페이스 (ABC)

| 인터페이스 | 메서드 | 사용 스테이지 |
|-----------|--------|-------------|
| `Strategy` | `name`, `configure()` | 모든 Strategy의 기반 |
| `RetryStrategy` | `should_retry()`, `get_delay()`, `max_retries` | s07 |
| `ToolRouter` | `route()`, `list_available()` | s08 |
| `ToolExecutor` | `execute_all()` | s08 |
| `EvaluationStrategy` | `evaluate()` | s09 |
| `QualityScorer` | `score()` | 공통 |
| `ToolDiscoveryStrategy` | `discover()` | s04 |
| `ContextCompactor` | `compact()` | s06 |

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

Guard는 Strategy ABC를 구현하므로 커스텀 Guard를 만들어서 체인에 추가 가능.

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

## Stage I/O 계약

각 Stage가 뭘 받고 뭘 내보내는지 정형화. Pipeline이 실행 전에 `StageInput.validate()`로 검증.

```python
# s07_llm의 I/O 계약 (stage_io.py)
input:  requires=["provider", "messages"]
        optional=["system_prompt", "tool_definitions"]
output: produces=["last_assistant_text"]
        modifies=["messages", "pending_tool_calls", "token_usage", "cost_usd"]
        events=["MessageEvent", "ThinkingEvent"]
```

`StageInput`과 `StageOutput` 데이터클래스로 선언하며, 12개 스테이지 전체 스펙이 `STAGE_IO_SPECS` 딕셔너리에 등록되어 있다.

## Artifact 시스템

Stage의 구현체를 복사해서 커스텀 버전을 만드는 시스템.

```python
store = ArtifactStore()
custom = store.clone("s07_llm", "default", "low_temp_v2")
custom.config["temperature"] = 0.1
store.register(custom)  # is_verified=False — 검증 후 사용
```

`ArtifactRegistry`가 stage_id → {artifact_name → Stage class} 매핑을 관리하며, 스테이지 별칭도 지원한다 ("llm" / "LLM" / "7" → "s07_llm").

## Preset 시스템

스테이지 활성/비활성 + Strategy 조합을 일괄 적용.

| Preset | 용도 | 비활성화 스테이지 | 핵심 Strategy |
|--------|------|-----------------|-------------|
| `minimal` | 단순 질의응답 | s02,s04,s05,s06,s08,s09,s11 | decide: always_pass |
| `chat` | 멀티턴 대화 | s04,s05,s06,s08,s09,s11 | decide: always_pass |
| `agent` | 에이전트 | 없음 (전체 활성) | discovery: progressive_3level, decide: threshold |
| `evaluator` | 품질 검증 | 없음 | evaluation: llm_judge, threshold: 0.8 |
| `rag` | 문서 검색 | s04,s05,s08,s09 | decide: always_pass |

```python
from xgen_harness.core.presets import apply_preset

config = HarnessConfig()
apply_preset(config, "agent")  # 에이전트 프리셋 적용
```

## PipelineBuilder (Fluent API)

체이닝으로 파이프라인 구성. 도구/MCP/RAG/검증/thinking 등을 선언적으로 설정.

```python
from xgen_harness import PipelineBuilder

pipeline = (PipelineBuilder()
    .with_provider("anthropic", "claude-sonnet-4-20250514", api_key)
    .with_system("You are a helpful assistant.")
    .with_tools([weather_tool, search_tool])
    .with_mcp_sessions(["session-abc"])
    .with_rag(collection="docs", top_k=5)
    .with_validate(threshold=0.8)
    .with_thinking(budget_tokens=10000)
    .with_loop(max_iterations=10)
    .disable("s05_plan")
    .build())

state = pipeline.build_state("사용자 질문")
await pipeline.run(state)
```

## 멀티턴 세션

`HarnessSession`이 대화 이력과 설정을 유지. `SessionManager`로 여러 세션을 관리.

```python
from xgen_harness import HarnessSession, SessionManager

manager = SessionManager()
session = manager.create(config=HarnessConfig(provider="anthropic"))

# 연속 대화
result1 = await session.run("안녕하세요")
result2 = await session.run("아까 질문에 이어서...")

# 세션 직렬화/복원 (DB 저장 가능)
json_str = session.to_json()
restored = HarnessSession.from_json(json_str)
```

## DAG Orchestrator (멀티 에이전트)

단일 파이프라인이 아니라 여러 에이전트를 DAG로 연결:

```python
from xgen_harness import DAGOrchestrator, AgentNode, DAGEdge

orch = DAGOrchestrator()
orch.add_node(AgentNode(node_id="researcher", config=...))
orch.add_node(AgentNode(node_id="writer", config=...))
orch.add_edge(DAGEdge(source="researcher", target="writer"))
result = await orch.run("사용자 질문")
```

- 토폴로지 정렬 (Kahn's algorithm) → 같은 레벨 에이전트 병렬 실행
- 이전 출력 → 다음 입력 자동 연결 (input_transformer 커스텀 가능)
- `MultiAgentExecutor`로 워크플로우 JSON 데이터에서 DAG 자동 구성

## LLM 프로바이더

SDK 미사용. **httpx로 직접 API 호출** (의존성 최소화).

| 프로바이더 | 구현 | 비고 |
|-----------|------|------|
| Anthropic | `AnthropicProvider` | Messages API v2023-06-01, SSE 스트리밍, prompt caching 자동 설정, extended thinking 지원 |
| OpenAI | `OpenAIProvider` | Chat Completions API, tool_calls 파싱 |

`ProviderEvent` 스트리밍 이벤트: `TEXT_DELTA`, `THINKING_DELTA`, `TOOL_USE`, `USAGE`, `STOP`

### 비용 추적

`ModelPricingCalculator`가 모델별 USD 비용을 자동 계산:

| 모델 | 입력 ($/1M) | 출력 ($/1M) |
|------|-----------|-----------|
| Claude Sonnet | 3.0 | 15.0 |
| Claude Opus | 15.0 | 75.0 |
| Claude Haiku | 0.8 | 4.0 |
| GPT-4o | 2.5 | 10.0 |
| GPT-4o-mini | 0.15 | 0.6 |

### 재시도 전략

`ExponentialBackoffRetry` — 에러 타입별 백오프:
- 429 (Rate Limit): 10s → 20s → 40s
- 529 (Overload): 1s → 2s → 4s
- 5xx (Server): 2s → 4s → 8s

## 이벤트 시스템

`EventEmitter` (AsyncIO Queue)로 파이프라인 실행 이벤트를 실시간 스트리밍.

| 이벤트 | 발생 시점 | 주요 필드 |
|--------|---------|----------|
| `StageEnterEvent` | 스테이지 진입 | stage_id, phase, step/total |
| `StageExitEvent` | 스테이지 종료 | output, score |
| `MessageEvent` | LLM 텍스트 스트리밍 | text, is_final |
| `ThinkingEvent` | Extended thinking | text |
| `ToolCallEvent` | 도구 호출 | tool_name, tool_input |
| `ToolResultEvent` | 도구 결과 | tool_name, result, is_error |
| `EvaluationEvent` | 검증 결과 | score, verdict |
| `MetricsEvent` | 실행 메트릭 | duration_ms, total_tokens, cost_usd, model |
| `ErrorEvent` | 에러 발생 | message, recoverable |
| `DoneEvent` | 파이프라인 종료 | final_output, success |

## 에러 계층

`HarnessError` → `ErrorCategory` (RATE_LIMIT, OVERLOAD, TIMEOUT, AUTH, TOKEN_LIMIT 등) → 각 카테고리별 `recoverable` 속성.

| 에러 | 용도 |
|------|------|
| `ConfigError` | API 키 누락/잘못된 설정 |
| `ProviderError` | LLM API 에러 (HTTP 상태코드별 자동 분류) |
| `RateLimitError` | 429 응답 |
| `OverloadError` | 529 응답 |
| `ContextOverflowError` | 컨텍스트 윈도우 초과 |
| `ToolError` | 도구 실행 실패 |
| `ToolTimeoutError` | 도구 타임아웃 |
| `MCPConnectionError` | MCP 세션 연결 실패 |
| `ValidationError` | 품질 검증 실패 |
| `PipelineAbortError` | 비복구, 즉시 종료 |

## API 라우터 (xgen-workflow 통합용)

`harness_router`를 xgen-workflow FastAPI에 include하면 하네스 API가 활성화된다.

```python
# xgen-workflow main.py
from xgen_harness.api.router import harness_router
app.include_router(harness_router, prefix="/api/harness")
```

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/harness/stages` | 12개 스테이지 + required + UI 설정 스키마 |
| GET | `/api/harness/options/mcp-sessions` | MCP 세션 목록 (xgen-mcp-station 연동) |
| GET | `/api/harness/options/rag-collections` | RAG 컬렉션 목록 (xgen-documents 연동) |
| POST | `/api/harness/execute` | SSE 스트리밍 실행 |
| WS | `/api/harness/ws/{session_id}` | WebSocket 실행 |
| POST | `/api/harness/orchestrate` | 멀티에이전트 DAG SSE 실행 |

## xgen-workflow 통합 (integrations/)

| 모듈 | 역할 |
|------|------|
| `workflow_bridge.py` | `execute_via_python_pipeline()` — 워크플로우 데이터 → 파이프라인 실행 → xgen SSE 이벤트 변환 |
| `xgen_streaming.py` | `convert_to_xgen_event()` — HarnessEvent → xgen SSE 포맷 변환 |
| `config_client.py` | 외부 config API 연동 |
| `harness_router_patch.py` | xgen-workflow harness_router.py 패치 |

## 독립 Web UI

`web/` 디렉토리에 캔버스 대체 UI가 포함. 별도 배포 가능.

```
web/
├── backend/           — FastAPI 서버 (세션 관리 + WebSocket 실행)
│   ├── main.py        — API: /api/pipeline/describe, /api/stages,
│   │                    /api/sessions, /api/config/api-key, /ws/execute/{sid}
│   └── Dockerfile
├── frontend/          — React + Zustand + TailwindCSS
│   ├── src/
│   │   ├── App.tsx
│   │   ├── components/
│   │   │   ├── pipeline/   — PipelineView, StageDetailPanel
│   │   │   ├── execution/  — InputPanel, EventLog, ResultPanel
│   │   │   └── layout/     — Header, ConfigPanel, WorkflowSidebar
│   │   ├── stores/         — executionStore, pipelineStore, uiStore
│   │   ├── hooks/          — useWebSocket
│   │   └── types/          — pipeline.ts
│   └── Dockerfile
└── docker-compose.yml — 프론트엔드 + 백엔드 일괄 실행
```

실행:
```bash
cd web && docker compose up -d
# 프론트엔드: http://localhost:5173
# 백엔드: http://localhost:8100
```

## 디렉토리 구조

```
xgen-harness-executor/
├── pyproject.toml               # 패키지 설정 (Python ≥3.10, httpx ≥0.27)
├── test_pipeline.py             # 구조 검증 테스트 (API 호출 없이)
│
├── xgen_harness/                # 메인 라이브러리
│   ├── __init__.py              # 퍼블릭 API (Pipeline, PipelineState, HarnessConfig 등)
│   │
│   ├── core/                    # 핵심 엔진
│   │   ├── pipeline.py          # 3-Phase 실행 엔진 (Ingress → Loop → Egress)
│   │   ├── stage.py             # Stage ABC (I/O 계약 + Strategy resolve + get_param 3단계 폴백)
│   │   ├── stage_io.py          # 12개 스테이지 I/O 선언 (StageInput/StageOutput)
│   │   ├── stage_config.py      # UI 필드 스키마 (type, label, options, default)
│   │   ├── state.py             # PipelineState (실행 컨텍스트) + TokenUsage
│   │   ├── config.py            # HarnessConfig (ALL_STAGES, REQUIRED_STAGES)
│   │   ├── artifact.py          # ArtifactStore (clone/register/verify)
│   │   ├── registry.py          # ArtifactRegistry (스테이지 별칭 + 파이프라인 빌드)
│   │   ├── strategy_resolver.py # StrategyResolver (27개, 와일드카드 폴백)
│   │   ├── presets.py           # 5개 Preset (minimal/chat/agent/evaluator/rag)
│   │   ├── builder.py           # PipelineBuilder (Fluent API)
│   │   └── session.py           # HarnessSession + SessionManager (멀티턴 + DB 저장)
│   │
│   ├── stages/                  # 12개 스테이지 구현체
│   │   ├── s01_input.py         # 입력 검증 + Provider 초기화 + MCP 도구 탐색
│   │   ├── s02_memory.py        # 대화 이력 + 이전 결과 주입
│   │   ├── s03_system_prompt.py # 시스템 프롬프트 조립 + 캐싱
│   │   ├── s04_tool_index.py    # 도구 색인 (Progressive Disclosure)
│   │   ├── s05_plan.py          # 실행 계획 (선택적)
│   │   ├── s06_context.py       # 컨텍스트 + 토큰 예산
│   │   ├── s07_llm.py           # LLM 호출 (스트리밍 + 재시도 + 비용 추적 + thinking)
│   │   ├── s08_execute.py       # 도구 실행 (MCP/빌트인/레지스트리 디스패치)
│   │   ├── s09_validate.py      # 응답 품질 검증
│   │   ├── s10_decide.py        # Guard 체인 + 루프 판단
│   │   ├── s11_save.py          # 결과 저장
│   │   ├── s12_complete.py      # 메트릭 수집 + DoneEvent
│   │   ├── interfaces.py        # Strategy ABC 7종 + StrategySlot
│   │   └── strategies/          # Strategy 구현체
│   │       ├── guard.py         # Guard 체인 (Iteration/Cost/Token/Content)
│   │       ├── cache.py         # Prompt Caching (Anthropic/None)
│   │       ├── token_tracker.py # Token Tracker + Cost Calculator
│   │       ├── thinking.py      # Extended Thinking (Default/Disabled)
│   │       ├── parser.py        # Response Parser (Anthropic/OpenAI) + CompletionDetector
│   │       ├── retry.py         # Retry (ExponentialBackoff/NoRetry)
│   │       ├── tool_router.py   # Tool Router (Composite/MCP/Builtin)
│   │       ├── tool_executor.py # Tool Executor (Sequential/Parallel)
│   │       ├── evaluation.py    # Evaluation (LLMJudge/RuleBased/None)
│   │       ├── discovery.py     # Tool Discovery (Progressive/Eager)
│   │       ├── compactor.py     # Context Compactor (TokenBudget/SlidingWindow)
│   │       ├── scorer.py        # Quality Scorer (Weighted)
│   │       └── _decide.py       # Decide (Threshold/AlwaysPass)
│   │
│   ├── providers/               # LLM 프로바이더 (SDK 미사용, httpx 직접 호출)
│   │   ├── base.py              # LLMProvider ABC + ProviderEvent
│   │   ├── anthropic.py         # Anthropic Messages API (SSE + prompt caching + thinking)
│   │   └── openai.py            # OpenAI Chat Completions API
│   │
│   ├── tools/                   # 도구 시스템
│   │   ├── base.py              # Tool ABC + ToolResult
│   │   ├── builtin.py           # DiscoverToolsTool (Progressive Disclosure Level 2)
│   │   └── mcp_client.py        # MCP 서버 통신 (도구 탐색 + 실행)
│   │
│   ├── events/                  # 이벤트 스트리밍
│   │   ├── emitter.py           # EventEmitter (AsyncIO Queue + stream())
│   │   └── types.py             # 10종 이벤트 타입 + event_to_dict()
│   │
│   ├── errors/                  # 에러 계층
│   │   ├── __init__.py          # 퍼블릭 에러 export
│   │   └── hierarchy.py         # HarnessError + ErrorCategory + 10개 서브클래스
│   │
│   ├── orchestrator/            # 멀티 에이전트
│   │   ├── dag.py               # DAGOrchestrator (토폴로지 정렬 + 병렬 실행)
│   │   └── multi_agent.py       # MultiAgentExecutor (워크플로우 JSON → DAG 자동 변환)
│   │
│   ├── integrations/            # xgen 생태계 연동
│   │   ├── workflow_bridge.py   # execute_via_python_pipeline() — xgen-workflow 브릿지
│   │   ├── xgen_streaming.py    # HarnessEvent → xgen SSE 변환
│   │   ├── config_client.py     # 외부 config API
│   │   └── harness_router_patch.py  # 워크플로우 라우터 패치
│   │
│   └── api/                     # FastAPI 라우터 (xgen-workflow include용)
│       └── router.py            # harness_router — SSE/WS/오케스트레이션 엔드포인트
│
└── web/                         # 독립 Web UI (캔버스 대체)
    ├── docker-compose.yml
    ├── backend/                 # FastAPI (세션 관리 + WebSocket)
    │   └── main.py
    └── frontend/                # React + Zustand + TailwindCSS
        └── src/
            ├── App.tsx
            ├── components/      # pipeline/, execution/, layout/
            ├── stores/          # executionStore, pipelineStore, uiStore
            ├── hooks/           # useWebSocket
            └── types/           # pipeline.ts
```

## 빠른 시작

### 최소 실행

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

### Builder 패턴

```python
from xgen_harness import PipelineBuilder

pipeline = (PipelineBuilder()
    .with_provider("anthropic", "claude-sonnet-4-20250514", "sk-...")
    .with_system("당신은 도움이 되는 어시스턴트입니다.")
    .disable("s05_plan")
    .disable("s09_validate")
    .build())

state = pipeline.build_state("오늘 날씨 알려줘")
await pipeline.run(state)
```

### 멀티턴 대화

```python
from xgen_harness import HarnessSession, HarnessConfig

session = HarnessSession(config=HarnessConfig(provider="anthropic"))
r1 = await session.run("안녕하세요")
r2 = await session.run("아까 질문에 이어서...")
```

## 의존성

```
httpx >= 0.27
```

Pure Python. LLM SDK/LangChain 의존 없음. FastAPI는 API 라우터 사용 시에만 필요 (선택적).
