# xgen-harness-executor

Rust 상태 머신 기반 에이전트 실행기.

기존 xgen-workflow의 Python/LangGraph DAG 실행기를 대체한다.
LLM 호출, MCP 도구 실행, 컨텍스트 관리, 품질 검증, 멀티에이전트 오케스트레이션을 12단계 파이프라인으로 처리한다.

---

## 왜 만들었나

기존 실행기(`AsyncWorkflowExecutor`)의 한계:

```python
# 기존: for 루프 — 한 번 돌고 끝
for node_id in execution_order:
    result = await process_node(node_id)
```

- LangGraph 종속 — 실행 구조를 바꿀 수 없음
- `for` 루프 — 검증 실패 시 재시도(사이클) 불가
- 에이전트가 틀린 답을 해도 넘어감, 도구를 잘못 써도 재시도 없음
- context window 터져도 모름, 비용 추적 안 됨

```rust
// 신규: while 루프 — 상태 머신, 재시도 점프 가능
while pointer < stages.len() {
    let result = execute_stage(stages[pointer]);
    match decide(result) {
        JumpTo(Plan) => pointer = plan_index,  // 재시도!
        Next         => pointer += 1,
    }
}
```

---

## 전체 아키텍처

```
xgen-workflow (Python)                    xgen-harness-executor (Rust)
──────────────────────                    ─────────────────────────────
execution_core.py
  should_route_to_harness()
  → harness_router.py
    → subprocess: xgen-harness-stdio      ┌─────────────────────────────┐
                                          │  stdin: JSON-RPC request     │
    stdin ──────────────────────────────→ │                              │
                                          │  HarnessBuilder              │
                                          │    → AgentStateMachine       │
                                          │      → 12단계 파이프라인     │
                                          │        → LLM SSE 스트리밍   │
                                          │        → MCP 도구 실행      │
                                          │        → 품질 검증/재시도   │
    stdout (이벤트, 라인별) ←──────────── │                              │
    stdout (최종 결과)      ←──────────── │  stdout: JSON-RPC response   │
                                          └─────────────────────────────┘
    → SSE 이벤트 변환
    → 프론트엔드로 스트리밍
```

**핵심 원칙:**
- HTTP 서버가 아닌 **subprocess + stdio** 방식 — 라이브러리로 동작
- 에이전트마다 독립 `AgentStateMachine` 인스턴스
- 모든 도구는 MCP 프로토콜 (기존 `@tool` 코드 변경 0)
- 파이프라인 단계를 체크리스트로 넣다 뺐다

---

## 12단계 파이프라인

```
Phase 1 (초기화)         Phase 2 (계획)          Phase 3 (실행)
┌───────┐ ┌────────┐   ┌──────┐ ┌──────────┐   ┌─────────┐ ┌─────┐
│ Input │→│ Memory │→  │ Plan │→│Tool Index│→  │ Context │→│ LLM │◄─┐
└───────┘ └────────┘   └──────┘ └──────────┘   └─────────┘ └──┬──┘  │
  ↓                                                            │     │
┌───────────┐                                            ┌─────▼───┐ │
│  System   │                                            │ Execute │─┘ 도구 루프
│  Prompt   │                                            │  (MCP)  │   (최대 20회)
└───────────┘                                            └────┬────┘
                                                              │
Phase 4 (검증)                    Phase 5 (마무리)            │
┌──────────┐ ┌────────┐         ┌──────┐ ┌──────────┐        │
│ Validate │→│ Decide │────────→│ Save │→│ Complete │        │
│ (평가LLM)│ └───┬────┘         └──────┘ └──────────┘        │
└──────────┘     │                                            │
                 └── score < threshold → Plan (재시도, 최대3회)┘
```

### 각 단계별 역할

