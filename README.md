<div align="center">

# xgen-harness

### Configurable agent runtime — 13 Stage 환경 슬롯 + MCP 양방향 + wheel 컴파일

[![PyPI](https://img.shields.io/pypi/v/xgen-harness?color=blue&label=PyPI)](https://pypi.org/project/xgen-harness/)
[![Python](https://img.shields.io/pypi/pyversions/xgen-harness)](https://pypi.org/project/xgen-harness/)
[![License](https://img.shields.io/pypi/l/xgen-harness)](https://pypi.org/project/xgen-harness/)

```bash
pip install xgen-harness          # 코어
pip install 'xgen-harness[mcp]'   # MCP stdio 서버 + 마켓 연동
pip install 'xgen-harness[api]'   # FastAPI 라우터 (이식측에서만 필요)
```

</div>

---

## 한 줄 설명

> 워크플로우를 **"짜는 것"** 이 아니라 **"설정하는 것"** 으로 바꾼 LLM 에이전트 실행기.
> 13 Stage 가 환경 슬롯(도구·정책·컨텍스트·예산)을 LLM 에 노출, 사용자는 **무엇** 을 선언하면 하네스가 **어떻게** 자동 조립.

| 무엇을 풀어주나 | 어떻게 |
|---|---|
| **다양한 LLM** | provider 레지스트리 + entry_points (anthropic / openai / google / bedrock / vllm 빌트인) |
| **다양한 도구** | `ToolSource` Protocol 단일 채널 (MCP 세션 / 캔버스 노드 / 로컬 함수 / 합성 도구 모두) |
| **다양한 실행 패턴** | Orchestrator 레지스트리 (linear / iterative / react / plan_execute / dag) |
| **워크플로우 배포** | `compile_workflow()` → pip 설치 가능한 wheel → 그대로 **MCP stdio 서버** |
| **정책·예산** | Policy Gate (선언형 Guard 체인 × 4 훅 포인트) |

---

## 어떻게 돌아가는가

요청 한 건은 **세 그룹의 13 Stage** 를 순차적으로 통과한다. 각 Stage 는 자기가 담당하는 환경(도구·정책·컨텍스트·예산) 만 LLM 에 펼쳐 보여주고 다음 Stage 로 `PipelineState` 를 넘긴다.

```
[ ingress · 1회 ]                       [ agent loop · max_iterations 회 ]              [ egress · 1회 ]

  s00 ─ s01 ─ s02 ─ s03 ─ s04   ─▶   s05 ─ s06 ─ s07 ─ s08 ─ s09  ─┐   ─▶   s10 ─ s11
  ──────────────────────────────         ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ │
  LLM 핸들 owner / 입력 / 히스토리 /        계획 / 컨텍스트 / 본문호출 /     │       DB 기록 / 최종 응답
  프롬프트·citation / 도구 카탈로그         judge / 루프 결정                │
                                       ◀─── orchestrator hint 분기 ─────┘
                                            (linear · iterative · plan_execute · react · dag)
```

### 핵심 동작 4 가지

1. **Stage = 환경 슬롯, 단계 아님.** 각 Stage 는 자기 capability·도구·리소스를 progressive disclose 하고, Auto 모드의 Planner LLM 이 그 안에서 자율 선택. 사용자는 **무엇** 을 선언하면 하네스가 **어떻게** 자동 조립.
2. **도구는 한 채널로 들어온다.** MCP 세션 · 캔버스 노드 · 로컬 함수 · LLM 합성 도구 모두 `ToolSource` Protocol 한 갈래로 통합돼 s04 에서 LLM 카탈로그로 합쳐짐.
3. **본문 LLM 호출은 single owner.** `s00_harness` 가 모든 모델 호출의 owner — provider · model · max_tokens · streaming/batch transport 결정이 한 곳에 모이고, 다른 Stage 는 dispatcher 를 통해서만 호출.
4. **워크플로우는 wheel 로 발행된다.** 같은 `HarnessConfig` 를 `compile_workflow()` 한 번 호출이면 `pip install` 가능한 wheel + MCP stdio 서버 + 격리 검증 페이로드(NOMGraph) 가 동시에 산출.

### 한 사이클의 데이터 흐름

```
HarnessConfig + user_input
    │
    ▼
PipelineState  ◀──────────  EventEmitter (17 이벤트, SSE 스트림)
    │
    │   ── ingress ──
    ├─ s00  LLM 핸들 owner / transport(streaming|batch) 선정
    ├─ s01  사용자 입력 정규화 · multimodal 추출
    ├─ s02  같은 interaction 의 이전 turn 로드
    ├─ s03  system_prompt 주입 + citation
    ├─ s04  ToolSource → tool_definitions 합성 (UI 가 selected_tools 로 필터)
    │
    │   ── agent loop (orchestrator_hint 가 결정한 횟수만큼) ──
    ├─ s05_strategy   CoT / ReAct / Capability 계획
    ├─ s05_policy     (옵션) Guard 체인 — pre_main / pre_tool / post_response / loop_boundary
    ├─ s06            RAG · 온톨로지 · DB → 컨텍스트 주입 + 압축 (microcompact / cascade / …)
    ├─ s07            본문 LLM 호출 + tool_use multi-turn   (s00 dispatcher 경유)
    ├─ s08            응답 품질 judge (llm_judge | rule_based | none)
    ├─ s09            judge 결과 → loop_decision (continue / complete)
    │
    │   ── egress ──
    ├─ s10  DB 실행 기록 (이식측 hook)
    └─ s11  최종 응답 + MetricsEvent  →  state.final_output
```

`HarnessConfig.harness_mode` 가 이 사이클의 자율도를 결정한다 (`off` / `selected` / `autonomous`) — 다음 섹션.

---

## 3 모드 — 자유도 vs 안정성

| 모드 | `harness_mode` | 동작 | 언제 쓰나 |
|---|---|---|---|
| **Off** (기본) | `"off"` | 13 Stage 정해진 순서, Plan 안 만듦, 본문 LLM 1회 | 빠른 단발 Q&A |
| **Selected** | `"selected"` + `active_strategies={...}` | 사용자 핀 한 부분만 hard-pin, 나머지 Planner 자율 | 일부만 강제 |
| **Auto** | `"autonomous"` | Planner LLM 이 Stage / Strategy / 도구 / orchestrator_hint 자율 결정 | 복잡 요청 · RAG · 멀티턴 도구 |

```python
# Auto — LLM 이 입력 보고 자율 조립 (RAG 결정, 도구 선택, orchestrator 결정)
config = HarnessConfig(harness_mode="autonomous", max_iterations=5)
assert config.is_autonomous() is True   # v0.25.3 helper

# Selected — 일부만 핀
config = HarnessConfig(
    harness_mode="selected",
    active_strategies={
        "s06_context": "microcompact",   # RAG 압축 전략 핀
        "s08_judge":   "rule_based",
    },
)
```

---

## 13 Stage — "환경 슬롯" 단위로 LLM 에 노출

각 Stage 는 **자기 환경(capability / 도구 / 리소스)을 progressive disclose** 해서 Auto 모드 LLM 이 골라 쓰게 한다.

### 초기화 그룹 (ingress · 1 회)

| # | Stage | 책임 | 주요 `stage_params` | Strategy |
|---|---|---|---|---|
| 0 | **s00_harness** | LLM 핸들 owner + 본문호출 dispatcher (모드별 책임) | `strategy`(transport), `max_tokens`, `thinking_enabled`, `thinking_budget` | `streaming`* / `batch` |
| 1 | **s01_input** ✱ | 사용자 입력 정규화, multimodal 추출 | (없음 — top-level config.user_input) | `default`* / `with_classification` |
| 2 | **s02_history** | 같은 interaction 이전 turn 로드 | `max_history` | `default`* / `embedding_search` |
| 3 | **s03_prompt** | system_prompt 주입 + citation | `system_prompt`, `prompt_id`, `include_rules`, `citation_mode`, `citation_auto_doc_tokens`, `citation_auto_prod_tokens` | `section_priority`* |
| 4 | **s04_tool** | LLM 노출 도구 카탈로그 — **ToolSource 단일 채널** | `selected_tools`, `tool_source_filters` (UI 가 주입) | `progressive_3level`* / `eager_load` / `none` |

### 에이전트 루프 그룹 (loop · `max_iterations` 회)

| # | Stage | 책임 | 주요 `stage_params` | Strategy |
|---|---|---|---|---|
| 5 | **s05_strategy** | 계획 모드 결정 (cot/react/capability) | `planning_mode`, `intent_rules` | `cot_planner`* / `react` / `capability` / `none` |
| 5 | **s05_policy** ◆ | Guard 체인 × 4 훅 포인트 (옵트인) | `guards: [{name, params}]` | — (Guard 합성) |
| 6 | **s06_context** | RAG / 온톨로지 / DB → 컨텍스트 주입 + 압축 | `rag_collections`, `folders`, `files`, `db_connections`, `ontology_collections`, `score_threshold`, `reranker`, `metadata_filter`, `rag_pd_mode`, `rag_ingestion_mode`, `strategy`(compactor), cascade/L3/L4/L5 임계 | `token_budget`* / `sliding_window` / `microcompact` / `context_collapse_overlay` / `autocompact_llm` / `cascade` |
| 7 | **s07_act** | 본문 LLM 호출 + tool_use multi-turn | `timeout`, `result_budget`, `tool_result_preview_threshold`, `tool_result_preview_size` | `default`* / `parallel_read` |
| 8 | **s08_judge** | 응답 품질 평가 (0~1) | `threshold`, `criteria` | `llm_judge`* / `rule_based` / `none` |
| 9 | **s09_decide** ✱ | judge 결과 → loop_decision | `max_retries` (max_iterations 은 top-level config) | `threshold`* / `always_pass` |

### 종료 그룹 (egress · 1 회)

| # | Stage | 책임 | 주요 `stage_params` | Strategy |
|---|---|---|---|---|
| 10 | **s10_save** | DB 실행 기록 (이식측 hook) | `save_enabled` | `default`* / `noop` |
| 11 | **s11_finalize** ✱ | 최종 응답 + MetricsEvent | `output_format` (text/markdown/json) | `default`* / `format_json` |

`✱` = `REQUIRED_STAGES` (비활성화 불가) · `◆` = 옵트인 (registry-only, role 로 호출됨) · `*` = 기본 strategy

> **최근 변경 — 한눈에**:
>
> - **v0.26.x 패치 사이클** (production 라이브 검증 → 발견 → 즉시 fix): `s06_context.files` 부활 (v0.26.1, frontend UI 와 wiring 일치) · OpenAI strict schema 호환 (v0.26.2) · `s10_save` 컬럼명 정합 (v0.26.3) · batch transport 응답 누락 fix (v0.26.4) · Anthropic thinking max_tokens 자동 보정 (v0.26.5) · DAG orchestrator init TypeError fix (v0.26.6) · `max_iter=1` + 도구 활성 빈응답 보강 (v0.26.7).
> - **v0.26.0 — Dead UI 정리**: 사용자 클릭이 LLM 환경에 안 박히던 stage_param 정리. `s01_input.provider` (글로벌 ConfigPanel 와 중복) · `s02_history.memory_source` (코드 미read) · `s09_decide.max_iterations` (top-level config 만 작동) 3건 제거. Label-only 라 동일 동작이던 `s03_prompt.simple` strategy 제거. `s04_tool.none` / `s10_save.noop` 는 분기 코드 신규 구현해 진짜 short-circuit. EventEmitter queue 1000→8000 + drop 카운터.
>   - ⚠ `s06_context.files` 도 v0.26.0 에선 같이 제거됐으나, frontend UI 가 잔존해 클릭 무효화되는 문제로 **v0.26.1 에서 부활** (`metadata_filter.file_name` 자동 라우팅).
> - **v0.25.0 — 도구 채널 단일화**: `s04_tool` 의 `mcp_sessions` / `custom_tools` / `node_tags` / `cli_skills` 4 개 stage_param 사라짐. 모든 도구는 이제 **ToolSource 한 채널** 로 (다음 섹션).

---

## 🧩 도구 한 채널 — `ToolSource` Protocol

하네스 에이전트가 쓸 수 있는 도구는 한 가지 경로로만 들어옵니다.

```python
from xgen_harness import ToolSource, register_tool_source

class MySource:
    source_id = "my-tools"
    display_name = "My Tools"
    display_name_ko = "내 도구"
    description = "외부 API 도구 모음"
    icon = "🛠"
    category = "api"
    # 프론트가 이 Box 안에 sub-UI 자동 렌더 — 검색·필터 UI 데이터로 전달.
    filter_schema = {
        "tags": {"type": "multi_select", "options_source": "my-tags"},
    }

    async def list_tools(self, filters=None) -> list[dict]:
        # 각 dict 표준 스키마: {name, description, input_schema?, annotations?, tags?}
        return [{
            "name": "echo",
            "description": "echo back the input",
            "input_schema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        }]

    async def call_tool(self, name: str, args: dict) -> dict:
        # 반환: {"content": str, "is_error": bool?}
        return {"content": args["text"]}

    def has_tool(self, name: str) -> bool:
        return name == "echo"

register_tool_source(MySource())
```

**한 줄 자동 발견 (entry_points)** — 다른 패키지에서 import 도 안 해도 됩니다:

```toml
# 외부 패키지의 pyproject.toml
[project.entry-points."xgen_harness.tool_sources"]
my_source = "my_pkg:MySource"
```

`pip install` 후 엔진 재시작 → 엔진/이식/프론트 코드 0 수정으로 s04 UI 에 "My Tools" Box 가 등장하고 LLM 이 호출 가능.

**선택 / 필터 (config 단)**

```python
config = HarnessConfig(
    stage_params={
        "s04_tool": {
            # source_id → 노출할 도구 이름 리스트 ([] 면 비활성, 키 없으면 소스 전체)
            "selected_tools": {"my-tools": ["echo"], "mcp-sessions": ["search"]},
            # source_id → list_tools 에 전달할 filter
            "tool_source_filters": {"my-tools": {"tags": ["safe"]}},
        }
    }
)
```

---

## 🔌 MCP — 양방향, 처음부터 끝까지

하네스 워크플로우는 **닫힌 루프** 를 그린다 — UI 에서 설정한 워크플로우가 도구로 말려서 발행되고, 다시 (같은 또는 다른) 하네스의 s04 카탈로그로 흡수돼 LLM 이 호출 가능한 도구가 된다.

```
   ┌────────────────────────────────────────────────────────────────────────────┐
   │                                                                            │
   │    하네스 UI · HarnessConfig                                               │
   │           │                                                                │
   │           │  ① compile_workflow()  — 워크플로우를 도구로 "말기"           │
   │           ▼                                                                │
   │    WheelBuildResult                                                        │
   │     · xgen_gallery_<name>-<ver>-py3-none-any.whl                           │
   │     · CLI 3종 (run / info / serve-mcp)                                     │
   │     · NOMGraph 단일 IR                                                     │
   │           │                                                                │
   │           │  ② MCPStdioVerifier — 격리 핸드셰이크 + SHA-256 지문            │
   │           ▼                                                                │
   │    POST /api/harness/compile/publish    ─────────►   PublishTargetRegistry│
   │                                                       ├─ mcp-station       │
   │                                                       ├─ xgen-gallery      │
   │                                                       ├─ 사내 PyPI / 폐쇄망  │
   │                                                       └─ Claude Desktop    │
   │                                                              │             │
   │                                                              ▼             │
   │                                                       ③ 발행처에서        │
   │                                                       wheel install +      │
   │                                                       MCP stdio 기동       │
   │                                                              │             │
   │           ┌─────────── ④ 다시 받아오기 (re-ingest) ──────────┘             │
   │           │                                                                │
   │           ▼                                                                │
   │    ToolSource 로 흡수                                                      │
   │     ├─ MCPSessionToolSource(station_url)   — mcp-station 세션              │
   │     ├─ discover_galleries()                — entry_points 자동 스캔        │
   │     └─ Claude Desktop / Cursor             — 외부 호스트가 직접 호출       │
   │           │                                                                │
   │           ▼                                                                │
   │    ⑤ s04_tool 카탈로그 합류 → Auto 모드 LLM 이 호출                        │
   │                                                                            │
   └────────────────────────────────────────────────────────────────────────────┘
```

| 단계 | 무엇이 | 어디서 | 핵심 산출 |
|---|---|---|---|
| ① 말기 (wrap) | `HarnessConfig` + 캔버스 스냅샷 → wheel | 엔진 `compile_workflow()` | `WheelBuildResult` (wheel/sdist/dist_name/package_name + NOMGraph) |
| ② 검증 (verify) | wheel 격리 기동 → `initialize`/`tools/list` 왕복 | 엔진 `MCPStdioVerifier` | `VerifyResult` (`payload_hash` 발행 감사용 지문) |
| ③ 올리기 (publish) | wheel + 메타 → 발행처 등록 | 이식측 `POST /api/harness/compile/publish` | PublishTargetRegistry 항목 (mcp-station / gallery / Claude Desktop / 사내 PyPI) |
| ④ 받아오기 (ingest) | 발행된 도구를 다시 카탈로그로 흡수 | `MCPSessionToolSource` / `discover_galleries()` | `tool_definitions` 추가 |
| ⑤ 호출 (call) | LLM 이 도구로 사용 | `s04_tool` → `s07_act` (s00 dispatcher 경유) | `ToolCallEvent` / `ToolResultEvent` |

각 단계를 자세히:

### A. 다른 MCP 서버를 하네스 안에서 쓰기

이미 떠 있는 MCP 서버 (Claude Desktop, mcp-station, npx 로 띄운 서버 등) 의 도구를 그대로 카탈로그에 합칩니다. **이식측 (xgen-workflow) 가 `MCPSessionToolSource` 를 등록**하면 하네스는 평범한 ToolSource 로 받아옵니다.

```python
# 이식측 (예: xgen-workflow) 에서 한 번만 호출
from xgen_harness import register_tool_source
from harness_bridge.tool_sources.mcp_sessions import MCPSessionToolSource

register_tool_source(MCPSessionToolSource(station_url="http://mcp-station:8030"))
```

```python
# 하네스 사용자 코드
config = HarnessConfig(
    harness_mode="autonomous",
    stage_params={
        "s04_tool": {
            "selected_tools": {"mcp-sessions": ["playwright_navigate", "playwright_screenshot"]},
        }
    },
)
```

### B. 하네스 워크플로우를 MCP stdio 서버로 내보내기

워크플로우 하나를 **`pip install` 가능한 wheel** 로 컴파일. 그 wheel 이 그대로 **MCP stdio 서버** — Claude Desktop · Cursor · 다른 하네스 어디서든 도구로 호출.

```python
from xgen_harness import HarnessConfig, compile_workflow

result = compile_workflow(
    harness_config=HarnessConfig(
        harness_mode="autonomous",
        system_prompt="너는 KRRA 규정 검색 전문가야.",
        capabilities=["retrieval.rag_query"],
        stage_params={"s06_context": {"rag_collections": ["krra_2024"]}},
    ),
    workflow_data={"workflow_type": "harness", "nodes": [], "edges": []},  # 캔버스 스냅샷 (옵션)
    gallery_name="krra_search",
    gallery_version="0.1.0",
    out_dir="./dist",
)

print(result.wheel_path)     # ./dist/xgen_gallery_krra_search-0.1.0-py3-none-any.whl
print(result.dist_name)      # xgen-gallery-krra_search
print(result.package_name)   # xgen_gallery_krra_search
```

설치 후 CLI 3 종이 자동 주입됩니다:

```bash
pip install ./dist/xgen_gallery_krra_search-0.1.0-py3-none-any.whl
xgen-gallery-krra_search run --input "마사회 운영 규정 알려줘"     # 일회성 호출
xgen-gallery-krra_search info                                        # 갤러리 메타
xgen-gallery-krra_search serve-mcp                                   # MCP stdio 서버로 기동
```

**Claude Desktop 에 등록** (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "krra-search": {
      "command": "xgen-gallery-krra_search",
      "args": ["serve-mcp"]
    }
  }
}
```

→ Claude Desktop 이 이 하네스 워크플로우를 `run_workflow(input, overrides)` 도구로 호출.

### C. 발행 전 격리 검증 (Sandbox Verifier)

서버에 등록하기 **전** 에 `initialize + tools/list` 왕복 + POSIX rlimit + SHA-256 재현성 해시로 게이팅.

```python
from xgen_harness import MCPStdioVerifier, SandboxLimits

v = MCPStdioVerifier()
result = v.verify(
    command=["xgen-gallery-krra_search", "serve-mcp"],
    timeout_sec=10.0,
    limits=SandboxLimits(
        cpu_seconds=15,
        address_space_mb=1024,
        max_open_files=128,
        max_file_size_mb=16,
        no_core_dump=True,
    ),
)

assert result.ok, result.error
print(result.tool_count, result.payload_hash[:12], result.handshake_ms)
```

`VerifyResult` 에는 `ok / tool_count / tools / handshake_ms / tools_ms / payload_hash / stderr_tail / applied_limits / timed_out / error` 가 모두 담깁니다. **같은 wheel → 같은 `payload_hash`** 라 발행 감사용 지문으로 활용.

---

## 🛠 Compile — wheel 한 장에 무엇이 들어가나

`compile_workflow()` 는 멀티 스테이지로 산출물을 만듭니다. 각 단계는 독립적으로 호출 가능합니다.

```
HarnessConfig + workflow_data
        │
        ▼
 ① WorkflowSnapshot.from_config()       — 직렬화 가능한 스냅샷 (SNAPSHOT_VERSION 박제)
        │
        ▼
 ② scan_placeholders(snapshot)          — ${VAR} 발견 → ExternalInputSpec 자동 등록
        │
        ▼
 ③ resolve_dependencies(snapshot)       — DependencyRule 레지스트리 → pip 의존성 산출
        │
        ▼
 ④ build_wheel(snapshot)                — _write_source_tree → python -m build
        │
        ▼
 WheelBuildResult { wheel_path, sdist_path, source_dir, dist_name, package_name, snapshot }
```

### 외부 입력 (`external_inputs`) — wheel 이 런타임에 요구하는 값

```python
from xgen_harness import scan_placeholders, ExternalInputSpec, InputType

config.external_inputs = {
    "QDRANT_URL": ExternalInputSpec(
        name="QDRANT_URL", type=InputType.URL, required=True,
        description="RAG 컬렉션이 살아있는 Qdrant 인스턴스",
    ),
    "OPENAI_API_KEY": ExternalInputSpec(
        name="OPENAI_API_KEY", type=InputType.SECRET, required=True,
    ),
}
```

UI 가 이 선언을 보고 배포 전 입력 폼 자동 렌더. `${QDRANT_URL}` 같은 placeholder 가 system_prompt / capability_params / stage_params 에 박혀있으면 컴파일러가 **자동으로** ExternalInputSpec 후보를 등록합니다.

### 의존성 해석 — `DependencyRule` 레지스트리

```python
from xgen_harness import register_dependency_rule, DependencyRule

register_dependency_rule(DependencyRule(
    name="my-tool-deps",
    matcher=lambda snap: any(
        sid == "my-tools" for sid in snap.harness_config.stage_params
            .get("s04_tool", {}).get("selected_tools", {}).keys()
    ),
    requirements=["my-tool-package>=1.2"],
))
```

Snapshot 내용에 따라 wheel 의 `install_requires` 가 자동 산출 — 외부 도구 / capability / provider 가 자기 의존성을 선언할 때 이 한 줄이면 끝.

### 갤러리 자동 발견 — `discover_galleries()`

```python
from xgen_harness import discover_galleries, get_gallery

for g in discover_galleries():       # entry_points "xgen_harness.galleries" 자동 스캔
    print(g.dist_name, g.version, g.entry_module)

g = get_gallery("xgen-gallery-krra_search")
print(g.snapshot.harness_config["system_prompt"])
```

### NOM IR — wheel + MCP 카탈로그 + 격리 페이로드 단일 그래프

Stage / Strategy / Tool / MCP 서버 / 외부 플러그인 노드를 **하나의 IR (`NOMGraph`)** 로 표현하고, **세 가지 변환** 으로 wheel · MCP · Sandbox 모두 커버:

```python
from xgen_harness import NOMGraph, NOMNode, NOMKind, NOMParam, compile_nom_graph

graph = NOMGraph(nodes=[
    NOMNode(
        id="x.tools.search", kind=NOMKind.TOOL,
        description="웹 검색", entry="my_pkg.tools:search",
        inputs=[NOMParam(name="q", type="string", required=True)],
    ),
])

graph.to_mcp_schema()                      # → MCP tools/list 응답 그대로
graph.to_sandbox_payload("x.tools.search", {"q": "hello"})   # → 격리 실행 payload
compile_nom_graph(graph, gallery_name="my_tools", gallery_version="0.1.0")  # → wheel
```

런타임에 LLM 이 만든 합성 도구를 그대로 wheel 로:

```python
from xgen_harness.tools.synthesis import synthesize_and_register, synthesized_tools_as_nom_graph

# synthesize_and_register(...) 로 검증·등록한 뒤
graph = synthesized_tools_as_nom_graph([slugify, camelcase, redact_pii])
result = compile_nom_graph(graph, gallery_name="my_synth_tools", gallery_version="0.1.0")
```

엔진 현 상태도 그대로 NOM 으로 덤프 가능 — 디버깅 / 갤러리 업로드 / 샌드박스 복원에 재사용:

```python
from xgen_harness import snapshot_current_registry_as_nom

nom = snapshot_current_registry_as_nom()
print([n.id for n in nom.nodes])
# ["xgen.stages.s00_harness", ..., "xgen.strategies.s06_context.compact.microcompact",
#  "xgen.orchestrators.iterative", "xgen.providers.anthropic", ...]
```

---

## Strategy — 디폴트 그대로 두고 변형만

```python
config = HarnessConfig(
    strategy_variants={
        "s06_context": [{
            "name":   "microcompact_strict",   # active_strategies 에서 참조할 새 이름
            "base":   "microcompact",          # 복제 원본
            "params": {"threshold": 95},       # 오버라이드
            "label":  "엄격 압축",
        }],
    },
    active_strategies={"s06_context": "microcompact_strict"},
)
```

엔진 코드 0 수정 — variant 레지스트리에서 base impl 클래스를 찾아 새 인스턴스로 생성 + `configure(params)` 주입.

---

## Orchestrator — Auto 모드 5 패턴 + 외부 추가

Auto 모드에서 Planner 가 입력 보고 `Plan.orchestrator_hint` 결정 → loop 가 분기.

| hint | 동작 | 사용 케이스 |
|---|---|---|
| `linear` | 1 회 실행 후 종료 | 단발 Q&A |
| `iterative` (default) | 매 iter Plan replan + 13 Stage 1바퀴 | 멀티턴 도구 |
| `plan_execute` | 첫 Plan 고수, replan 생략, 반복 | 정형 절차 |
| `react` | 엔진 no-op, 이식측 dispatcher 위임 | 외부 ReAct 통합 |
| `dag` | 엔진 no-op, 이식측 DAG runner 위임 | 멀티 에이전트 병렬 |

**외부 패턴 추가** — 한 줄.

```python
from xgen_harness.core.orchestrator_registry import register_orchestrator
register_orchestrator(
    "swarm_v2",
    description="병렬 swarm + 보팅 결합",
    dispatch_key="swarm_v2",
    replan_per_iter=True,
)
```

또는 `entry_points` 그룹 `xgen_harness.orchestrators` 노출 → 자동 합류. `OrchestratorSpec.replan_per_iter` / `max_iterations_override` 가 행동 선언이라 pipeline 은 이름 분기 없이 spec 속성만 읽습니다.

---

## Policy Gate — 선언형 Guard × 4 훅

"submit_result 호출 전 iterative_document_search 를 최소 1회 불러야 한다" 같은 **도구 호출 선행조건 / 입출력 정책 / 예산 제한** 을 **코드 수정 없이 데이터로** 선언.

```python
config = HarnessConfig(
    stage_params={
        "s05_policy": {
            "guards": [
                {"name": "iteration"},                                          # max_iterations 도달 → 종료
                {"name": "cost_budget", "params": {"cost_budget_usd": 5.0}},    # 누적 비용 초과 → 종료
                {"name": "tool_precondition", "params": {                        # 선행조건 강제
                    "rules": [{
                        "tool": "submit_result",
                        "require_prior": [{"tool": "iterative_document_search", "min_count": 1}],
                        "message": "합격 판정 전 QA 기준을 검색하세요.",
                    }]
                }},
                {"name": "hitl"},   # destructiveHint=true 도구는 사용자 승인 후 실행
            ]
        }
    }
)
```

| 훅 | 시점 | 차단 시 동작 |
|---|---|---|
| `pre_main` | 본문 LLM 호출 직전 | `state.policy_block_reason` 설정 + `loop_decision="complete"` |
| `pre_tool` | 도구 실행 직전 (각 pending tool_call) | 해당 도구 제거 + 가짜 `tool_result(is_error=True)` 주입 → LLM 자체 교정 |
| `post_response` | LLM 응답 직후 | `policy_block_reason` 설정 + 종료 |
| `loop_boundary` | 루프 경계 | `policy_block_reason` 설정 + 종료 |

**내장 6 Guard:** `iteration` · `token_budget` · `cost_budget` · `content` · `tool_precondition` · `hitl` (HITL 승인 모달 — `ApprovalRequiredEvent` / `ApprovalDecidedEvent` 송수신).

**외부 Guard 추가:**

```toml
[project.entry-points."xgen_harness.guards"]
my_guard = "my_pkg.guards:MyGuard"
```

`pip install` 한 번이면 UI Guard 드롭다운에 자동 합류.

---

## Capability — 선언적 도구 wiring

```python
config = HarnessConfig(
    capabilities=["retrieval.web_search", "retrieval.rag_query"],
    capability_params={"retrieval.web_search": {"max_results": 10}},
)
```

`s04_tool` 이 `CapabilityRegistry` 에서 자동 매칭 → `tool_definitions` 합류. 외부 패키지가 `entry_points` 로 `CapabilitySpec` 등록하면 자동 발견.

---

## RAG 사용

```python
config = HarnessConfig(
    stage_params={
        "s06_context": {
            "rag_collections":  ["masahoe_v1", "voc_templates"],   # Qdrant 컬렉션 ID
            "rag_top_k":        5,
            "rag_ingestion_mode": "both",     # system_prompt + tool_only 양쪽 주입
        }
    }
)
```

`s06_context` Strategy 별 동작:
- `microcompact` (기본) — 5-Level Claude Code 압축
- `cascade` — L3 → L4 → L5 자동 압력 단계화
- `progressive_3level` — Stage 환경 슬롯 progressive disclosure
- `none` — 패스스루

---

## 외부 패키지가 끼워넣는 11 지점

엔진 코드 수정 0. `pyproject.toml` 한 줄로 합류.

| # | 지점 | entry_points 그룹 | 등록 함수 |
|---|---|---|---|
| 1 | **Stage** | `xgen_harness.stages` | `register_stage()` |
| 2 | **Strategy** | `xgen_harness.strategies` | (registry 자동 검색) |
| 3 | **Provider** | `xgen_harness.providers` | `register_provider()` |
| 4 | **Orchestrator** | `xgen_harness.orchestrators` | `register_orchestrator()` |
| 5 | **ToolSource** | `xgen_harness.tool_sources` | `register_tool_source()` |
| 6 | **Capability** | `xgen_harness.capabilities` | `CapabilityRegistry.register()` |
| 7 | **Guard** | `xgen_harness.guards` | `register_guard()` |
| 8 | **NodeAdapter** | `xgen_harness.node_adapters` | (이식측 wiring) |
| 9 | **OptionSource** | `xgen_harness.option_sources` | (이식측 OptionRegistry) |
| 10 | **SandboxVerifier** | `xgen_harness.sandbox_verifiers` | `register_sandbox_verifier()` |
| 11 | **PublishTarget** | `xgen_harness.publish_targets` (이식측) | mcp-station / gallery / 사내 PyPI |

각 그룹별 빈 본 섹션이 [pyproject.toml](pyproject.toml) 에 있습니다 — 외부 작업자가 어떤 그룹이 valid 인지 한눈에 알 수 있게.

---

## 이식 통합 (xgen-workflow) — 엔진과 호스트의 책임 분리

엔진은 generic primitive. 실서비스 (xgen-documents, xgen-mcp-station, postgres, SSE) 와의 결선은 **호스트 측 (`xgen-workflow/controller/workflow/endpoints/`) 가 소유**.

| 엔드포인트 | 역할 |
|---|---|
| `GET  /api/harness/stages` | 13 Stage 정의 + 설정 스키마 (icon, fields, behavior) |
| `GET  /api/harness/tool-sources` | 등록된 ToolSource 메타 + `list_tools()` 결과 |
| `GET  /api/harness/options/{source}` | 동적 옵션 (mcp-sessions / rag-collections / providers …) |
| `POST /api/harness/execute/stream` | SSE 실행 (하네스 전용 — 레거시 워크플로우와 분리) |
| `POST /api/harness/compile` | wheel 컴파일 + 바이너리 다운로드 |
| `POST /api/harness/compile/publish` | compile + PublishTarget 발행 (mcp-station / gallery) |
| `POST /api/harness/dag/execute/stream` | 멀티 하네스 DAG orchestration |

**호스트 노드 주입** — 엔진은 xgen 노드 스키마를 모르고, 호스트가 Protocol 으로 주입:

```python
from xgen_harness import register_xgen_node_resolver, XgenNodeResolver

class MyResolver:
    def list_nodes(self) -> list[dict]: ...
    def get_node_tool(self, node_id: str): ...

register_xgen_node_resolver(MyResolver())
```

엔진 자신은 라이브러리에서 제공하는 작은 FastAPI 라우터도 가지고 있어 단독으로 띄울 수 있습니다 (옵션):

```python
from fastapi import FastAPI
from xgen_harness.api.router import harness_router

app = FastAPI()
app.include_router(harness_router, prefix="/api/harness")
```

→ `GET /stages`, `GET /tool-sources`, `POST /execute`, `POST /orchestrate`, `WS /ws/{session_id}`.

---

## 공식 Public API

```python
from xgen_harness import (
    # Core
    Pipeline, PipelineState, TokenUsage, HarnessConfig,
    ALL_STAGES, REQUIRED_STAGES,
    Stage, StageDescription, StrategyInfo,
    # Builder & Session
    PipelineBuilder, HarnessSession, SessionManager,
    # Events (17종)
    EventEmitter, HarnessEvent, StageEnterEvent, StageExitEvent,
    MessageEvent, ToolCallEvent, ToolResultEvent, MetricsEvent,
    PlanningEvent, ApprovalRequiredEvent, ApprovalDecidedEvent,
    ErrorEvent, DoneEvent, MissingParamEvent, ServiceLookupEvent,
    CapabilityBindEvent, StageSubstepEvent, RetryEvent,
    # Errors
    HarnessError, ConfigError, ProviderError, ToolError,
    PipelineAbortError, RateLimitError, OverloadError,
    ContextOverflowError, ToolTimeoutError, MCPConnectionError,
    ValidationError, ErrorCategory,
    # Tools
    ToolSource, register_tool_source, get_tool_sources,
    ToolPackageSpec, GalleryTool, load_tool_package, discover_gallery_tools,
    # Policy Gate
    Guard, GuardResult, GuardChain, HookPoint, HookContext,
    available_guards, register_guard, describe_guards, build_guard_chain,
    # Orchestrator
    DAGOrchestrator, AgentNode, DAGEdge, DAGResult, DAGCycleError,
    MultiAgentExecutor,
    # Capability
    CapabilitySpec, CapabilityMatch, ParamSpec, ProviderKind,
    CapabilityRegistry, get_default_registry, set_default_registry,
    CapabilityMatcher, MatchStrategy,
    materialize_capabilities, merge_into_state, MaterializationReport,
    ParameterResolver, ResolveResult,
    # Compile
    compile, compile_workflow, build_wheel, WheelBuildResult,
    WorkflowSnapshot, SNAPSHOT_VERSION, load_snapshot,
    ExternalInputSpec, InputType, scan_placeholders, merge_scanned,
    collect_runtime_values, MissingExternalInputError,
    DependencyResolver, resolve_dependencies, register_dependency_rule, DependencyRule,
    serve_mcp, run_mcp_blocking, MCPNotInstalledError,
    InstalledGallery, discover_galleries, get_gallery,
    # Sandbox
    Sandbox, SandboxLimits, SandboxResult, run_sandboxed,
    SandboxVerifier, VerifyResult, MCPStdioVerifier,
    register_sandbox_verifier, get_sandbox_verifier, list_sandbox_verifiers,
    bootstrap_default_sandbox_verifiers, verify_mcp_stdio,
    # NOM IR
    NOMKind, NOMParam, NOMOutput, NOMNode, NOMGraph,
    snapshot_current_registry_as_nom, compile_nom_graph,
    # Host integration
    register_xgen_node_resolver, get_xgen_node_resolver, XgenNodeResolver,
    # Catalog & planning
    get_catalog, HarnessPlanner, HarnessPlan, ensure_provider,
    # Presets (legacy compat)
    PRESETS, Preset, get_preset, apply_preset, list_presets,
)
```

---

## 환경변수 치트시트

| 변수 | 의미 | 기본값 |
|---|---|---|
| `XGEN_HARNESS_DEFAULT_PROVIDER` | 기본 provider 강제 | (env → openai → anthropic → registry[0]) |
| `XGEN_HARNESS_<PROVIDER>_DEFAULT_MODEL` | 해당 provider 기본 모델 override | `PROVIDER_DEFAULT_MODEL` |
| `XGEN_HARNESS_DEFAULT_CONTEXT_LIMIT` | 등록 안 된 provider 의 컨텍스트 한도 (chars) | 500_000 |
| `XGEN_HARNESS_API_KEY_FILE_DIR` | API key 파일 폴백 디렉토리 | `/app/config` |
| `XGEN_HARNESS_PRELOAD_MANIFEST` | 시작 시 자동 로드할 LocalManifest 경로 (PATH-style) | (없음) |
| `HARNESS_SANDBOX_POLICY` | 발행 전 검증 정책 (`strict` / `advisory` / `off`) | `strict` (이식측) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` | provider API 키 | (없음) |

---

## 진화 요약

큰 흐름은 **4 페이즈** 로 정리됩니다.

```
Phase 1  REAL HARNESS  ─ 13 Stage 골조 / s00_harness 통제탑       (v0.12 ~ v0.16)
Phase 2  발행·격리·정책 ─ MCP wheel / Sandbox / NOM / Policy Gate   (v0.17 ~ v0.21)
Phase 3  독립성 정리   ─ xgen 특화 호스트 이관 / ToolSource 통합    (v0.22 ~ v0.25)
Phase 4  라이브 검증 패치 ─ production 결함 일괄 fix                (v0.26.x)
```

### Phase 1 — REAL HARNESS 골조 (v0.12 ~ v0.16)

캔버스를 버리고 "13 Stage = 환경 슬롯" 모델로 전환한 시기. 각 Stage 가 자기 capability·도구·리소스를 LLM 에게 progressive disclose 하고, `s00_harness` 가 본문 LLM 호출의 single owner 가 됨.

| 버전 | 핵심 |
|---|---|
| `v0.12.0` | REAL HARNESS Phase 1 — `s00_harness` Planner + 13 Stage 디렉토리화 |
| `v0.13.0` | Phase 2 — 단일 Provider + iterative planning |
| `v0.14.0` | `s00_harness` 통제탑 승격 — 본문 LLM 호출 owner + 3 모드 (`off`/`selected`/`autonomous`) |
| `v0.15.x` | 재귀적 자율주행 — `orchestrator_hint` + OrchestratorRegistry + fs_scanner 자동 발견 |
| `v0.16.x` | 자가증식 골조 — Sandbox / NOM / NodePlugin / ToolSynthesis + Pipeline Role |

### Phase 2 — 발행·격리·정책 (v0.17 ~ v0.21)

워크플로우를 wheel 로 컴파일해서 MCP stdio 서버로 배포하는 양방향 통합과, 발행 전 격리 검증, 선언형 Guard 정책 시스템이 자리잡은 시기. Stage / Tool / MCP / Plugin 을 단일 IR (NOMGraph) 로 통합.

| 버전 | 핵심 |
|---|---|
| `v0.17.0` | **Policy Gate** — 선언형 Guard × 4 훅 포인트 + `entry_points` 외부 Guard 합류 |
| `v0.18.0` | **양방향 MCP** — 하네스 → wheel → MCP stdio 발행 / 마켓·Station → s04 카탈로그 흡수 |
| `v0.20.0` | **Sandbox Verifier** — `MCPStdioVerifier` + POSIX rlimit + SHA-256 재현성 해시 |
| `v0.21.0` | **NOM IR 허브** — `to_mcp_schema` / `to_sandbox_payload` / `to_wheel_snapshot` 3 변환 단일 그래프로 |

### Phase 3 — 독립성 정리 (v0.22 ~ v0.25)

엔진이 xgen 서비스(workflow / mcp-station / documents) 와 직접 결선돼 있던 잔재를 모두 호스트(이식측)로 이관. 도구 공급 채널을 4 갈래 → ToolSource 단일 Protocol 로 통합.

| 버전 | 핵심 |
|---|---|
| `v0.22.0` | **엔진 독립성 완결** — xgen 특화 코드 호스트 이관 + `ExternalNodeRef` Protocol + `REQUIRED_STAGES` 레지스트리 |
| `v0.23.0` | **MCP Tool Annotations 1급화** — `readOnlyHint` / `destructiveHint` / `idempotentHint` / `openWorldHint` Tool ABC 1급 속성 |
| `v0.24.0` | **HITL Guard + Agent-controlled Compact Tool** — `destructiveHint=true` 도구 사용자 승인 모달 |
| `v0.25.0` | **ToolSource 단일 공급 채널** — s04 의 `mcp_sessions` / `custom_tools` / `node_tags` / `cli_skills` 4 하드코딩 제거 + `/tool-sources` 엔드포인트 |
| `v0.25.3` | **HarnessConfig 헬퍼** — `is_autonomous()` / `is_selected()` / `is_off()` 도메인 캡슐화 (리터럴 `== "autonomous"` 비교 추적 불필요) |

### Phase 4 — 라이브 검증 패치 사이클 (v0.26.x)

production 라이브 운영에서 한 번 검증할 때마다 한두 개씩 드러나는 결함을 즉시 메우는 패치 사이클. 보고서 + 라이브 재검증 + 패치 → 다음 검증에서 또 결함 → 패치 의 빠른 루프.

| 버전 | 무엇이 깨져있었나 | 어떻게 고쳤나 |
|---|---|---|
| `v0.26.0` | UI 클릭이 LLM 환경에 안 박히는 4 stage_param + label-only strategy 1개 + EventQueue 백프레셔 부재 | Dead UI 4건 제거 (`s01.provider` / `s02.memory_source` / `s06.files` / `s09.max_iterations`), label-only 1건 제거 (`s03.simple`) + 분기 신규 2건 (`s04.none` / `s10.noop`), queue 1000→8000 + drop 카운터 |
| `v0.26.1` | v0.26.0 에서 dead 로 제거한 `s06_context.files` 가 frontend UI 엔 살아있어서 사용자 클릭이 무효화 | 엔진에 진짜 wiring 추가 (`metadata_filter.file_name` 자동 라우팅) → 필드 부활 |
| `v0.26.2` | OpenAI strict schema 가 `properties` 없는 도구를 거부 → HTTP 400 (SynthesizedToolSource 자동 등록 도구 영향) | `providers/openai.py:_convert_tools` 가 `type=object` + `properties` 누락 시 `{}` 자동 보강 |
| `v0.26.3` | `s10_save` 가 dict 컬럼 (`input_data` / `output_data`) 으로 보내지만 실 DB 는 text (`input_text` / `output_text`) — 매 실행 `inserted_id=None` 으로 graceful 종료, `/executions` 빈 채 | record 컬럼명을 실 schema 에 맞춰 직렬화 (5K / 50K 자 truncate) |
| `v0.26.4` | OpenAI batch transport (`stream=False`) 가 STOP 이벤트 `.text` 로 응답 한 번에 yield, 엔진 STOP 핸들러는 `output_tokens` 만 처리 → 응답 텍스트 사라짐 | `core/llm_call.py:_single_call` STOP 핸들러에 `event.text` 처리 + `MessageEvent` emit 추가 |
| `v0.26.5` | Anthropic `thinking` 켤 때 `thinking_budget > max_tokens` 이면 무조건 HTTP 400 — engine default 도 동일 함정 (`max_tokens=8192 < thinking_budget=10000`) | thinking 활성 시 자동 보정 `max_tokens = budget_tokens + 1024` (사용자 설정 무시 아니라 안전 보장) |
| `v0.26.6` | DAG orchestrator 가 `PipelineState(tool_definitions=...)` 로 init — v0.11.22 도메인 그룹화 후 `dag.py:255` 동기화 누락 → 모든 DAG 노드 100% TypeError | init kwarg 제거, instance 생성 후 `state.tool_definitions = ...` setter 로 박음 |
| **`v0.26.7`** | `max_iter=1` + 도구 활성 시 LLM 이 첫 iter 에서 도구만 호출, 답변 텍스트 만들 두 번째 iter 가 없어 `output_length=0` 빈 응답 (default `max_iter=10` 환경에선 안 드러남) | Phase B 후 빈 응답 + 도구 실행 ≥ 1 이면 `tool_definitions=[]` 로 1회 보강 `main_call` (직후 `tool_definitions` 원복 → 다음 iteration / 외부 코드 영향 0) |

이전 변경: [CHANGELOG.md](CHANGELOG.md).

---

## 사용자 매뉴얼 (UI 사용자용)

엔진을 직접 쓰지 않고 XGEN 하네스 페이지 (`http://xgen.x2bee.com/harness`) 사용자라면 → [docs/confluence/harness-user-manual.md](https://github.com/jinsoo96/xgen-harness-executor/blob/main/docs/confluence/harness-user-manual.md)

---

## 라이선스

MIT
