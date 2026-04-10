# xgen-harness-executor

**Rust 기반 에이전트 하네스 실행기**

기존 Python/LangGraph DAG 실행기를 대체하는 **에이전트 전용 상태 머신 실행기**.
에이전트마다 독립적인 하네스(harness)가 돌아가며, 실행 단계를 체크리스트처럼 넣다 뺐다 할 수 있다.

---

## 왜 만들었나

기존 xgen-workflow는 Python + LangGraph 기반 DAG 실행기다.

**문제:**
- 에이전트 실행 루프가 LangGraph에 종속 — 구조를 바꿀 수 없음
- `for` 루프 순차 실행 — Evaluator→Generator 재시도 같은 **사이클 불가**
- 100+ 노드의 제네릭 캔버스 — 에이전트 하네스 관점이 아님
- 파이프라인 단계(계획→실행→검증→재시도)를 **bool 토글로 켜고 끌 수밖에 없음**
- 도구는 LangChain `@tool` 직접 호출 — 프로토콜 표준 없음

**비전:**
> 에이전트마다 하네스 실행기 하나하나 돌아가게 하고,
> 실행 로직은 API 호출 하나니까 Rust로.
> MCP native하게 붙여서, 하네스 페이지 가서 뭐 쓸거야 체크체크.
> 파이프라인 단계를 넣다 뺐다도 가능하게.

---

## 하네스 엔지니어링이란

**Harness Engineering**은 LLM 에이전트에게 **구조화된 실행 프레임워크**를 씌우는 것이다.
LLM은 똑똑하지만, 혼자 돌리면 환각, 무한 루프, 컨텍스트 폭발, 도구 남용 같은 문제가 생긴다.
하네스는 이런 에이전트를 **실행 인프라**로 감싸서 품질을 보장한다.

### OpenClaude에서 가져온 것 (실행 인프라)

유출된 OpenClaude(Claude Code 오픈소스 재구현)의 내부 파이프라인을 분석하여 **실행 인프라** 부분을 포팅했다. Claude Code는 **고정 파이프라인**이며, 단계를 넣다 뺐다 하는 구조가 아니다.

```
OpenClaude 내부 파이프라인 (실제 구조):

1. Memory Read (CLAUDE.md, 메모리 파일)
2. Tool Budget 계산
3. Context Window 체크 → autocompact 트리거
4. Prompt Section 조립 (우선순위 기반)
5. System Prompt 병합
6. Message 조립
7. API Call (스트리밍)
8. Tool Execution (있으면)
9. Error Recovery (413, rate limit 등)
10. → 7번으로 돌아감 (도구 루프)
11. 응답 렌더링
```

**이 파이프라인에는 Plan/Validate/Decide 같은 "독립 평가 게이트"가 없다.**
Claude Code는 단일 에이전트가 도구 루프를 돌리는 구조이고, 자기평가나 재시도 판단은 하지 않는다.

#### OpenClaude → xgen-harness 포팅 대응표

| OpenClaude 원본 | 포팅된 모듈 | 상수/로직 동일 여부 |
|----------------|-----------|-----------------|
| autoCompact.ts (컨텍스트 압축) | `context/window.rs` | ✅ AUTOCOMPACT_BUFFER_TOKENS=13,000, MAX_CONSECUTIVE_FAILURES=3 동일 |
| promptSections (우선순위 프롬프트) | `context/sections.rs` | ✅ 우선순위 기반 섹션 조립, 예산 초과 시 자동 제거 |
| errorRecovery (413/rate limit 복구) | `stages/recover.rs` | ✅ 7가지 복구 경로 → 5액션 (Compact/Escalate/Fallback/Retry/GiveUp) |
| toolOrchestration (도구 실행 제어) | `tools/orchestration.rs` | ✅ TOOL_RESULT_BUDGET=50K, MICROCOMPACT HEAD=800/TAIL=500 동일 |
| memoryRead (CLAUDE.md 로드) | `context/memory.rs` | ⚠️ 키워드 매칭 방식으로 변형 (원본은 파일 직접 로드) |
| LLM 직접 HTTP 호출 | `llm/anthropic.rs`, `llm/openai.rs` | ✅ LangGraph 없이 reqwest → SSE 스트리밍 |