| # | 단계 | 모듈 | 역할 | 필수 |
|---|------|------|------|------|
| 1 | **Input** | `bootstrap.rs` | API 키 확인, provider/model 유효성 검증. 없으면 즉시 에러 (불필요한 LLM 비용 방지) | O |
| 2 | **Memory** | `memory_read.rs` | `previous_results`에서 키워드 매칭으로 관련 이전 결과 프리페치 → 컨텍스트 주입 | |
| 3 | **System Prompt** | `context_build.rs` | 5개 섹션 자동 조립: 역할 + 도구지침 + 톤/형식 + 출력효율 + 환경정보 | O |
| 4 | **Plan** | `plan.rs` | LLM에게 목표/검색전략/완료기준 수립 (스프린트 계약). Validate의 채점 기준이 됨 | |
| 5 | **Tool Index** | `tool_discovery.rs` | MCP `tools/list` JSON-RPC로 사용 가능한 도구 탐색 → system prompt에 주입 | |
| 6 | **Context** | `context_compact.rs` | 토큰 버짓 체크 + 3단계 자동 압축 (History Snip → RAG 축소 → LLM 요약) | |
| 7 | **LLM** | `llm_call.rs` | LLM API SSE 스트리밍 호출. 지수 백오프(529→1s/2s/4s), 모델 폴백, max_tokens 에스컬레이션 | O |
| 8 | **Execute** | `tool_execute.rs` | MCP 도구 실행 루프. Read=병렬(`tokio::spawn + join_all`), Write=직렬. 50K char 초과 → 프리뷰 | |
| 9 | **Validate** | `validate.rs` | 독립 평가 LLM이 관련성/완전성/정확성 채점 (0~1). 실행 LLM과 분리하여 편향 방지 | |
| 10 | **Decide** | `decide.rs` | 점수 < threshold(기본 0.7) → Plan으로 점프하여 재시도 (최대 3회) | |
| 11 | **Save** | `memory_write.rs` | `harness_execution_log` + `harness_trace_span` DB 저장. 실패해도 Complete은 실행 | |
| 12 | **Complete** | (내장) | 최종 출력 반환, 메트릭 수집 (duration_ms, total_tokens, cost_usd) | O |

### 자동 바이패스 (`classify.rs`)

`run()` 진입 직후, 입력 복잡도를 규칙 기반(0ms, LLM 호출 없음)으로 판별하여 다운그레이드:

| 입력 예시 | 분류 | 결과 |
|-----------|------|------|
| "hi", "안녕" | Simple | full → minimal (4단계) |
| "CSV 분석해줘" | Moderate | full → standard (7단계) |
| "RAG 검색 후 비교 보고서" | Complex | 유지 (12단계) |

### 프리셋

| 프리셋 | 단계 수 | 포함 단계 |
|--------|---------|-----------|
| `minimal` | 4 | Input → System Prompt → LLM → Complete |
| `standard` | 7 | + Memory, Plan, Tool Index, Execute |
| `anthropic` | 11 | + Context, Validate, Decide |
| `full` | 12 | + Save (전체) |

---

## 사용 모드

### 1. stdio CLI (기본, 권장)

xgen-workflow에서 subprocess로 호출. Python asyncio와 완벽 호환.

```bash
cargo build --release --bin xgen-harness-stdio
```

```
Python (asyncio)                      Rust (tokio)
────────────────                      ────────────
proc = create_subprocess_exec(
  "xgen-harness-stdio",
  stdin=PIPE, stdout=PIPE)

stdin.write(JSON-RPC) ──────→         stdin 한 줄 읽기
stdin.close()                         → HarnessBuilder
                                      → AgentStateMachine.run()
                                      → 12단계 파이프라인 실행

  ←── stdout (라인별)     {"method":"harness/event",...}  실시간 이벤트
  ←── stdout (라인별)     {"method":"harness/event",...}
  ←── stdout (마지막)     {"id":1,"result":{"text":"..."}}  최종 결과

async for line in proc.stdout:        프로세스 종료
    event = json.loads(line)
    yield sse_event(event)
```

### 2. Rust 라이브러리

```toml
[dependencies]
xgen-harness-executor = { path = ".", default-features = false, features = ["core"] }
```

