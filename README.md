# xgen-harness-executor

Rust 상태 머신 기반 에이전트 실행기.  
LLM 호출, MCP 도구 실행, 컨텍스트 관리, 멀티에이전트 오케스트레이션을 하나의 파이프라인으로 처리합니다.

---

## 전체 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                      xgen-harness-executor                      │
│                                                                 │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐    │
│  │  Input    │──→│  Memory  │──→│  System  │──→│   Plan   │    │
│  │          │   │   Read   │   │  Prompt  │   │          │    │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘    │
│       │                                             │          │
│       v                                             v          │
│  ┌──────────┐   ┌──────────────────────────────────────────┐   │
│  │ Classify │   │         Tool Discovery                   │   │
│  │ (bypass) │   └──────────────────────────────────────────┘   │
│  └──────────┘                    │                              │
│                                  v                              │
│            ┌──────────┐   ┌──────────┐                         │
│            │ Context  │──→│   LLM    │◄─┐                      │
│            │ Compact  │   │   Call   │  │ 도구 루프             │
│            └──────────┘   └──────────┘  │ (최대 20회)          │
│                                │        │                      │
│                                v        │                      │
│                           ┌──────────┐  │                      │
│                           │ Execute  │──┘                      │
│                           │ (MCP)    │                         │
│                           └──────────┘                         │
│                                │                               │
│                                v                               │
│            ┌──────────┐   ┌──────────┐   ┌──────────┐         │
│            │ Validate │──→│  Decide  │──→│ Complete  │         │
│            │ (평가)   │   │          │   │          │         │
│            └──────────┘   └──┬───────┘   └──────────┘         │
│                              │                                 │
│                              └── 점수 < threshold → Plan 재시도│
│                                  (최대 3회)                    │
└─────────────────────────────────────────────────────────────────┘
```

## 4가지 사용 모드

### 1. stdio CLI (기본, 권장)

Python이나 다른 프로세스에서 subprocess로 호출합니다.  
stdin에 JSON-RPC 요청을 보내고, stdout에서 이벤트 + 결과를 읽습니다.

```bash
# 빌드
cargo build --release --bin xgen-harness-stdio

# 실행 예시
echo '{"jsonrpc":"2.0","id":1,"method":"harness/run","params":{"text":"안녕","provider":"anthropic","model":"claude-sonnet-4-6","api_key":"sk-..."}}' \
  | ./target/release/xgen-harness-stdio
```

**프로토콜 흐름:**

```
Python (호출자)                    xgen-harness-stdio
    │                                    │
    │── stdin: JSON-RPC request ───────→ │
    │   (한 줄, 전송 후 stdin 닫기)       │
    │                                    │
    │←─ stdout: JSON-RPC notification ── │  이벤트 (N개)
    │   {"method":"harness/event",...}    │
    │              ...                   │
    │←─ stdout: JSON-RPC response ────── │  최종 결과
    │   {"id":1,"result":{"text":"..."}} │
    │                                    │  프로세스 종료
```

### 2. Rust 라이브러리

다른 Rust 서비스에서 직접 임포트합니다.

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
    .run()
    .await?;
```

이벤트 스트리밍이 필요하면:

```rust
let output = HarnessBuilder::new()
    .provider("openai", "gpt-4o")
    .text("분석해줘")
    .run_with_events(|event| {
        println!("[{}] {:?}", event.event, event.data);
    })
    .await?;
```

### 3. HTTP 서버

독립 서비스로 Docker에서 실행합니다.

```bash
cargo build --release --features server --bin xgen-harness-executor
./target/release/xgen-harness-executor  # :8000
```

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/health` | 헬스체크 |
| POST | `/api/harness/execute/simple` | 단일 에이전트 |
| POST | `/api/harness/execute/legacy` | 기존 workflow JSON 자동 변환 후 실행 |

### 4. Python 네이티브 모듈 (PyO3)

```bash
python3 -m venv .venv && source .venv/bin/activate
maturin develop --features python
```

```python
import xgen_harness