### 자체 설계한 것 (하네스 품질 보장)

Claude Code에 없는 것을 **에이전트 품질 관리** 관점에서 설계했다:

| 기능 | 설명 | Claude Code에 있나? |
|------|------|------------------|
| **Plan 스프린트 계약** | 실행 전 목표/전략/완료 기준 선언 → Validate에서 검증 | ❌ 없음 |
| **Validate 독립 평가** | 별도 LLM 호출로 응답 품질 채점 (Contract Compliance 포함) | ❌ 없음 |
| **Decide 재시도 루프** | 점수 미달 시 Plan으로 점프 → 재실행 | ❌ 없음 |
| **단계 체크리스트** | Init/Plan/Execute/Validate/Decide/Complete 넣다 뺐다 | ❌ 고정 파이프라인 |
| **멀티에이전트 오케스트레이션** | Sequential/Parallel/Supervisor/Pipeline 4패턴 | ❌ 단일 에이전트 |
| **Service Tools Bridge** | 기존 서비스 API(문서검색, DB)를 MCP 도구로 래핑 | ❌ 없음 |
| **레거시 워크플로우 변환** | React Flow JSON → harness-v1 자동 변환 | ❌ 없음 |
| **HarnessBuilder 라이브러리** | HTTP 서버 없이 다른 Rust 서비스에 임베드 | ❌ 없음 |

### 하네스가 해결하는 것

| 문제 | 해법 | 출처 | 구현 |
|------|------|------|------|
| **컨텍스트 폭발** | 3단계 자동 압축 | OpenClaude | `context/window.rs` |
| **에러 무한 루프** | 5액션 복구 + 회로차단기 | OpenClaude | `stages/recover.rs` |
| **프롬프트 비대** | 우선순위 섹션 관리 | OpenClaude | `context/sections.rs` |
| **도구 남용** | Read 병렬/Write 직렬 + 결과 제한 | OpenClaude | `tools/orchestration.rs` |
| **LLM 환각/부정확** | Validate 독립 평가 → 재시도 | **자체 설계** | `stages/validate.rs` |
| **계획 없는 실행** | Plan 스프린트 계약 → 계약 이행 검증 | **자체 설계** | `stages/plan.rs` |
| **메모리 부재** | 이전 실행 결과 키워드 매칭 주입 | OpenClaude 변형 | `context/memory.rs` |

---

## 핵심 개념

### 1. 에이전트별 독립 상태 머신

```
기존:  for node_id in execution_order:  ← 한 번 실행하고 끝
          result = execute(node_id)

신규:  while pointer < stages.len():   ← 상태 머신 루프
          stage = stages[pointer]
          result = execute_stage(stage)
          if decide(result) == JumpTo(Plan):
              pointer = plan_index       ← 재시도 점프!
          else:
              pointer += 1
```

10개 에이전트가 있으면 10개의 **독립적인 상태 머신**이 돈다.

### 2. 12단계 파이프라인 (체크리스트)

```
Phase 1 (초기화)     Phase 2 (계획)      Phase 3 (실행)           Phase 4 (검증)      Phase 5 (마무리)
Input → Memory →    Plan →              Context →               Validate →          Save →
System Prompt       Tool Index          LLM ↔ Execute (루프)    Decide              Complete
                                              │                    │
                                              └── retry (score < threshold) ──┘
```