```rust
use xgen_harness_executor::prelude::*;

let output = HarnessBuilder::new()
    .provider("anthropic", "claude-sonnet-4-6")
    .api_key("sk-...")
    .text("피보나치 함수를 작성해줘")
    .stages(["input", "system_prompt", "plan", "llm", "execute", "complete"])
    .workflow_id("wf-123")
    .user_id("42")
    .run()
    .await?;
```

이벤트 스트리밍:

```rust
let output = HarnessBuilder::new()
    .provider("openai", "gpt-4o")
    .text("분석해줘")
    .run_with_events(|event| {
        println!("[{}] {:?}", event.event, event.data);
    })
    .await?;
```

### 3. HTTP 서버 (Docker 독립 서비스)

```bash
cargo build --release --features server --bin xgen-harness-executor
```

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/health` | 헬스체크 |
| POST | `/api/harness/execute/simple` | 단일 에이전트 |
| POST | `/api/harness/execute/legacy` | React Flow JSON 자동 변환 후 실행 |

### 4. PyO3 Python 모듈

```bash
maturin develop --features python
```

```python
import xgen_harness
result = xgen_harness.run(text="분석해줘", provider="anthropic")
```

---

## Feature Flags

| Feature | 기본 | 설명 | 추가 의존성 |
|---------|------|------|-------------|
| `core` | O | 상태 머신, LLM, MCP, Builder, 컨텍스트 관리 | - |
| `stdio` | O | stdin/stdout JSON-RPC CLI 바이너리 | - |
| `server` | - | HTTP 서버 (Axum, JWT, DB 마이그레이션) | axum, tower-http, jsonwebtoken |
| `python` | - | PyO3 네이티브 모듈 (.so) | pyo3 |

```bash
# 기본 (stdio CLI) — xgen-workflow에서 사용
cargo build --release

# Rust 라이브러리만 (바이너리 없이)
cargo build --release --no-default-features --features core

# HTTP 서버 포함
cargo build --release --features server

# PyO3 Python 모듈
maturin build --features python
```

---

## stdio JSON-RPC 프로토콜

### 요청 (stdin, 한 줄)

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "harness/run",
  "params": {
    "text": "CSV 데이터 분석해줘",
    "provider": "anthropic",
    "model": "claude-sonnet-4-6",
    "api_key": "sk-...",

    "system_prompt": "너는 데이터 분석가야",
    "harness_pipeline": "standard",
    "stages": ["input", "system_prompt", "plan", "llm", "execute", "complete"],
    "tools": ["mcp://bridge/nodes", "mcp://session/abc-123"],
    "modules": ["error_recovery", "context_manager"],

    "temperature": 0.7,
    "max_tokens": 8192,
    "max_retries": 3,
    "eval_threshold": 0.7,

    "workflow_data": { "nodes": [...], "edges": [...] },
    "workflow_id": "wf-abc",
    "workflow_name": "데이터 분석",
    "interaction_id": "int-xyz",
    "user_id": "42",
    "attached_files": [
      { "name": "data.csv", "content": "col1,col2\n...", "file_type": "text/csv", "is_image": false }
    ],
    "previous_results": ["이전 분석 결과 텍스트..."]
  }
}
```

`text` 외 모든 필드는 선택사항. `stages`가 없으면 `harness_pipeline` 프리셋으로 결정, 둘 다 없으면 minimal.

### 이벤트 알림 (stdout, 라인별)

```json
{"jsonrpc":"2.0","method":"harness/event","params":{"event":"stage_enter","data":{"stage_id":"plan","stage":"Plan","stage_ko":"실행 계획","phase":"plan","step":4,"total":7}}}
{"jsonrpc":"2.0","method":"harness/event","params":{"event":"message","data":{"type":"text","text":"분석 결과를..."}}}
{"jsonrpc":"2.0","method":"harness/event","params":{"event":"tool_call","data":{"name":"search_docs","input":{"query":"매출"}}}}
{"jsonrpc":"2.0","method":"harness/event","params":{"event":"tool_result","data":{"name":"search_docs","result":"..."}}}
{"jsonrpc":"2.0","method":"harness/event","params":{"event":"evaluation","data":{"score":0.85}}}
{"jsonrpc":"2.0","method":"harness/event","params":{"event":"stage_exit","data":{"stage_id":"plan","stage":"Plan","score":null}}}
```