result = xgen_harness.run(
    text="분석해줘",
    provider="anthropic",
    model="claude-sonnet-4-6",
    api_key="sk-...",
)
```

---

## 10단계 파이프라인

| 단계 | 모듈 | 역할 |
|------|------|------|
| **Input** | `bootstrap.rs` | API 키 확인, 입력 검증 |
| **Memory** | `memory_read.rs` | 이전 실행 결과 키워드 매칭 프리페치 |
| **System Prompt** | `context_build.rs` | 역할/도구지침/톤/출력효율/환경정보 조립 |
| **Plan** | `plan.rs` | 스프린트 계약 (목표, 전략, 완료기준) |
| **Tool Index** | `tool_discovery.rs` | MCP 도구 목록 캐시, 스키마 로드 |
| **Context** | `context_compact.rs` | 토큰 버짓 체크 + 3단계 자동 압축 |
| **LLM** | `llm_call.rs` | SSE 스트리밍, 지수 백오프(529/rate-limit), 모델 폴백 |
| **Execute** | `tool_execute.rs` | MCP 도구 실행 (Read=병렬, Write=직렬) |
| **Validate** | `validate.rs` | 독립 평가 LLM (관련성/완전성/정확성, 0~1 점수) |
| **Decide** | `decide.rs` | 점수 < threshold → Plan 재시도 (최대 3회) |
| **Complete** | `memory_write.rs` | 최종 반환, 메트릭(tokens/cost), DB 저장 |

### 자동 바이패스 (classify.rs)

입력 복잡도를 규칙 기반(0ms)으로 판별하여 불필요한 단계를 건너뜁니다.

| 입력 예시 | 분류 | 파이프라인 |
|-----------|------|-----------|
| "hi", "안녕" | Simple | minimal (4단계) |
| "CSV 분석해줘" | Moderate | standard (7단계) |
| "RAG 검색 후 비교 보고서" | Complex | full (12단계) |

### 프리셋

| 프리셋 | 단계 수 | 포함 단계 |
|--------|---------|-----------|
| `minimal` | 4 | Input → LLM → Complete |
| `standard` | 7 | Input → Plan → LLM ↔ Execute → Complete |
| `full` | 12 | 전체 (Memory, Validate, Decide 포함) |

---

## Feature Flags

| Feature | 기본 | 설명 | 추가 의존성 |
|---------|------|------|-------------|
| `core` | O | 상태 머신, LLM, MCP, Builder | - |
| `stdio` | O | stdin/stdout JSON-RPC CLI | - |
| `server` | - | HTTP 서버 (axum, JWT) | axum, tower-http, jsonwebtoken |
| `python` | - | PyO3 네이티브 모듈 | pyo3 |

```bash
# 기본 (stdio CLI)
cargo build --release

# HTTP 서버 포함
cargo build --release --features server

# Python .so
maturin build --features python

# 라이브러리만 (바이너리 없이)
cargo build --release --no-default-features --features core
```

---

## 멀티에이전트 오케스트레이션

| 패턴 | 설명 |
|------|------|
| **Sequential** | A → B → C 순차 실행 |
| **Pipeline** | 앞 에이전트 출력 → 다음 에이전트 입력으로 체이닝 |
| **Supervisor** | Lead 에이전트가 `[DELEGATE:이름]` 태그로 Worker에게 위임 |
| **Parallel** | 동시 실행 후 결과 집계 |

---

## stdio JSON-RPC 프로토콜

### 요청 (stdin)

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "harness/run",
  "params": {
    "text": "분석해줘",
    "provider": "anthropic",
    "model": "claude-sonnet-4-6",
    "api_key": "sk-...",
    "system_prompt": "너는 데이터 분석가야",
    "stages": ["input", "plan", "llm", "execute", "complete"],
    "tools": ["mcp-server-name"],
    "temperature": 0.7,
    "max_tokens": 8192,
    "max_retries": 3,
    "eval_threshold": 0.7
  }
}
```

모든 params 필드는 `text`를 제외하면 선택사항입니다.

### 이벤트 알림 (stdout, 여러 줄)

```json
{"jsonrpc":"2.0","method":"harness/event","params":{"event":"stage_enter","data":{"stage":"Plan"}}}
{"jsonrpc":"2.0","method":"harness/event","params":{"event":"message","data":{"type":"text","text":"..."}}}
{"jsonrpc":"2.0","method":"harness/event","params":{"event":"tool_call","data":{"name":"search","input":{}}}}
{"jsonrpc":"2.0","method":"harness/event","params":{"event":"stage_exit","data":{"stage":"Plan"}}}
```