| 단계 | 사용자 ID | 역할 | 필수 |
|------|----------|------|------|
| **Input** | `input` | API 키 확인, 설정 초기화 | ✅ |
| **Memory** | `memory` | 이전 실행 결과에서 관련 내용 불러오기 | 선택 |
| **System Prompt** | `system_prompt` | 에이전트 역할/지시사항 조립 | ✅ |
| **Plan** | `plan` | 목표, 검색 전략, 완료 기준 수립 | 선택 |
| **Tool Index** | `tool_index` | MCP 도구 목록 탐색 후 주입 | 선택 |
| **Context** | `context` | 토큰 한도 초과 시 자동 압축 | 선택 |
| **LLM** | `llm` | LLM API 스트리밍 호출 | ✅ |
| **Execute** | `execute` | MCP 도구 실행 후 결과 반환 (LLM과 루프) | 선택 |
| **Validate** | `validate` | 독립 평가 LLM으로 품질 채점 | 선택 |
| **Decide** | `decide` | 점수 미달 시 Plan으로 재시도 | 선택 |
| **Save** | `save` | 실행 결과를 DB에 저장 | 선택 |
| **Complete** | `complete` | 최종 출력 반환 | ✅ |

**프리셋:**
- `minimal` → Input, System Prompt, LLM, Complete (4단계, 단순 대화)
- `standard` → + Plan, Tool Index, Execute (7단계, 계획 + 도구)
- `full` → 전체 12단계 (검증/재시도/저장 포함)

### 3. MCP Native 도구

모든 도구는 MCP 프로토콜(JSON-RPC 2.0)로 통신. 도구의 언어/위치에 무관.

```
Rust 실행기 → MCP JSON-RPC → 도구 서버 (Python, Node, 외부 등)
```

| 도구 연결 방식 | 예시 |
|--------------|------|
| Node MCP Bridge (Python subprocess) | 기존 xgen-workflow 노드 30+개 |
| xgen-mcp-station (HTTP) | 샌드박스, 외부 MCP 서버 |
| LangChain MCP Bridge | @tool 함수 래핑 |
| 외부 MCP 서버 (stdio) | Brave Search, GitHub 등 |

### 4. Node MCP Bridge

**기존 Python 노드를 코드 변경 없이 MCP 도구로 노출.**

```
bridge/server.py
  → Node.execute() 자동 스캔
  → Parameter → JSON Schema 변환
  → MCP tools/call → Node 인스턴스 생성 → execute() 호출 → 결과 반환
```

Rust 실행기가 subprocess로 spawn → McpClient stdio 연결.
에이전트 tools에 `"mcp://bridge/nodes"` 추가하면 자동 연결.

### 5. 멀티에이전트 오케스트레이션

| 패턴 | 동작 |
|------|------|
| **Sequential** | A → B → C |
| **Parallel** | A + B + C → merge |
| **Supervisor** | Lead가 태스크 분배 → Worker 병렬 → Lead 종합 |
| **Pipeline** | A → Eval → (점수 미달 시 피드백 재시도) |

---

## 아키텍처

```
┌─────────────────────────────────────────────────┐
│  xgen-frontend (Next.js)                         │
│  /canvas (기존 워크플로우) + /harness/* (전용 UI) │
└────────────────────┬────────────────────────────┘
                     │ SSE
┌────────────────────▼────────────────────────────┐
│  xgen-backend-gateway (Rust/Axum :8000)          │
│  /api/harness/* → xgen-harness-executor          │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│  xgen-harness-executor (Rust :8006)              │
│                                                  │
│  ┌─────────────────────────────────────────┐    │
│  │ Orchestrator (멀티에이전트)               │    │
│  └──┬──────────┬──────────┬────────────────┘    │
│  ┌──▼────┐  ┌──▼────┐  ┌──▼────┐               │
│  │Agent A│  │Agent B│  │Agent C│               │
│  │상태머신│  │상태머신│  │상태머신│               │
│  └──┬────┘  └──┬────┘  └──┬────┘               │
│  ┌──▼──────────▼──────────▼────┐               │
│  │ MCP Client Layer             │               │
│  │ - Node Bridge (subprocess)   │               │
│  │ - MCP Station (HTTP)         │               │
│  │ - 외부 MCP (stdio)           │               │
│  └─────────────────────────────┘               │
└─────────────────────────────────────────────────┘
```

---

## 주요 기능

### 에러 복구 (실배선)

5가지 자동 복구 액션:

| 액션 | 동작 | 트리거 |
|------|------|--------|
| **Compact** | 최근 4개 메시지만 유지 | 413 (prompt too long) |
| **Escalate** | max_tokens 8K → 64K | max_output_tokens 에러 |
| **Fallback** | claude-sonnet → claude-haiku | rate limit |
| **Retry** | 힌트 메시지 추가 후 재시도 | timeout |
| **GiveUp** | 3회 연속 실패 → 포기 | 회로차단기 |

### 컨텍스트 자동 관리 (3단계 압축)

매 LLM 호출 전 자동 실행:

1. **History Snip** — 오래된 대화 잘라내기 (최근 4개 유지)
2. **RAG 축소** — 인덱스만 남기기
3. **LLM 요약** — 대화를 3~5문장으로 요약 (provider.chat 호출)

회로차단기: 연속 3회 실패 시 압축 중단 (무한 루프 방지).

### Plan 스프린트 계약 강제화

Plan 단계에서 생성한 Completion Criteria를 Validate 단계에서 **실제 검증**.
Contract Compliance 가중치 0.2로 점수에 반영.

### 자동 바이패스 (classify.rs)

입력 텍스트의 복잡도를 LLM 호출 없이 규칙 기반으로 판별 (0ms):

| 등급 | 예시 | 적용 프리셋 | 바이패스 단계 |
|------|------|------------|-------------|
| **Simple** | "hi", "안녕", "ok" | minimal (4단계) | Plan, Validate, Decide 건너뜀 |
| **Moderate** | "이 CSV 분석해줘" | standard (7단계) | Validate, Decide 건너뜀 |
| **Complex** | "RAG 검색 후 비교 보고서" | full (12단계) | 전체 실행 |

`should_downgrade("full", "hi")` → `Some("minimal")` — "hi" 같은 단순 인사에 full 파이프라인은 낭비.

### 시스템 프롬프트 우선순위 관리

PromptSectionManager로 섹션 단위 조립:

```
1. role_definition (100)    — 역할 정의
2. task_instructions (90)   — 태스크 지시
3. tool_index (80)          — 도구 인덱스
4. sprint_contract (50)     — 스프린트 계약
5. memory (30)              — 이전 실행 컨텍스트
```

예산 초과 시 낮은 우선순위부터 자동 제거.

---

## 사용법

### 1. HTTP 서비스로 실행

```bash
# Docker
docker compose -f docker-compose.dev.yml --profile harness up -d --build xgen-harness-executor

# 직접 실행
cargo run --release
```

```bash
# 단일 에이전트
curl -X POST http://localhost:8006/api/harness/execute/simple \
  -H "Content-Type: application/json" \
  -d '{
    "text": "피보나치 함수를 작성해줘",
    "provider": "anthropic",
    "model": "claude-sonnet-4-6",
    "stages": ["init", "plan", "execute", "validate", "decide", "complete"]
  }'
```

### 2. Rust 라이브러리로 임베드

```toml
# Cargo.toml
[dependencies]
xgen-harness-executor = { git = "https://github.com/PlateerLab/xgen-harness-executor.git", default-features = false, features = ["core"] }
```

```rust
use xgen_harness_executor::prelude::HarnessBuilder;

let output = HarnessBuilder::new()
    .provider("anthropic", "claude-sonnet-4-6")
    .api_key("sk-ant-...")
    .text("피보나치 함수를 작성해줘")
    .stages(["init", "execute", "complete"])
    .run()
    .await?;

println!("{}", output);
```

이벤트 콜백:

```rust
let output = HarnessBuilder::new()
    .provider("openai", "gpt-4o-mini")
    .text("안녕하세요")
    .run_with_events(|event| {
        println!("{:?}", event);  // stage_enter, message, done 등
    })
    .await?;
```

### 3. Python 네이티브 모듈 (PyO3)

maturin으로 빌드하면 Python에서 직접 `import xgen_harness`로 사용 가능.