### 최종 응답 (stdout, 마지막 줄)

```json
{"jsonrpc":"2.0","id":1,"result":{"text":"분석 결과입니다..."}}
```

에러:
```json
{"jsonrpc":"2.0","id":1,"error":{"code":-32000,"message":"API key invalid"}}
```

### 이벤트 타입

| event | 설명 |
|-------|------|
| `stage_enter` | 단계 시작 (stage_id, stage_ko, phase, step/total) |
| `stage_exit` | 단계 완료 (score 포함) |
| `message` | LLM 텍스트 스트리밍 청크 |
| `tool_call` | MCP 도구 호출 시작 |
| `tool_result` | 도구 실행 결과 |
| `evaluation` | Validate 채점 결과 (score 0~1) |
| `decision` | 재시도/통과 결정 |
| `plan_contract` | 스프린트 계약 생성 |
| `metrics` | 실행 완료 메트릭 (duration_ms, total_tokens, cost_usd) |
| `memory_write` | DB 저장 완료 |
| `debug_log` | 내부 디버그 (바이패스, MCP 연결 등) |
| `error` | 에러 발생 |

---

## MCP 도구 연결

### 도구 URI 형식

| URI | 동작 |
|-----|------|
| `mcp://session/SESSION_ID` | xgen-mcp-station HTTP 트랜스포트 |
| `mcp://bridge/nodes` | Node MCP Bridge — Python 노드를 MCP 도구로 자동 래핑 (subprocess, stdio) |
| `mcp://bridge/nodes?categories=rag,api` | 카테고리 필터링 |
| `mcp://bridge/services` | Service Tools Bridge — xgen-documents API를 MCP 도구로 래핑 |
| `SESSION_ID` (plain) | xgen-mcp-station HTTP 트랜스포트 |

### 도구 실행 규칙

- **Read 도구** (search, read, list): 병렬 실행 (`tokio::spawn + join_all`)
- **Write 도구** (execute, create): 직렬 실행
- 결과 50K char 초과 → 디스크 저장 + 2KB 프리뷰 (앞 800자 + 뒤 500자)

---

## 멀티에이전트 오케스트레이션

| 패턴 | 설명 | 사용 예 |
|------|------|---------|
| **Sequential** | A → B → C 순차. 앞 출력을 다음 입력으로 전달 | 기본 |
| **Pipeline** | 순차 + 평가. 점수 미달 시 피드백 재시도 (최대 3회) | Evaluator 노드 포함 시 |
| **Supervisor** | Lead가 `[DELEGATE:워커이름]` 태그로 Worker에게 위임 | Router 노드 포함 시 |
| **Parallel** | 동시 실행 후 결과 집계 | 명시적 지정 |

### 자동 추론 (`converter.rs`)

| 조건 | 추론 패턴 |
|------|----------|
| 에이전트 1개 | Sequential |
| Router 노드 존재 | Supervisor |
| Evaluator 노드 또는 anthropic/full 프리셋 | Pipeline |
| 그 외 | Sequential |

---

## 에러 복구 (`recover.rs`)

7가지 에러 패턴을 감지하여 5가지 복구 액션 중 하나를 실행:

| 에러 | 복구 |
|------|------|
| 413 context_length_exceeded | **Compact** — 히스토리 압축 (최근 4개 유지) |
| max_tokens 초과 | **Escalate** — 8K → 64K로 에스컬레이션 |
| 429 rate limit | **Fallback** — claude-sonnet → claude-haiku 모델 폴백 |
| 529 overloaded | **Retry** — 지수 백오프 (1s/2s/4s) |
| 3회 연속 실패 | **GiveUp** — 에러 전파 |

---

## 디렉토리 구조

