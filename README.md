# xgen-harness-executor

Rust 상태 머신 기반 에이전트 실행기. Claude Code query.ts 분석 기반 10단계 파이프라인.

## 3가지 사용 모드

```toml
# 1. HTTP 서버 (Docker)
xgen-harness-executor = { path = "." }  # default = ["core", "server"]

# 2. Rust 라이브러리
xgen-harness-executor = { path = ".", default-features = false, features = ["core"] }

# 3. Python 네이티브 모듈
# maturin develop --features python
```

## 10단계 파이프라인

```
Input → Memory → System Prompt → Plan → Context →
LLM ↔ Execute (도구 루프, 최대 20회) →
Validate → Decide → Complete
```

| 단계 | 역할 |
|------|------|
| Input | API 키 확인, 입력 검증 |
| Memory | 이전 실행 결과 키워드 매칭 프리페치 |
| System Prompt | 역할/도구지침/톤/출력효율/환경정보 조립 |
| Plan | 스프린트 계약 (목표, 전략, 완료기준) |
| Context | 토큰 버짓 체크 + 3단계 자동 압축 (9섹션 구조화 요약) |
| LLM | SSE 스트리밍, 지수백오프(529/rate-limit), 모델폴백 |
| Execute | MCP 도구 실행 (Read=병렬, Write=직렬) |
| Validate | 독립 평가 LLM (관련성/완전성/정확성/계약이행, 0~1) |
| Decide | 점수 < threshold → Plan 재시도 (최대 3회) |
| Complete | 최종 반환, 메트릭(tokens/cost), DB 저장 |

### 자동 바이패스 (classify.rs)

"hi" 같은 단순 입력에 12단계 돌리는 건 낭비. 입력 복잡도를 규칙 기반(0ms)으로 판별하여 자동 다운그레이드:

| 입력 | 분류 | 파이프라인 |
|------|------|-----------|
| "hi", "안녕" | Simple | minimal (4단계) |
| "CSV 분석해줘" | Moderate | standard (7단계) |
| "RAG 검색 후 비교 보고서" | Complex | full (12단계) |

### 프리셋

| 프리셋 | 단계 수 | 용도 |
|--------|---------|------|
| minimal | 4 | 단순 대화 |
| standard | 7 | 계획 + 도구 |
| full | 12 | 검증/재시도 포함 |

## Feature Flags

| Feature | 포함 | 제외 |
|---------|------|------|
| `core` | 상태 머신, LLM, MCP, Builder | axum, tower-http |
| `server` | core + HTTP 서버 | - |
| `python` | core + PyO3 바인딩 | axum |

## 빠른 시작

### HTTP 서버

```bash
cargo run  # localhost:8000
curl http://localhost:8000/health
```

### Rust 라이브러리

```rust
use xgen_harness_executor::prelude::*;

let output = HarnessBuilder::new()
    .provider("anthropic", "claude-sonnet-4-6")
    .api_key("sk-...")
    .text("분석해줘")
    .stages(["input", "system_prompt", "plan", "llm", "execute", "complete"])
    .run()
    .await?;
```

### Python 네이티브 모듈

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
    stages=["input", "system_prompt", "plan", "llm", "execute", "complete"],
)
```

## API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | /health | 헬스체크 |
| POST | /api/harness/execute/simple | 단일 에이전트 |
| POST | /api/harness/execute/legacy | 기존 workflow JSON → 자동 변환 후 실행 |

## 멀티에이전트 오케스트레이션

| 패턴 | 설명 |
|------|------|
| Sequential | A → B → C 순차 실행 |
| Pipeline | 앞 출력 → 다음 입력 체이닝 |
| Supervisor | Lead가 Worker에게 [DELEGATE:이름] 태그로 위임 |
| Parallel | 동시 실행 후 집계 |

## 디렉토리 구조

```
src/
├── events.rs          — SseEvent (core 모듈, 서버/라이브러리 공용)
├── builder.rs         — HarnessBuilder (임베드용 fluent API)
├── python.rs          — PyO3 바인딩 (feature = "python")
├── api/               — HTTP 핸들러 (feature = "server")
│   ├── http.rs        — execute/simple, execute/legacy
│   └── sse.rs         — SseEvent re-export
├── stages/            — 10단계 파이프라인
│   ├── bootstrap.rs   — Input
│   ├── memory_read.rs — Memory
│   ├── context_build.rs — System Prompt
│   ├── plan.rs        — Plan (스프린트 계약)
│   ├── tool_discovery.rs — Tool Index
│   ├── context_compact.rs — Context (3단계 압축)
│   ├── llm_call.rs    — LLM (지수백오프, 529감지)
│   ├── tool_execute.rs — Execute (MCP 도구)
│   ├── validate.rs    — Validate (독립 평가 LLM)
│   ├── decide.rs      — Decide (재시도 결정)
│   ├── memory_write.rs — Save (DB 저장, 메트릭)
│   ├── classify.rs    — 자동 바이패스 (입력 복잡도 분류)
│   └── recover.rs     — 에러 복구 (Compact/Escalate/Fallback/Retry/GiveUp)
├── state_machine/
│   ├── agent_executor.rs — AgentStateMachine (while 루프 상태 머신)
│   ├── orchestrator.rs   — 멀티에이전트 패턴 정의
│   └── stage.rs          — HarnessStage enum, 프리셋, 전이 규칙
├── context/           — 컨텍스트 관리
├── llm/               — LLM Provider (Anthropic/OpenAI 직접 SSE)
├── mcp/               — MCP 클라이언트 (JSON-RPC 2.0)
├── tools/             — 도구 레지스트리 + 오케스트레이션
└── workflow/          — harness-v1 정의, React Flow 변환기, DB
```