```bash
# 빌드
pip install maturin
maturin develop --features python

# 또는 wheel 빌드
maturin build --release --features python
pip install target/wheels/xgen_harness-*.whl
```

```python
import xgen_harness

# 동기 호출
result = xgen_harness.run(
    text="분석해줘",
    provider="anthropic",
    model="claude-sonnet-4-6",
    api_key="sk-...",
    stages=["input", "system_prompt", "plan", "llm", "execute", "complete"],
)
print(result)

# 이벤트 수집 호출
events, result = xgen_harness.run_with_events(
    text="분석해줘",
    provider="anthropic",
    model="claude-sonnet-4-6",
    api_key="sk-...",
)
for e in events:
    print(e["event"], e["data_json"])
```

### 4. 기존 워크플로우 자동 라우팅

기존 캔버스 워크플로우를 **프론트엔드 변경 없이** 하네스로 실행:

```
POST /api/workflow/execution/based-id/stream  ← 기존 엔드포인트 그대로
  → execution_core.py
    → agent 노드 감지 → POST /api/harness/execute/legacy
    → converter.rs (React Flow → harness-v1 자동 변환)
    → 비에이전트 노드 → Node MCP Bridge 자동 주입
    → SSE 이벤트 → 기존 포맷으로 변환 → 프론트엔드
```

환경변수 `HARNESS_FULL_ROUTING=true`로 모든 워크플로우 하네스 라우팅 강제.

---

## 파일 구조

```
xgen-harness-executor/
├── src/
│   ├── main.rs                    — Axum HTTP 서버
│   ├── lib.rs                     — 라이브러리 진입점 + prelude
│   ├── builder.rs                 — HarnessBuilder (임베드용 API)
│   │
│   ├── state_machine/
│   │   ├── stage.rs               — HarnessStage enum, 프리셋, 전이
│   │   ├── agent_executor.rs      — AgentStateMachine (while 상태 머신)
│   │   └── orchestrator.rs        — 4패턴 멀티에이전트
│   │
│   ├── stages/                        ── 12단계 개별 모듈 ──
│   │   ├── bootstrap.rs           — Input: API 키/설정 초기화
│   │   ├── memory_read.rs         — Memory: 이전 실행 컨텍스트 프리페치
│   │   ├── context_build.rs       — System Prompt: 프롬프트 섹션 조립
│   │   ├── plan.rs                — Plan: 스프린트 계약
│   │   ├── tool_discovery.rs      — Tool Index: MCP 도구 인덱스 주입
│   │   ├── context_compact.rs     — Context: 3단계 자동 압축
│   │   ├── llm_call.rs            — LLM: 순수 LLM API 스트리밍 호출
│   │   ├── tool_execute.rs        — Execute: MCP 도구 실행 (LLM과 루프)
│   │   ├── validate.rs            — Validate: 독립 평가 LLM 채점
│   │   ├── decide.rs              — Decide: 재시도/통과 결정
│   │   ├── memory_write.rs        — Save: 실행 결과 DB 저장
│   │   ├── recover.rs             — 에러 복구 5액션
│   │   ├── init.rs                — (레거시) Init 통합 단계
│   │   └── execute.rs             — (레거시) Execute 통합 단계
│   │
│   ├── llm/
│   │   ├── provider.rs            — LlmProvider trait
│   │   ├── anthropic.rs           — Anthropic Messages API SSE
│   │   └── openai.rs              — OpenAI Chat Completions API SSE
│   │
│   ├── mcp/
│   │   ├── protocol.rs            — JSON-RPC 2.0
│   │   └── client.rs              — McpClient (stdio + HTTP)
│   │
│   ├── context/
│   │   ├── window.rs              — 3단계 자동 압축 + 회로차단기
│   │   ├── sections.rs            — 우선순위 프롬프트 섹션
│   │   └── memory.rs              — 메모리 프리페치
│   │
│   ├── tools/
│   │   ├── registry.rs            — 도구 레지스트리
│   │   └── orchestration.rs       — Read 병렬, Write 직렬
│   │
│   ├── workflow/
│   │   ├── definition.rs          — harness-v1 JSON 스키마
│   │   ├── converter.rs           — 레거시 → harness-v1 변환
│   │   └── db.rs                  — PostgreSQL 실행 로그
│   │
│   └── api/
│       ├── sse.rs                 — SSE 이벤트 정의
│       └── http.rs                — REST 엔드포인트
│
├── bridge/
│   └── server.py                  — Node MCP Bridge (Python 노드 래핑)
│
├── tests/
│   └── integration_test.rs        — 24개 테스트
│
├── Cargo.toml                     — features: core / server
└── Dockerfile
```