```
xgen-harness-executor/
├── Cargo.toml                      # Feature flags: core, stdio, server, python
├── Cargo.lock
├── Dockerfile
├── pyproject.toml                  # maturin (PyO3 빌드 설정)
│
├── bridge/                         # Python MCP 브릿지 (subprocess로 실행됨)
│   ├── server.py                   #   Node MCP Bridge — @tool 자동 스캔 + MCP 래핑 (333줄)
│   └── service_tools.py            #   Service Tools Bridge — xgen-documents API 래핑 (258줄)
│
├── src/
│   ├── lib.rs                      # 라이브러리 루트 (feature-gated 모듈 등록, prelude)
│   ├── main.rs                     # HTTP 서버 진입점 (feature = "server")
│   ├── stdio_main.rs               # stdio CLI 진입점 (feature = "stdio") — 205줄
│   ├── stdio.rs                    # JSON-RPC 2.0 프로토콜 타입 + HarnessRunParams
│   ├── events.rs                   # SseEvent (전 모드 공용 이벤트 구조체)
│   ├── builder.rs                  # HarnessBuilder — fluent API (296줄)
│   ├── python.rs                   # PyO3 바인딩 (feature = "python")
│   │
│   ├── state_machine/              # 상태 머신 코어
│   │   ├── agent_executor.rs       #   AgentStateMachine — while 루프 기반 실행기 (779줄)
│   │   │                           #     run(), execute_stage(), decide_transition(),
│   │   │                           #     apply_recovery(), init_mcp_clients()
│   │   ├── orchestrator.rs         #   멀티에이전트 (Sequential/Pipeline/Supervisor/Parallel)
│   │   └── stage.rs                #   HarnessStage enum (12+3), 프리셋, from_str(), display_name()
│   │
│   ├── stages/                     # 12단계 개별 모듈
│   │   ├── bootstrap.rs            #   [1] Input — API 키/provider 유효성
│   │   ├── memory_read.rs          #   [2] Memory — previous_results 키워드 매칭
│   │   ├── context_build.rs        #   [3] System Prompt — 5개 섹션 조립
│   │   ├── plan.rs                 #   [4] Plan — 스프린트 계약 (LLM 호출)
│   │   ├── tool_discovery.rs       #   [5] Tool Index — MCP tools/list
│   │   ├── context_compact.rs      #   [6] Context — 토큰 버짓 + 3단계 압축
│   │   ├── llm_call.rs             #   [7] LLM — SSE 스트리밍 + 에러 복구 (293줄)
│   │   ├── tool_execute.rs         #   [8] Execute — MCP 도구 루프
│   │   ├── validate.rs             #   [9] Validate — 독립 평가 LLM (285줄)
│   │   ├── decide.rs               #   [10] Decide — 재시도 결정
│   │   ├── memory_write.rs         #   [11] Save — DB 로그 저장 (220줄)
│   │   ├── classify.rs             #   자동 바이패스 (규칙 기반, 0ms)
│   │   ├── recover.rs              #   에러 복구 (7패턴→5액션, 343줄)
│   │   ├── init.rs                 #   레거시 compat (Bootstrap+Memory+ContextBuild 통합)
│   │   └── execute.rs              #   레거시 compat (LLMCall+ToolExecute 통합, 380줄)
│   │
│   ├── context/                    # 컨텍스트 윈도우 관리
│   │   ├── window.rs               #   ContextWindowManager — 3단계 압축, 회로차단기 (326줄)
│   │   ├── sections.rs             #   PromptSectionManager — 우선순위 섹션 빌더 (288줄)
│   │   └── memory.rs               #   MemoryPrefetcher — 키워드 매칭, 중복 방지
│   │
│   ├── llm/                        # LLM Provider 직접 HTTP 호출
│   │   ├── provider.rs             #   LlmProvider trait + create_provider()
│   │   ├── anthropic.rs            #   Anthropic Messages API (308줄)
│   │   ├── openai.rs               #   OpenAI Chat Completions API (361줄)
│   │   └── streaming.rs            #   SSE 스트림 파서 (data: 라인 → 청크)
│   │
│   ├── mcp/                        # MCP 클라이언트
│   │   ├── client.rs               #   McpClientManager — stdio + HTTP 트랜스포트 (494줄)
│   │   │                           #     initialize handshake, tools/list, tools/call
│   │   └── protocol.rs             #   JSON-RPC 2.0 요청/응답 타입
│   │
│   ├── tools/                      # 도구 관리
│   │   ├── registry.rs             #   ToolRegistry — 역할별 접근 제어, Progressive Disclosure
│   │   └── orchestration.rs        #   ToolOrchestrator — Read=병렬, Write=직렬 (268줄)
│   │
│   ├── workflow/                   # 워크플로우 통합
│   │   ├── definition.rs           #   HarnessWorkflow (harness-v1 JSON 스키마, 250줄)
│   │   ├── converter.rs            #   React Flow → harness-v1 변환기 (495줄)
│   │   └── db.rs                   #   PostgreSQL 실행 로그/TraceSpan (224줄)
│   │
│   └── api/                        # HTTP 서버 (feature = "server")
│       ├── http.rs                 #   Axum 핸들러 (/health, /execute/simple, /execute/legacy, 753줄)
│       ├── sse.rs                  #   SSE 스트리밍 헬퍼
│       └── mod.rs
│
└── tests/
    └── integration_test.rs         # 24개 테스트 (612줄)
```

