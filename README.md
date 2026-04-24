<div align="center">

# xgen-harness

### 13 Stage 에이전트 실행 프레임워크 — Stage = 환경 슬롯

[![PyPI](https://img.shields.io/pypi/v/xgen-harness?color=blue&label=PyPI)](https://pypi.org/project/xgen-harness/)
[![Python](https://img.shields.io/pypi/pyversions/xgen-harness)](https://pypi.org/project/xgen-harness/)
[![License](https://img.shields.io/pypi/l/xgen-harness)](https://pypi.org/project/xgen-harness/)

```bash
pip install xgen-harness
```

</div>

---

## 한 줄 요약

> 워크플로우를 **"짜는 것"** 이 아니라 **"설정하는 것"** 으로 바꾼 에이전트 실행기.  
> 13 Stage 가 환경 슬롯(capability/도구/리소스/파라미터를 LLM 에 노출), 사용자는 **무엇을** 할지만 선언, 하네스가 **어떻게** 자동 조립.

**v0.25.0 기준**
1. **Stage = 환경 슬롯** + **LLM 자율 선택** (Planner 통제탑 · Auto/Selected/Off 3 모드)
2. **자동 오케스트레이터** (linear/iterative/plan_execute/react/dag) — 행동 속성은 `OrchestratorSpec.replan_per_iter / max_iterations_override` 로 선언적. 외부 오케스트레이터도 동일 계약.
3. **Strategy × Capability 3층 구조**
4. **Policy Gate** (v0.17.0) — 선언형 Guard 체인 × 4 훅 포인트
5. **양방향 MCP** (v0.18.0) — 하네스 워크플로우 → wheel → **MCP stdio 서버 자동 말아올리기**, 마켓·설치된 MCP 를 s04_tool 카탈로그에 자동 주입
6. **Sandbox Verifier** (v0.20.0) — 발행 전 `initialize + tools/list + rlimit` **격리 게이트**
7. **NOM IR 허브** (v0.21.0) — Stage/Strategy/Tool/MCP/Plugin 을 단일 IR 로 통합. `to_mcp_schema()` / `to_sandbox_payload()` / `to_wheel_snapshot()` 3 변환. `compile_nom_graph(graph, ...)` one-shot
8. **엔진 독립성 완결** (v0.22.0) — xgen 특화 코드(Adapter/Service/NodeAdapter/SSE 2,040 LOC) 는 호스트 측 소유. `ExternalNodeRef` Protocol 로 호스트가 4 필드 dataclass 만 등록하면 duck-typing 자동 감지.
9. **MCP Tool Annotations 1급화** (v0.23.0) — Tool ABC 에 4 힌트 1급 속성. `s07_act` 이름 휴리스틱 완전 폐기 → `annotations.readOnlyHint` 우선순위 조회.
10. **HITL Guard + Agent-controlled Compact Tool** (v0.24.0) — `destructiveHint=true` 도구 호출 전 approval 모달 + `CompactTool` 로 LLM 이 자율 컨텍스트 압축.
11. **ToolSource 단일 공급 채널** (v0.25.0) — s04_tool 이 도구를 얻는 **유일한 경로**. `mcp_sessions` / `custom_tools` / `node_tags` / `cli_skills` 4 하드코딩 stage_param 전부 제거. 이식·외부 플러그인은 `ToolSource` Protocol (`source_id` / `display_name` / `filter_schema` / `list_tools(filters)` / `call_tool(name, args)` / `has_tool(name)`) 만 만족해서 `register_tool_source()` 또는 `entry_points(group="xgen_harness.tool_sources")` 로 합류. 신설 `GET /api/harness/tool-sources` 가 등록된 모든 소스의 메타 + `list_tools()` 결과를 한 번에 내려줘서 프론트 s04 가 **하드코딩 Box 없이 동적 N Box** 렌더. `use_request_headers()` 컨텍스트가 Authorization / x-user-* 를 downstream ToolSource 에 자동 전파.

---

## 🧩 ToolSource — 도구 공급의 단일 경로 (v0.25.0)

하네스 에이전트가 쓸 수 있는 도구는 **한 가지 경로** 로만 들어온다:

```python
from xgen_harness.tools import ToolSource, register_tool_source

class MySource:
    source_id = "my-tools"
    display_name = "My Tools"
    display_name_ko = "내 도구"
    description = "외부 API 도구 모음"
    icon = "🛠"
    category = "api"
    # 필터 스키마 — 프론트 s04 UI 가 Box 안에 sub-UI 로 자동 렌더.
    filter_schema = {
        "tags": {"type": "multi_select", "options_source": "my-tags",
                 "label_ko": "태그", "label_en": "Tags"},
    }

    async def list_tools(self, filters=None) -> list[dict]:
        # filters["tags"] 있으면 필터 적용. 각 dict 는 {name, description,
        # input_schema, annotations?, tags?} 표준 스키마.
        ...

    async def call_tool(self, name: str, args: dict) -> dict:
        # tool 실행. 반환 dict 는 {"content": str, "is_error": bool?}.
        ...

    def has_tool(self, name: str) -> bool: ...

register_tool_source(MySource())
```

**entry_points 자동 발견**:

```toml
# pyproject.toml
[project.entry-points."xgen_harness.tool_sources"]
my_source = "my_pkg:MySource"
```

pip install 후 엔진 재시작 → **엔진 / 이식 / 프론트 코드 0 수정** 으로 s04 UI 에 "My Tools" Box 가 자동 등장하고 LLM 이 호출 가능.

**stage_params** (v0.24 → v0.25 Breaking):

| v0.24 (제거) | v0.25 |
|---|---|
| `mcp_sessions: list[str]` | `selected_tools: dict[str, list[str]]` (source_id → 도구 이름) |
| `custom_tools: list[str]` | 키 없음 = 소스 전체, `[]` = 소스 비활성 |
| `node_tags: list[str]` | `tool_source_filters: dict[str, dict]` (소스별 list_tools 필터) |
| `cli_skills: list[str]` | (껍데기였음 — GC) |

**엔드포인트** (신설):

```
GET /api/harness/tool-sources?include_tools=true&filters=<json>
→ {
    "sources": [
      {
        "source_id": "mcp-sessions",
        "display_name": "MCP Sessions",
        "display_name_ko": "MCP 세션",
        "description": "...",
        "icon": "🔌",
        "category": "mcp",
        "filter_schema": {"session_ids": {...}},
        "tools": [{"name": "...", "description": "...", "input_schema": {...}}]
      },
      ...
    ]
  }
```

요청 헤더 (Authorization / x-user-*) 는 `use_request_headers()` contextvar 로 전파되어 각 ToolSource 의 self-loopback 호출에 재사용된다.

---

## 🎯 Phase A / B / C 수렴 — 한 그림

외부 기여자는 `NOMGraph` 만 만들면 **세 경로** 에 자동 합류:

```
                       외부 기여자는 NOMGraph 만 만들면…
                                     ▼
                             ┌───────────────┐
                             │   NOMGraph    │   ← v0.21.0 IR 허브
                             │  (Phase C)    │
                             └──┬─────┬──────┘
                                │     │     └────────┐
                     to_wheel   │     │ to_mcp       │ to_sandbox
                     _snapshot  ▼     ▼ _schema      ▼ _payload
                      ┌─────────────┐ ┌─────┐   ┌─────────┐
                      │ compile     │ │ MCP │   │ Sandbox │
                      │ _workflow   │ │tools│   │.run_nom │
                      │ (v0.10+)    │ │/list│   │_tool    │
                      └──┬──────────┘ └──┬──┘   └────┬────┘
                         ▼               ▼           ▼
                      [wheel]    [Claude Desktop]  [격리 실행]
                         │
                 ┌───────▼────────┐
                 │ Sandbox Gate   │  ← v0.20.0 Phase B
                 │ MCPStdioVerify │     (initialize + tools/list + rlimit)
                 └───────┬────────┘
                         ▼
                 [Station POST /sessions]     ← v0.18.0 Phase A
                         ▼
                 [s04_tool 카탈로그]          ← 사용자 UI (양방향)
```

| Phase | 버전 | 무엇을 해결했나 |
|---|---|---|
| **A** | v0.18.0 | 양방향 MCP — 하네스 ↔ MCP 생태계. wheel 을 MCP stdio 서버로 자동 말아올리고, Station 의 활성 세션을 카탈로그로 자동 가져온다 |
| **B** | v0.20.0 | Sandbox Gate — Station 등록 전 격리 검증 (`MCPStdioVerifier`). JSON-RPC 왕복 + POSIX rlimit + SHA-256 재현성 해시 |
| **C** | v0.21.0 | NOM IR 허브 — Stage/Strategy/Tool/MCP/Plugin 을 단일 IR 로. `to_mcp_schema()` / `to_sandbox_payload()` / `to_wheel_snapshot()` 3 변환으로 위 3 경로 전부 커버 |

---

## 빠른 시작 — 4 줄

```python
from xgen_harness import Pipeline, HarnessConfig, PipelineState

config = HarnessConfig(provider="anthropic", model="claude-sonnet-4-5-20250929")
pipeline = Pipeline.from_config(config)
state = PipelineState(input_text="마사회 운영 규정 알려줘")
await pipeline.run(state)
print(state.output_text)
```

→ 13 Stage (입력 → 이력 → 프롬프트 → 도구 → 전략·정책 → 컨텍스트 → 본문 LLM → 판정 → 결정 → 저장 → 마무리) 가 default Strategy 로 1 바퀴 실행.

---

## 모드 3 종 — 무엇을 어떻게 설정하나

| 모드 | 코드 | 동작 | 언제 쓰나 |
|---|---|---|---|
| **Off (기본)** | `harness_mode="off"` | 13 Stage 정해진 순서, Plan 안 만듦, 본문 LLM 1회 | 빠른 단발 Q&A |
| **Selected** | `harness_mode="selected"` + `pinned_strategies={...}` | 사용자 핀한 Stage→Strategy hard-pin, 나머지 Planner 자율 | 일부만 강제, 나머진 자율 |
| **Auto** | `harness_mode="autonomous"` | Planner LLM 이 Stage/Strategy/도구/orchestrator_hint 자율 결정 | 복잡 요청·RAG·멀티턴 도구 |

```python
# Off (기본 — 빠른 단발)
config = HarnessConfig(harness_mode="off", max_iterations=1)

# Auto (LLM 자율 + 자동 오케스트레이터)
config = HarnessConfig(harness_mode="autonomous", max_iterations=5)

# Selected (사용자 핀 + 일부 자율)
config = HarnessConfig(
    harness_mode="selected",
    pinned_strategies={"s06_context": "microcompact", "s08_judge": "rule_based"},
)
```

---

## 13 Stage 카탈로그 — 기능 / 설정 / Strategy

### 초기화 그룹 (ingress, 1 회)

| # | Stage | 하는 일 | 주요 설정 | Strategy |
|---|---|---|---|---|
| 0 | **s00_harness** (Planner) | LLM 핸들 owner + 본문 호출 dispatcher (모드별 책임) | `harness_mode`, `provider`, `model` | `streaming` * / `batch` |
| 1 | **s01_input** (필수) | 사용자 입력 추출 + 정규화 | `input_text`, `attached_files` | `default` * / `multimodal` |
| 2 | **s02_history** | 같은 interaction 이전 turn 가져옴 | `history_limit` | `last_n` * / `relevant` |
| 3 | **s03_prompt** | System prompt 주입 | `system_prompt`, `prompt_id` | `static` * / `templated` |
| 4 | **s04_tool** | LLM 노출 도구 카탈로그 | `mcp_sessions`, `custom_tools`, `cli_skills`, `node_tags`, `capabilities` | `default` * / `progressive` / `auto` |

### 에이전트 루프 그룹 (loop, max_iterations 회 반복)

| # | Stage | 하는 일 | 주요 설정 | Strategy |
|---|---|---|---|---|
| 5 | **s05_strategy** | 각 Stage 의 Strategy 결정 | `pinned_strategies` (Selected) | `default` * / `pinned_first` / `llm_decide` / `cascade` |
| 5 | **s05_policy** ◆ | 선언형 Guard 체인을 4 훅 포인트에 집행 | `guards: [{name, params}]` | — (Guard 조합) |
| 6 | **s06_context** | RAG/온톨로지/DB 검색 → 컨텍스트 주입 | `rag_collections`, `rag_top_k`, `ontology_collections`, `db_connections` | `microcompact` * / `context_collapse` / `autocompact_llm` / `cascade` / `progressive_3level` / `none` |
| 7 | **s07_act** ★ | 본문 LLM 호출 + tool_use multi-turn | `max_tool_rounds`, `force_tool_use` | `default` * / `react` |
| 8 | **s08_judge** | 응답 품질 평가 (0~1 점수) | `validation_threshold`, `judge_model` | `llm_judge` * / `rule_based` / `none` |
| 9 | **s09_decide** (필수) | judge 결과 보고 loop_decision 설정 | — | `default` * / `always_complete` |

`◆` = Pipeline 이 `role="policy_gate"` 로 찾아 `pre_main` / `pre_tool` / `post_response` / `loop_boundary` 4 훅에 호출. 일반 loop 순서는 bypass.

### 최종 그룹 (egress, 1 회)

| # | Stage | 하는 일 | 주요 설정 | Strategy |
|---|---|---|---|---|
| 10 | **s10_save** | DB 실행 기록 저장 | `save_metrics`, `save_full_text` | `default` * / `none` |
| 11 | **s11_finalize** (필수) | 최종 응답 + MetricsEvent | — | `default` * / `lite` |

`*` = 기본 Strategy. **필수** Stage 는 비활성화 불가.

---

## Strategy 변경 — 두 가지 방식

### 방식 1 — config 에 직접 (코드)
```python
config = HarnessConfig(
    active_strategies={
        "s06_context": "cascade",        # RAG L3→L4→L5 자동 압력
        "s08_judge": "none",             # 검증 skip
    },
)
```

### 방식 2 — Strategy 변형 (디폴트 그대로 두고 파라미터만 바꾼 사본)
```python
config = HarnessConfig(
    strategy_variants={
        "s06_context": [{
            "name": "microcompact_strict",     # 새 이름
            "base": "microcompact",            # 복제 원본
            "params": {"threshold": 95},       # 파라미터 override
            "label": "엄격 압축",
        }],
    },
    active_strategies={"s06_context": "microcompact_strict"},
)
```

---

## 자동 오케스트레이터 (Auto 모드 전용)

Auto 모드에서 Planner 가 입력·카탈로그 보고 `Plan.orchestrator_hint` 결정 → Phase B loop 가 분기:

| hint | Phase B 동작 | 사용 케이스 |
|---|---|---|
| `linear` | 1 회 실행 후 종료 | 단발 Q&A |
| `iterative` (default) | 매 iter Plan replan + 13 Stage 1바퀴 | 멀티턴 도구 |
| `plan_execute` | 첫 Plan 고수, replan 생략, 반복 | 정형 절차 |
| `react` | 엔진 no-op, 이식측 dispatcher 위임 | 외부 ReAct 통합 |
| `dag` | 엔진 no-op, 이식측 DAG runner 위임 | 멀티에이전트 병렬 |

**외부 hint 추가**:
```python
from xgen_harness.core.orchestrator_registry import register_orchestrator
register_orchestrator("custom_swarm", description="My swarm runner", dispatch_key="swarm_v2")
```
또는 `entry_points` 그룹 `xgen_harness.orchestrators` 노출 → 자동 합류.

---

## 확장 — 외부 패키지가 끼워넣는 10 지점

| # | 지점 | entry_points 그룹 | 용도 |
|---|---|---|---|
| 1 | **Stage** | `xgen_harness.stages` | 새 Stage (예: 자체 Planner, 도메인 Stage) |
| 2 | **Strategy** | `xgen_harness.strategies` | 한 Stage 의 새 변형 |
| 3 | **Capability** | `xgen_harness.capabilities` | 선언적 도구 wiring (예: `retrieval.web_search`) |
| 4 | **Provider** | `xgen_harness.providers` | 새 LLM provider |
| 5 | **Orchestrator** | `xgen_harness.orchestrators` | 새 orchestrator hint |
| 6 | **Tool** | `xgen_harness.tool_sources` | 단일 도구 또는 도구 묶음 |
| 7 | **NodeAdapter** | `xgen_harness.node_adapters` | 캔버스 노드 → Stage 어댑터 |
| 8 | **Guard** | `xgen_harness.guards` | Policy Gate 에 꽂히는 정책 Guard |
| 9 | **SandboxVerifier** 🆕 | `xgen_harness.sandbox_verifiers` | publish 전 격리 검증 (mcp-stdio/mcp-http/wasm) |
| 10 | **PublishTarget** 🆕 | `xgen_harness.publish_targets` *(이식측)* | 컴파일된 wheel 의 발행 대상 (mcp-station, gallery, 사내 PyPI 등) |

```toml
# pyproject.toml — 한 줄이면 외부 wheel 이 자동 합류
[project.entry-points."xgen_harness.strategies"]
my_compactor = "my_pkg.compactor:MyCompactor"
```

---

## Policy Gate (v0.17.0) — 선언형 Guard 체인

"submit_result 호출 전 iterative_document_search 를 최소 1회 불러야 한다" 같은 **도구 호출 선행조건 / 입출력 정책 / 예산 제한** 을 **코드 수정 없이 데이터로** 선언합니다.

```python
config = HarnessConfig(
    stage_params={
        "s05_policy": {
            "guards": [
                {"name": "iteration"},
                {"name": "cost_budget", "params": {"cost_budget_usd": 5.0}},
                {"name": "tool_precondition", "params": {
                    "rules": [{
                        "tool": "submit_result",
                        "require_prior": [{"tool": "iterative_document_search", "min_count": 1}],
                        "when": {"path": "fileNo[*].status", "equals": "01"},
                        "message": "합격 판정 전 QA 기준을 iterative_document_search 로 조회하세요.",
                    }]
                }},
            ]
        }
    }
)
```

### 4 훅 포인트

| 훅 | 호출 시점 | Guard 예시 |
|---|---|---|
| `pre_main` | 본문 LLM 호출 직전 | ContentGuard (입력 검사) |
| `pre_tool` | 도구 실행 직전 (pending_tool_calls 각각) | ToolPreconditionGuard |
| `post_response` | LLM 응답 직후 | ContentGuard (출력 검사) |
| `loop_boundary` | 루프 경계 (iter 끝) | IterationGuard / CostBudgetGuard / TokenBudgetGuard |

Guard 는 자기 `hook_points` 집합을 선언 → Pipeline 이 훅별 필터링 후 실행. 차단 시:
- **pre_tool**: 해당 도구를 pending 에서 제거 + 가짜 `tool_result(is_error=True)` 주입 → LLM 이 자체 교정
- **기타 3 훅**: `state.policy_block_reason` 설정 + `loop_decision="complete"`

### 내장 5 Guard

- `iteration` — `config.max_iterations` 도달 시 종료
- `token_budget` — 누적 토큰 95% 초과 시 종료
- `cost_budget` — 누적 비용 초과 시 종료
- `content` — 정규식/PII 감지 (입력/출력)
- `tool_precondition` — 도구 호출 선행조건 (규칙 기반, 범용)

### 외부 Guard 추가 (entry_points 한 줄)

```toml
# pyproject.toml
[project.entry-points."xgen_harness.guards"]
my_guard = "my_pkg.guards:MyGuard"
```

`pip install my-pkg` 한 번이면 UI Guard 드롭다운에 자동 합류. 엔진·이식·프론트 코드 수정 불필요.

---

## 양방향 MCP (v0.18.0) — 하네스 ↔ MCP 생태계

### ➡️ 내보내기: 하네스 워크플로우 → MCP stdio 서버

**워크플로우 하나를 `pip install` 가능한 wheel 로 컴파일**, 그 wheel 이 그대로 **MCP stdio 서버**. 다른 하네스·Claude Desktop·임의 MCP 클라이언트가 도구로 사용 가능.

```python
from xgen_harness import HarnessConfig, compile_workflow

result = compile_workflow(
    harness_config=HarnessConfig(...),
    workflow_data={"workflow_type": "harness", ...},
    gallery_name="my_agent",
    gallery_version="0.1.0",
    out_dir="/tmp/build",
)
# result.wheel_path / result.dist_name / result.package_name
```

설치 후 세 가지 CLI 자동 주입:

```bash
pip install 'xgen-gallery-my_agent[mcp]'
xgen-gallery-my_agent run --input "안녕"   # 일회성 호출
xgen-gallery-my_agent info                  # 갤러리 메타
xgen-gallery-my_agent serve-mcp             # MCP stdio 서버로 기동
```

Claude Desktop `claude_desktop_config.json` 예시:
```json
{
  "mcpServers": {
    "my-agent": {
      "command": "xgen-gallery-my_agent",
      "args": ["serve-mcp"]
    }
  }
}
```

→ Claude Desktop 이 이 하네스 워크플로우를 **도구로** 호출. `run_workflow(input, overrides)` 하나만 노출됨.

### ⬅️ 가져오기: 마켓 / 설치된 MCP → `s04_tool` 카탈로그

```python
config = HarnessConfig(
    mcp_sessions=["my-playwright", "krra-search"],   # mcp-station 활성 세션 ID
)
```

`s04_tool` 이 xgen-mcp-station 에서 세션의 도구 목록을 가져와 LLM 카탈로그에 합류. 이식측(xgen-workflow) UI 는 마켓에서 원클릭 설치 → 바로 선택 가능.

---

## Sandbox Verifier (v0.20.0) — 발행 전 격리 검증 게이트

외부 호스트(MCP Station, 사내 레지스트리 등) 에 wheel 을 **등록하기 전** 에 건전성·스키마·리소스를 검증하는 관문. `initialize + tools/list` JSON-RPC 왕복 + POSIX rlimit + timeout + SHA-256 재현성 해시.

### 기본 사용

```python
from xgen_harness import MCPStdioVerifier, SandboxLimits

v = MCPStdioVerifier()
result = v.verify(
    command=["python", "-u", "-m", "xgen_gallery_my_agent.cli", "serve-mcp"],
    timeout_sec=10.0,
    limits=SandboxLimits(
        cpu_seconds=15,           # RLIMIT_CPU
        address_space_mb=1024,    # RLIMIT_AS
        max_open_files=128,       # RLIMIT_NOFILE
        max_file_size_mb=16,      # RLIMIT_FSIZE
        no_core_dump=True,
    ),
)
assert result.ok, result.error
print(result.tool_count, result.payload_hash[:12])
```

`VerifyResult` 필드:
- `ok` / `tool_count` / `tools` — 검증 결과와 발견된 도구
- `handshake_ms` / `tools_ms` — 단계별 latency
- `payload_hash` — 정규화된 tools 배열의 SHA-256 (같은 wheel → 같은 해시, 재현성 지표)
- `stderr_tail` — 진단용 마지막 4KB
- `applied_limits` — 실제 적용된 rlimit
- `timed_out` / `error`

### 확장: 새 protocol verifier 추가

```toml
# pyproject.toml
[project.entry-points."xgen_harness.sandbox_verifiers"]
mcp_http = "my_pkg.verifiers:MCPHTTPVerifier"
```

```python
from xgen_harness import SandboxVerifier, VerifyResult

class MCPHTTPVerifier:
    name = "mcp-http"
    def verify(self, *, command, env=None, limits=None, timeout_sec=10.0) -> VerifyResult:
        # command[0]=wrapper, command[1:]=HTTP URL 등 — protocol 별 해석
        ...
```

`pip install my-pkg` 한 번이면 Registry 에 자동 합류. 엔진 수정 불필요.

### 이식측 wiring (xgen-workflow)

이식측 `MCPStationPublisher.publish()` 가 Station 등록 **전** MCPStdioVerifier 를 호출. `HARNESS_SANDBOX_POLICY` env 로 정책 조절:

| 정책 | 동작 |
|---|---|
| `strict` (기본) | verify 실패 시 발행 거부, HTTP 422 반환 |
| `advisory` | 경고 로그, 발행은 강행 |
| `off` | 검증 스킵 |

통과 시 `payload_hash` 가 Station 세션 메타에 첨부 → 어떤 wheel 이 어떤 도구를 노출했는지 감사 가능.

---

## NOM IR 허브 (v0.21.0) — 하나의 노드 그래프, 세 가지 출력

Stage / Strategy / Tool / MCP 서버 / 외부 플러그인 노드 — 모두 **같은 IR (`NOMGraph`)** 로 표현하고, **세 가지 변환** 으로 wheel / MCP / Sandbox 에 재사용.

```python
from xgen_harness import (
    NOMGraph, NOMNode, NOMKind, NOMParam,
    compile_nom_graph,
)

graph = NOMGraph(nodes=[
    NOMNode(
        id="x.tools.search", kind=NOMKind.TOOL,
        description="웹 검색", entry="my_pkg.tools:search",
        inputs=[NOMParam(name="q", type="string", required=True)],
    ),
    NOMNode(
        id="x.tools.fetch", kind=NOMKind.TOOL,
        description="URL 페치", entry="my_pkg.tools:fetch",
        inputs=[NOMParam(name="url", type="string", required=True)],
    ),
])

# 1) MCP 서버 카탈로그로 — Claude Desktop, Cursor 호환
schema = graph.to_mcp_schema()
# [{"name": "search", "description": "웹 검색", "inputSchema": {...}},
#  {"name": "fetch", ...}]

# 2) 격리 실행 payload 로 — Sandbox.run_nom_tool 의 입력
payload = graph.to_sandbox_payload("x.tools.search", {"q": "hello"})
# {"entry": "my_pkg.tools:search", "input": {...}, "metadata": {...}}

# 3) wheel 배포 — 기존 compile_workflow 와 같은 build 파이프라인 재사용
result = compile_nom_graph(graph, gallery_name="my_tools", gallery_version="0.1.0")
# result.wheel_path → pip install 가능
```

### Tool Synthesis 와의 통합

LLM 이 런타임에 생성한 도구를 바로 wheel 로:

```python
from xgen_harness.tools.synthesis import (
    SynthesizedTool, synthesize_and_register,
    synthesized_tools_as_nom_graph,
)
from xgen_harness import compile_nom_graph

# synthesize_and_register(...) 로 여러 도구 검증 + 등록 후:
graph = synthesized_tools_as_nom_graph([slugify, camelcase, redact_pii])
r = compile_nom_graph(graph, gallery_name="my_synth_tools", gallery_version="0.1.0")
# → pip install 가능한 도구 모듈 한 방에 완성
```

### 현재 엔진 상태 스냅샷

```python
from xgen_harness import snapshot_current_registry_as_nom

nom = snapshot_current_registry_as_nom()
print([n.id for n in nom.nodes])
# ["xgen.stages.s00_harness", "xgen.stages.s01_input", ...,
#  "xgen.strategies.s06_context.compact.microcompact", ...,
#  "xgen.orchestrators.iterative", ...,
#  "xgen.providers.anthropic", ...]
```

엔진 레지스트리(Stage/Strategy/Orchestrator/Provider) 를 통째로 NOM 으로 덤프 — 디버깅, 갤러리 업로드, 샌드박스 복원에 그대로 재사용.

---

## RAG 사용

```python
config = HarnessConfig(
    rag_collections=[
        "masahoe_7a64e5f6-...",      # Qdrant 컬렉션 ID
        "voc_templates_...",
    ],
    stage_params={"s06_context": {"rag_top_k": 5, "rag_ingestion_mode": "both"}},
)
```

`s06_context` 가 입력 보고 컬렉션 검색 → top-k 결과를 system_prompt + tool_only 양쪽 주입.

---

## Capability 선언적 도구

```python
config = HarnessConfig(
    capabilities=["retrieval.web_search", "retrieval.rag_query"],
    capability_params={"retrieval.web_search": {"max_results": 10}},
)
```

`s04_tool` 이 capability registry 에서 자동 매핑 → `tool_definitions` 합류. 외부 패키지가 entry_points 로 capability 등록하면 자동 발견.

---

## 이식 통합 (xgen-workflow)

엔진은 generic primitive. 실서비스 연결은 이식측 `xgen-workflow/controller/workflow/endpoints/` 에서:

| 엔드포인트 | 역할 |
|---|---|
| `GET /harness/stages` | 13 Stage 정의 + stage_config 메타 |
| `GET /harness/options/{source}` | 동적 리소스(mcp-sessions/mcp-market/rag-collections/providers/…) |
| `POST /harness/execute/stream` | SSE 실행 (하네스 전용 경로 — 레거시 workflow 분리) |
| `POST /harness/compile` | wheel 컴파일 + 바이너리 다운로드 |
| `POST /harness/compile/publish` | compile + PublishTarget 발행 (mcp-station / gallery) |
| `POST /harness/mcp/sessions` | MCP Station 세션 CRUD 프록시 (인증 전파) |
| `POST /harness/dag/execute/stream` | 멀티 하네스 DAG orchestration |

자세한 내용: `xgen-workflow/controller/workflow/endpoints/harness.py` + `harness_options_registry.py` + `harness_publish.py`.

---

## 진화 요약

| 버전 | 핵심 |
|---|---|
| `v0.12.0` | REAL HARNESS Phase 1 — `s00_harness` Planner 도입 + 13 Stage 디렉토리화 |
| `v0.13.0` | REAL HARNESS Phase 2 — 단일 provider + iterative planning |
| `v0.14.0` | s00_harness 통제탑 승격 — 본문 LLM 호출 owner + 3 모드 |
| `v0.15.x` | 재귀적 자율주행 — orchestrator_hint + max_iterations + OrchestratorRegistry + fs_scanner 자동 발견 |
| `v0.16.x` | 자가증식 골조 — Sandbox / NOM / NodePlugin / ToolSynthesis + Pipeline Role 체계 (Stage 이름 리터럴 12→0) |
| `v0.17.0` | **Policy Gate** — 선언형 Guard 체인 × 4 훅 포인트 + entry_points Guard 플러그인 |
| `v0.18.0` | **양방향 MCP** (Phase A) — 하네스 → wheel → MCP stdio 서버 / 마켓·Station → s04_tool 카탈로그. 이식측 `PublishTargetRegistry` |
| `v0.19.0` | PyPI 덮어쓰기 + 실측 버그 픽스 |
| `v0.20.0` | **Sandbox Verifier** (Phase B) — `MCPStdioVerifier` + `SandboxVerifier` Protocol + Registry + entry_points + SHA-256 재현성 해시 |
| **`v0.21.0`** | **NOM IR 허브** (Phase C) — `to_mcp_schema()` / `to_sandbox_payload()` / `to_wheel_snapshot()` 3 변환 + `compile_nom_graph` one-shot + Tool Synthesis → wheel 파이프라인 E2E |

이전 변경 (`v0.11.14 → v0.11.23`, Claude Code 5-Level 압축 / tool_choice API / drift-free 연결선)은 [CHANGELOG.md](CHANGELOG.md) 참조.

---

## 로드맵

- **HTTP MCP Verifier** — `MCPStreamableHTTPVerifier` 추가. JSON-RPC over HTTP SSE.
- **Gallery hot-reload** — 설치된 wheel 의 entry_points 핫리스캔 (discover_galleries force-refresh).
- **Docker-wrapped SandboxVerifier** — container 런타임 격리로 rlimit 한계 넘기.
- **NOM → Sandbox 자동 게이트** — `compile_nom_graph(..., verify=True)` 옵션으로 Sandbox Verifier 자동 실행.

---

## 사용자 매뉴얼 (UI)

엔진을 직접 쓰지 않고 XGEN 하네스 페이지 (http://xgen.x2bee.com/harness) 사용자라면 → [docs/confluence/harness-user-manual.md](https://github.com/jinsoo96/xgen-harness-executor/blob/main/docs/confluence/harness-user-manual.md) (사용자 친화 한국어 매뉴얼)

---

## 라이선스

Apache 2.0