---

## Feature Flags

```toml
[features]
default = ["core", "server"]
core = []              # 상태 머신 + LLM + MCP (임베드용, 가벼움)
server = ["core"]      # HTTP 서버 + JWT + DB (독립 서비스용)
python = ["core"]      # PyO3 바인딩 (maturin 빌드)
```

- **core만**: 다른 Rust 서비스에 임베드. HTTP 의존성 없음.
- **server**: 독립 서비스로 배포. axum + PostgreSQL + Redis.
- **python**: `maturin develop --features python`으로 빌드 → `import xgen_harness`.

---

## API 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| GET | `/health` | 헬스 체크 |
| POST | `/api/harness/execute/simple` | 단일 에이전트 (SSE) |
| POST | `/api/harness/execute` | 멀티에이전트 (harness-v1, SSE) |
| POST | `/api/harness/execute/legacy` | 기존 워크플로우 JSON → 자동 변환 → 실행 |

---

## 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `BIND_ADDR` | `0.0.0.0:8000` | 바인딩 주소 |
| `ANTHROPIC_API_KEY` | - | Anthropic API 키 (없으면 xgen-core에서 fetch) |
| `OPENAI_API_KEY` | - | OpenAI API 키 |
| `XGEN_CORE_URL` | `http://xgen-core:8000` | API 키 fetch용 |
| `MCP_STATION_URL` | `http://xgen-mcp-station:8000` | MCP Station |
| `NODE_BRIDGE_NODES_DIR` | `/app/workflow/editor/nodes` | Node Bridge 노드 디렉토리 |
| `NODE_BRIDGE_SCRIPT` | `bridge/server.py` | Node Bridge 스크립트 경로 |
| `DATABASE_URL` | - | PostgreSQL (실행 로그) |

---

## 기존 시스템 대비 변경

| 구분 | 기존 (xgen-workflow) | 신규 (xgen-harness-executor) |
|------|---------------------|-------------------------------|
| 언어 | Python | Rust |
| 실행 모델 | DAG for 루프 | while 상태 머신 |
| 에이전트 루프 | LangGraph | reqwest → LLM API 직접 |
| 도구 호출 | @tool 직접 | MCP JSON-RPC |
| 멀티에이전트 | 노드 순차 | Orchestrator 4패턴 |
| 파이프라인 설정 | bool 토글 | 단계 체크리스트 |
| 에러 복구 | try/except | 5액션 apply_recovery |
| 컨텍스트 관리 | 문자열 연결 | 3단계 자동 압축 |
| 시스템 프롬프트 | 단일 문자열 | 우선순위 섹션 관리 |
| 바이너리 | Python 런타임 | ~5.4MB 단일 바이너리 |

---

## 테스트

```bash
cargo test
# 24 passed, 0 failed
```

```
context::window      — 4 tests (budget, compaction, circuit breaker, history snip)
context::sections    — 3 tests (build, budget removal, inactive)
context::memory      — 2 tests (keyword matching, dedup)
stages::recover      — 4 tests (error detection, escalation, fallback, 413)
tools::orchestration — 2 tests (partition, microcompact)
orchestrator         — 3 tests (subtask parse, json extract, artifact injection)
workflow::definition — 2 tests (parse harness-v1, validation errors)
workflow::converter  — 4 tests (convert simple, infer pipeline, react flow normalization)
```

---

## 라이선스

Internal use only — XGEN 2.0 플랫폼 전용.