### 코드 통계

| 카테고리 | 파일 수 | 줄 수 |
|---------|---------|-------|
| Rust 소스 | 50 | ~9,770 |
| Python Bridge | 2 | 591 |
| 테스트 | 1 | 612 |
| **합계** | **53** | **~10,973** |

---

## DB 스키마

서버 시작 시 자동 마이그레이션 (`main.rs`에서 CREATE TABLE IF NOT EXISTS).

```sql
-- 실행 로그
CREATE TABLE IF NOT EXISTS harness_execution_log (
    id              BIGSERIAL PRIMARY KEY,
    workflow_id     VARCHAR(255) NOT NULL,
    interaction_id  VARCHAR(255) NOT NULL,
    user_id         BIGINT NOT NULL,
    agent_id        VARCHAR(255) NOT NULL,
    agent_name      VARCHAR(255) NOT NULL,
    stage           VARCHAR(50) NOT NULL,
    input_data      JSONB DEFAULT '{}',
    output_data     JSONB DEFAULT '{}',
    status          VARCHAR(20) NOT NULL DEFAULT 'started',
    duration_ms     BIGINT,
    token_usage     JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 실행 추적 (단계별 상세)
CREATE TABLE IF NOT EXISTS harness_trace_span (
    id                BIGSERIAL PRIMARY KEY,
    execution_log_id  BIGINT NOT NULL REFERENCES harness_execution_log(id),
    span_type         VARCHAR(50) NOT NULL,
    name              VARCHAR(255) NOT NULL,
    input             JSONB,
    output            JSONB,
    duration_ms       BIGINT NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

---

## 환경 변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `ANTHROPIC_API_KEY` | - | Anthropic API 키 |
| `OPENAI_API_KEY` | - | OpenAI API 키 |
| `DATABASE_URL` | - | PostgreSQL (실행 로그 저장) |
| `REDIS_URL` | - | Redis (세션 상태) |
| `MCP_STATION_URL` | `http://xgen-mcp-station:8000` | MCP 스테이션 |
| `NODE_BRIDGE_NODES_DIR` | `/app/workflow/editor/nodes` | Python 노드 디렉토리 |
| `PORT` | `8000` | HTTP 서버 포트 (server feature) |
| `RUST_LOG` | `info` | 로그 레벨 |

---

## 빌드

```bash
# 개발
cargo build

# 릴리스 (최적화)
cargo build --release

# 테스트
cargo test --features core
```

릴리스 프로파일:

```toml
[profile.release]
codegen-units = 1   # 단일 코드 유닛 (최적화 극대화)
lto = true          # 링크 타임 최적화
opt-level = "z"     # 바이너리 크기 최소화 (~5.4MB)
```

---

## License

MIT