### 최종 응답 (stdout, 마지막 줄)

성공:
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
| `stage_enter` | 단계 시작 |
| `stage_exit` | 단계 종료 |
| `message` | LLM 텍스트 스트리밍 |
| `tool_call` | 도구 호출 시작 |
| `tool_result` | 도구 실행 결과 |
| `evaluation` | 검증 점수 |
| `decision` | 재시도/완료 결정 |
| `error` | 에러 발생 |
| `log` | 내부 로그 |

---

## 디렉토리 구조

```
xgen-harness-executor/
├── Cargo.toml
├── pyproject.toml              # maturin (PyO3 빌드용)
├── bridge/
│   ├── server.py               # Python 노드 MCP 브릿지 (stdin/stdout)
│   └── service_tools.py        # 외부 서비스 MCP 브릿지
├── src/
│   ├── lib.rs                  # 라이브러리 루트 (feature-gated 모듈 등록)
│   ├── main.rs                 # HTTP 서버 진입점 (feature = "server")
│   ├── stdio_main.rs           # stdio CLI 진입점 (feature = "stdio")
│   ├── stdio.rs                # JSON-RPC 2.0 프로토콜 타입
│   ├── events.rs               # SseEvent (전 모드 공용)
│   ├── builder.rs              # HarnessBuilder (fluent API)
│   ├── python.rs               # PyO3 바인딩 (feature = "python")
│   ├── state_machine/
│   │   ├── agent_executor.rs   # AgentStateMachine (while 루프 기반)
│   │   ├── orchestrator.rs     # 멀티에이전트 패턴
│   │   └── stage.rs            # HarnessStage enum + 프리셋 + 전이 규칙
│   ├── stages/
│   │   ├── bootstrap.rs        # Input
│   │   ├── memory_read.rs      # Memory
│   │   ├── context_build.rs    # System Prompt
│   │   ├── plan.rs             # Plan
│   │   ├── tool_discovery.rs   # Tool Index
│   │   ├── context_compact.rs  # Context (토큰 압축)
│   │   ├── llm_call.rs         # LLM
│   │   ├── tool_execute.rs     # Execute (MCP 도구)
│   │   ├── validate.rs         # Validate
│   │   ├── decide.rs           # Decide
│   │   ├── memory_write.rs     # Complete (DB 저장)
│   │   ├── classify.rs         # 자동 바이패스
│   │   └── recover.rs          # 에러 복구
│   ├── context/                # 컨텍스트 윈도우 관리
│   ├── llm/                    # LLM Provider (Anthropic / OpenAI)
│   │   ├── provider.rs         # LlmProvider trait + create_provider
│   │   ├── anthropic.rs        # Anthropic 구현
│   │   ├── openai.rs           # OpenAI 구현
│   │   └── streaming.rs        # SSE 스트림 파서
│   ├── mcp/                    # MCP 클라이언트
│   │   ├── client.rs           # McpClientManager (stdio + HTTP transport)
│   │   └── protocol.rs         # JSON-RPC 2.0
│   ├── tools/                  # 도구 레지스트리 + 오케스트레이션
│   └── workflow/               # 워크플로우 정의, React Flow 변환기, DB
└── tests/
    └── integration_test.rs
```

---

## 환경 변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `ANTHROPIC_API_KEY` | - | Anthropic API 키 |
| `OPENAI_API_KEY` | - | OpenAI API 키 |
| `GOOGLE_API_KEY` | - | Google API 키 |
| `DATABASE_URL` | - | PostgreSQL 접속 (실행 로그 저장) |
| `REDIS_URL` | - | Redis 접속 (세션 상태) |
| `PORT` | `8000` | HTTP 서버 포트 (server feature) |
| `RUST_LOG` | `info` | 로그 레벨 |

---

## 빌드 최적화

릴리스 프로파일:

```toml
[profile.release]
codegen-units = 1   # 단일 코드 유닛 (최적화 극대화)
lto = true          # 링크 타임 최적화
opt-level = "z"     # 바이너리 크기 최소화
```

---

## License

MIT
