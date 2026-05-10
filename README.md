<div align="center">

# xgen-harness

LLM 에이전트 실행기입니다. 워크플로우를 코드로 짜지 않고 `HarnessConfig` 한 객체로 선언하면, 10개 Stage 가 정해진 순서로 실행합니다.

[![PyPI](https://img.shields.io/pypi/v/xgen-harness?color=blue&label=PyPI)](https://pypi.org/project/xgen-harness/)
[![Python](https://img.shields.io/pypi/pyversions/xgen-harness)](https://pypi.org/project/xgen-harness/)
[![License](https://img.shields.io/pypi/l/xgen-harness)](https://pypi.org/project/xgen-harness/)

```bash
pip install xgen-harness          # 코어
pip install 'xgen-harness[mcp]'   # MCP stdio 서버 / 마켓 연동
pip install 'xgen-harness[api]'   # FastAPI 라우터 (이식측에서만 필요)
```

</div>

---

## 처음 읽는 분께 — 용어 안내

본문에서 자주 나오는 여섯 단어입니다. 이 정도만 알고 계시면 나머지는 자연스럽게 따라옵니다.

| 용어 | 뜻 | 본문에서 |
|---|---|---|
| **Stage** | 요청 처리의 한 구간 (입력 정규화 / 도구 카탈로그 / LLM 호출 / 응답 평가 ···). 10개가 정해진 순서로 실행 | s00 ~ s09 |
| **Strategy** | 한 Stage 안에서 골라 끼우는 구현체. 예: 루프 결정 = `threshold` / `judge_then_loop` / `always_pass` | `active_strategies` |
| **ToolSource** | LLM 이 부를 도구를 한 곳에서 모으는 통로. MCP 서버·캔버스 노드·파이썬 함수 모두 같은 인터페이스로 들어옴 | `register_tool_source()` |
| **Capability** | "RAG 검색", "웹 검색" 같이 **무슨 능력이 필요하다** 만 선언하면 도구가 자동 매칭 | `capabilities=[...]` |
| **Orchestrator** | 10 Stage 사이클을 어떻게 반복할지의 패턴 (`linear` / `iterative` / `dag` ···) | `orchestrator_hint` |
| **Guard** | "이 도구 부르기 전엔 반드시 검색을 먼저 해라" 같은 규칙을 코드 수정 없이 데이터로 선언 | Policy Gate |

---

## 무엇을 풀어주나

| 항목 | 풀이 방법 |
|---|---|
| 여러 LLM 제공자 | provider 레지스트리 + entry_points. anthropic / openai / google / bedrock / vllm 빌트인 |
| 여러 종류 도구 | `ToolSource` 인터페이스 한 갈래로 통합 — MCP 세션 / 캔버스 노드 / 로컬 함수 / 합성 도구 |
| 여러 실행 패턴 | Orchestrator 레지스트리 — linear / iterative / react / plan_execute / dag |
| 워크플로우 배포 | `compile_workflow()` 가 `pip install` 가능한 wheel 로 만들어 MCP stdio 서버로 기동 |
| 도구 호출 정책·예산 | Policy Gate — 선언형 Guard 체인 × 4 훅 포인트 |

---

## 어떻게 돌아가는가

요청이 들어오면 10개 Stage 가 **초기화 → 에이전트 루프 → 종료** 세 그룹으로 나뉘어 순차 실행됩니다. 각 Stage 는 자기 담당 영역(도구·정책·컨텍스트·예산) 만 LLM 에게 보여주고 다음 Stage 로 `PipelineState` 객체를 넘깁니다.

```
[ ingress · 1회 ]                  [ agent loop · max_iterations 회 ]   [ egress · 1회 ]

  s00 ─ s01 ─ s02 ─ s03 ─ s04  ─▶  s05 ─ s06 ─ s07 ─ s08  ─┐  ─▶  s09
  ──────────────────────────────       ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ │
  LLM 핸들 owner / 입력 / 히스토리 /     정책 / 컨텍스트 /        │     최종 응답 + 메트릭스
  프롬프트·citation / 도구 카탈로그      도구실행 / 루프결정+judge │     + (선택) DB 저장
                                  ◀─── orchestrator hint ───┘
                                       (linear · iterative · plan_execute · react · dag)
```

> **v1.0 통합 (2026-04-29~30)**: 이전 13 Stage 에서 4개 흡수 후 10 Stage 로 정리.
> `s05_strategy` 분해 (CoT/ReAct → s03 / capability matcher → s04 / intent_routing → s06) ·
> `s08_judge` → `s08_decide.judge_then_loop` strategy · `s10_save` → `s09_finalize.persist` strategy ·
> `s12_publish` 빈 슬롯 제거. 외부 swap-in 슬롯은 strategy 격하로 보존.

### 알아두면 좋은 4 가지

1. **Stage 는 "단계"가 아니라 "담당 영역"입니다.** 각 Stage 가 자기 도구·자원·정책을 LLM 에게 점진적으로 보여주고, Auto 모드에서는 Planner LLM 이 그 안에서 골라 씁니다.
2. **도구는 한 통로로 들어옵니다.** MCP 세션·캔버스 노드·파이썬 함수·합성 도구가 전부 `ToolSource` 인터페이스를 거쳐 s04 에서 LLM 카탈로그로 합쳐집니다.
3. **본문 LLM 호출의 책임은 s00 하나에 있습니다.** provider·model·max_tokens·전송 방식(streaming/batch) 결정이 `s00_harness` 한 곳에 모이고, s07_act 같은 다른 Stage 는 s00 의 dispatcher 를 거쳐 호출합니다.
4. **워크플로우 자체를 도구로 발행할 수 있습니다.** `compile_workflow()` 호출 한 번에 `pip install` 가능한 wheel · MCP stdio 서버 · 격리 검증 페이로드(NOMGraph) 가 함께 만들어집니다.

### 한 사이클의 데이터 흐름

```
HarnessConfig + user_input
    │
    ▼
PipelineState  ◀──────────  EventEmitter (SSE 스트림)
    │
    │   ── ingress ──
    ├─ s00  LLM 핸들 owner / transport(streaming|batch) 선정 + Planner (Auto 모드)
    ├─ s01  사용자 입력 정규화 · multimodal 추출
    ├─ s02  같은 interaction 의 이전 turn 로드 (선택: embedding_search)
    ├─ s03  system_prompt 주입 + citation + thinking_mode (CoT/ReAct/none, 구 s05_strategy 흡수)
    ├─ s04  ToolSource → tool_definitions 합성 + Capability 자동 발견 (구 s05_strategy 흡수)
    │
    │   ── agent loop (orchestrator_hint 가 결정한 횟수만큼) ──
    ├─ s05_policy   (옵션) Guard 체인 — pre_main / pre_tool / post_response / loop_boundary
    ├─ s06          RAG · 온톨로지 · DB → 컨텍스트 주입 + 압축 (microcompact / cascade / …)
    │                + Intent Routing (구 s05_strategy 흡수)
    ├─ s07_act      tool_use multi-turn 도구 실행 (s00 본문 호출 결과 기반)
    ├─ s08_decide   루프 계속 / 종료 결정 + (선택) judge_then_loop strategy 로 응답 평가
    │                ※ 구 s08_judge 가 strategy 로 격하
    │
    │   ── egress ──
    └─ s09_finalize 최종 응답 + MetricsEvent + (선택) persist strategy 로 DB 기록
                    ※ 구 s10_save / s11_finalize 통합
```

`HarnessConfig.harness_mode` 가 이 사이클의 자율도를 결정합니다 (`off` / `selected` / `autonomous`). 자세한 내용은 다음 섹션입니다.

---

## 3 모드 — 자유도 vs 안정성

| 모드 | `harness_mode` | 동작 | 언제 쓰나 |
|---|---|---|---|
| **Off** (기본) | `"off"` | 10 Stage 정해진 순서, Plan 안 만듦, 본문 LLM 1회 | 빠른 단발 Q&A |
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
        "s06_context": "microcompact",       # RAG 압축 전략 핀
        "s08_decide":  "judge_then_loop",    # 응답 품질 평가 활성 (구 s08_judge stage 격하)
    },
)
```

---

## 10 Stage 표

각 Stage 는 자기 담당 영역(capability / 도구 / 리소스)만 LLM 에게 점진적으로 보여주고, Auto 모드에서는 Planner LLM 이 그 중에서 골라 씁니다.

### 초기화 그룹 (ingress · 1 회)

| # | Stage | 책임 | 주요 `stage_params` | Strategy |
|---|---|---|---|---|
| 0 | **s00_harness** | LLM 핸들 owner + 본문호출 dispatcher (모드별 책임) | `strategy`(transport), `max_tokens`, `thinking_enabled`, `thinking_budget` | `streaming`* / `batch` · Artifacts: `default` / `multi_agent` |
| 1 | **s01_input** ✱ | 사용자 입력 정규화, multimodal 추출 | (없음 — top-level config.user_input) | `default`* / `with_classification` |
| 2 | **s02_history** | 같은 interaction 이전 turn 로드 | `max_history`, `memory_top_k`, `memory_score_threshold` | `default`* / `embedding_search` / `none` |
| 3 | **s03_prompt** | system_prompt 주입 + citation + thinking_mode (CoT/ReAct, 구 s05_strategy 흡수) | `system_prompt`, `include_rules`, `citation_mode`, `thinking_mode`, `identity_template`, `rules_template` | `section_priority`* / `cot_planner` / `react` / `none` |
| 4 | **s04_tool** | LLM 노출 도구 카탈로그 — **ToolSource 단일 채널** + Capability 자동 발견 (구 s05_strategy 흡수) | `selected_tools`, `tool_source_filters`, `rag_collections`, `force_tool_use`, `capability_top_k`, `capability_min_score` | `progressive_3level`* / `eager_load` / `capability_auto` / `none` |

### 에이전트 루프 그룹 (loop · `max_iterations` 회)

| # | Stage | 책임 | 주요 `stage_params` | Strategy |
|---|---|---|---|---|
| 5 | **s05_policy** | Guard 체인 × 4 훅 포인트 (`guards` 비면 자동 bypass) | `guards: [{name, params}]` | — (Guard 합성, Strategy 카드 0) |
| 6 | **s06_context** | RAG / 온톨로지 / DB → 컨텍스트 주입 + 압축 + Intent Routing (구 s05_strategy 흡수) | `rag_collections`, `folders`, `files`, `db_connections`, `ontology_collections`, `score_threshold`, `reranker`, `metadata_filter`, `rag_pd_mode`, `rag_ingestion_mode`, `intent_rules`, cascade/L3/L4/L5 임계 | `token_budget`* / `sliding_window` / `microcompact` / `context_collapse_overlay` / `autocompact_llm` / `cascade` |
| 7 | **s07_act** | 도구 실행 (read 병렬 / write 직렬) | `timeout`, `result_budget`, `tool_result_preview_threshold`, `tool_result_preview_size` | `default`* / `parallel_read` / `strict_no_error` |
| 8 | **s08_decide** ✱ | 루프 계속 / 종료 결정 + (선택) 응답 품질 평가 (구 s08_judge 흡수) | `max_retries`, `judge_enabled`, `judge_threshold`, `criteria`, `evaluation_strategy`, `evaluation_prompt_template`, `evaluation_system_prompt` | `threshold`* / `judge_then_loop` / `always_pass` |

### 종료 그룹 (egress · 1 회)

| # | Stage | 책임 | 주요 `stage_params` | Strategy |
|---|---|---|---|---|
| 9 | **s09_finalize** ✱ | 최종 응답 + MetricsEvent + (선택) DB 기록 (구 s10_save 흡수) | `output_format` (text/markdown/json), `save_enabled`, `table_name`, `input_text_cap`, `output_text_cap` | `default`* / `persist` / `noop` |

`✱` = `REQUIRED_STAGES` (비활성화 불가) · `*` = 기본 strategy

> v1.0 BREAKING (2026-04-29~30): `s05_strategy` 분해 / `s08_judge` → strategy 격하 / `s10_save` → strategy 격하 / `s12_publish` 제거 / 번호 시프트 (`s09_decide`→`s08_decide`, `s11_finalize`→`s09_finalize`). 외부 swap-in 슬롯은 strategy 로 보존.

> **최근 변경 — 한눈에**:
>
> - **v1.0.9 (2026-05-01) — Plugin Registration API 정리 + s06 god-class 분해 + runtime_defaults 인프라**:
>   - 30+ `register_*` / `get_*` / `list_*` 함수 **top-level export** (entry_points 16 그룹과 1:1 매핑). 외부 plugin 이 깊은 모듈 경로 알 필요 없이 `from xgen_harness import register_phase` 한 줄로.
>   - `core/runtime_defaults.py` 신설 — 16 안전 바닥(safety floor) 사전 등록. 정책 sentinel(None) → `register_runtime_default()` override 가능.
>   - `s06_context` god-class 분해 — `CascadeCompactionMixin` (L3/L4/L5) + `IntentRoutingMixin` 별도 모듈.
>   - `tools/term_expansion.py` 단일 정의화 — `tools/builtin.py` 의 자체 정의 155 LOC 삭제 후 re-export.
> - **v1.0.0 ~ v1.0.8 (2026-04-29~05-01) — 11→10 stage 고결화 BREAKING + 후속 패치**:
>   - 4 stage 흡수: `s05_strategy` 분해 / `s08_judge` → strategy / `s10_save` → strategy / `s12_publish` 제거.
>   - v1.0.4 Policy Gate emit 본체 + decide_defaults 레지스트리 · v1.0.5 selected 모드 + synthesis 인프라 dead trigger 청소 · v1.0.6 도구 호출 후 합성 답변 미완 함정 fix · v1.0.7 PRE_MAIN/POST_RESPONSE 훅 독립 호출 + judge system prompt + advanced flag · v1.0.8 s02_history.memory_collection dead UI 제거.
> - **v0.26.x 패치 사이클** (production 라이브 검증 → 발견 → 즉시 fix): `s06_context.files` 부활 (v0.26.1, frontend UI 와 wiring 일치) · OpenAI strict schema 호환 (v0.26.2) · `s10_save` 컬럼명 정합 (v0.26.3) · batch transport 응답 누락 fix (v0.26.4) · Anthropic thinking max_tokens 자동 보정 (v0.26.5) · DAG orchestrator init TypeError fix (v0.26.6) · `max_iter=1` + 도구 활성 빈응답 보강 (v0.26.7) · DAG sub-Pipeline `DoneEvent` forward 누수 fix (v0.26.10, 후속 노드 이벤트 누락 차단).
> - **v0.26.0 — Dead UI 정리**: 사용자 클릭이 LLM 환경에 안 박히던 stage_param 정리. `s01_input.provider` (글로벌 ConfigPanel 와 중복) · `s02_history.memory_source` (코드 미read) · `s09_decide.max_iterations` (top-level config 만 작동) 3건 제거. Label-only 라 동일 동작이던 `s03_prompt.simple` strategy 제거. `s04_tool.none` / `s10_save.noop` 는 분기 코드 신규 구현해 진짜 short-circuit. EventEmitter queue 1000→8000 + drop 카운터.
>   - ⚠ `s06_context.files` 도 v0.26.0 에선 같이 제거됐으나, frontend UI 가 잔존해 클릭 무효화되는 문제로 **v0.26.1 에서 부활** (`metadata_filter.file_name` 자동 라우팅).
> - **v0.25.0 — 도구 채널 단일화**: `s04_tool` 의 `mcp_sessions` / `custom_tools` / `node_tags` / `cli_skills` 4 개 stage_param 사라짐. 모든 도구는 이제 **ToolSource 한 채널** 로 (다음 섹션).

---

## 도구 통로 — `ToolSource` 인터페이스

하네스가 LLM 에게 노출하는 모든 도구는 이 한 인터페이스를 거쳐 들어옵니다. MCP 서버든, 캔버스 노드든, 파이썬 함수든 똑같이 취급됩니다.

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

**외부 패키지에서 등록하기** — 외부 패키지의 `pyproject.toml` 에 다음 한 줄을 추가하면 `pip install` 후 자동으로 잡힙니다 (엔진 / 이식측 / 프론트 코드를 건드리지 않습니다):

```toml
[project.entry-points."xgen_harness.tool_sources"]
my_source = "my_pkg:MySource"
```

설치 후 엔진을 재시작하면 s04 UI 에 "My Tools" 박스가 나타나고, LLM 이 호출할 수 있게 됩니다.

**선택 / 필터** — 사용자 설정 단계에서 도구 가시성을 좁힐 수 있습니다:

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

## MCP — 받기 + 내보내기

하네스는 MCP 서버의 도구를 받아쓰는 쪽도 되고, 자기 워크플로우를 MCP stdio 서버로 내보내는 쪽도 됩니다. 한 번 내보낸 워크플로우는 다시 (같은 또는 다른 하네스의) `s04` 카탈로그로 받아와 도구처럼 호출할 수 있습니다.

```
   ┌────────────────────────────────────────────────────────────────────────────┐
   │                                                                            │
   │    하네스 UI · HarnessConfig                                               │
   │           │                                                                │
   │           │  ① compile_workflow()  — 워크플로우를 도구로 패키징           │
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
| ① 패키징 (wrap) | `HarnessConfig` + 캔버스 스냅샷 → wheel | 엔진 `compile_workflow()` | `WheelBuildResult` (wheel/sdist/dist_name/package_name + NOMGraph) |
| ② 검증 (verify) | wheel 격리 기동 → `initialize`/`tools/list` 왕복 | 엔진 `MCPStdioVerifier` | `VerifyResult` (`payload_hash` 발행 감사용 지문) |
| ③ 올리기 (publish) | wheel + 메타 → 발행처 등록 | 이식측 `POST /api/harness/compile/publish` | PublishTargetRegistry 항목 (mcp-station / gallery / Claude Desktop / 사내 PyPI) |
| ④ 받아오기 (ingest) | 발행된 도구를 다시 카탈로그로 흡수 | `MCPSessionToolSource` / `discover_galleries()` | `tool_definitions` 추가 |
| ⑤ 호출 (call) | LLM 이 도구로 사용 | `s04_tool` → `s07_act` (s00 dispatcher 경유) | `ToolCallEvent` / `ToolResultEvent` |

각 단계의 상세입니다:

### A. 다른 MCP 서버를 하네스 안에서 쓰기

이미 떠 있는 MCP 서버 (Claude Desktop, mcp-station, npx 로 띄운 서버 등) 의 도구를 카탈로그에 그대로 합치는 경우입니다. 이식측(xgen-workflow) 에서 `MCPSessionToolSource` 를 등록하면 하네스는 일반 `ToolSource` 와 동일하게 도구를 받아 옵니다.

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

워크플로우 하나를 `pip install` 가능한 wheel 로 컴파일하면, 그 wheel 자체가 MCP stdio 서버로 동작합니다. Claude Desktop, Cursor, 다른 하네스 어디서든 도구로 호출할 수 있습니다.

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

이렇게 등록하면 Claude Desktop 이 이 하네스 워크플로우를 `run_workflow(input, overrides)` 도구로 호출합니다.

### C. 발행 전 격리 검증 (Sandbox Verifier)

서버에 등록하기 전에 `initialize + tools/list` 왕복 / POSIX rlimit / SHA-256 재현성 해시로 게이팅합니다.

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

`VerifyResult` 에는 `ok / tool_count / tools / handshake_ms / tools_ms / payload_hash / stderr_tail / applied_limits / timed_out / error` 가 모두 담깁니다. 같은 wheel 은 항상 같은 `payload_hash` 를 내므로, 발행 감사용 지문으로 사용할 수 있습니다.

---

## Compile — wheel 한 장에 들어가는 내용

`compile_workflow()` 는 여러 단계를 거쳐 산출물을 만듭니다. 각 단계는 독립적으로도 호출할 수 있습니다.

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

UI 는 이 선언을 보고 배포 전에 입력 폼을 자동으로 렌더링합니다. `${QDRANT_URL}` 같은 placeholder 가 `system_prompt` / `capability_params` / `stage_params` 안에 들어 있으면 컴파일러가 `ExternalInputSpec` 후보를 자동 등록합니다.

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

Snapshot 내용에 따라 wheel 의 `install_requires` 가 자동 산출됩니다. 외부 도구 / capability / provider 가 자기 의존성을 선언할 때 이 등록 한 번으로 충분합니다.

### 갤러리 자동 발견 — `discover_galleries()`

```python
from xgen_harness import discover_galleries, get_gallery

for g in discover_galleries():       # entry_points "xgen_harness.galleries" 자동 스캔
    print(g.dist_name, g.version, g.entry_module)

g = get_gallery("xgen-gallery-krra_search")
print(g.snapshot.harness_config["system_prompt"])
```

### NOM IR — wheel + MCP 카탈로그 + 격리 페이로드 단일 그래프

Stage / Strategy / Tool / MCP 서버 / 외부 플러그인 노드를 단일 IR (`NOMGraph`) 로 표현하고, 세 가지 변환으로 wheel · MCP · Sandbox 출력을 모두 다룹니다:

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

런타임에 LLM 이 합성한 도구도 같은 흐름으로 wheel 화할 수 있습니다:

```python
from xgen_harness.tools.synthesis import synthesize_and_register, synthesized_tools_as_nom_graph

# synthesize_and_register(...) 로 검증·등록한 뒤
graph = synthesized_tools_as_nom_graph([slugify, camelcase, redact_pii])
result = compile_nom_graph(graph, gallery_name="my_synth_tools", gallery_version="0.1.0")
```

엔진의 현재 상태도 그대로 NOM 으로 덤프할 수 있습니다. 디버깅 / 갤러리 업로드 / 샌드박스 복원에 재사용됩니다:

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

엔진 코드 변경 없이 동작합니다. variant 레지스트리에서 base 구현 클래스를 찾아 새 인스턴스를 만들고 `configure(params)` 로 오버라이드를 주입합니다.

---

## Orchestrator — Auto 모드 5 패턴 + 외부 추가

Auto 모드에서는 Planner 가 입력을 보고 `Plan.orchestrator_hint` 를 결정하고, 그 값에 따라 루프가 분기합니다.

| hint | 동작 | 사용 케이스 |
|---|---|---|
| `linear` | 1 회 실행 후 종료 | 단발 Q&A |
| `iterative` (default) | 매 iter Plan replan + 13 Stage 1바퀴 | 멀티턴 도구 |
| `plan_execute` | 첫 Plan 고수, replan 생략, 반복 | 정형 절차 |
| `react` | 엔진 no-op, 이식측 dispatcher 위임 | 외부 ReAct 통합 |
| `dag` | 엔진 no-op, 이식측 DAG runner 위임 | 멀티 에이전트 병렬 |

**외부 패턴 추가**:

```python
from xgen_harness.core.orchestrator_registry import register_orchestrator
register_orchestrator(
    "swarm_v2",
    description="병렬 swarm + 보팅 결합",
    dispatch_key="swarm_v2",
    replan_per_iter=True,
)
```

또는 `entry_points` 그룹 `xgen_harness.orchestrators` 에 노출시키면 자동으로 합류됩니다. `OrchestratorSpec.replan_per_iter` / `max_iterations_override` 가 행동을 선언하므로 pipeline 은 이름 분기 없이 spec 속성만 읽습니다.

---

## Policy Gate — 선언형 Guard × 4 훅

"submit_result 호출 전 iterative_document_search 를 최소 1회 불러야 한다" 같은 도구 호출 선행조건 / 입출력 정책 / 예산 제한을 코드 수정 없이 데이터로 선언할 수 있습니다.

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

`pip install` 후에 UI Guard 드롭다운에서 자동으로 노출됩니다.

---

## Capability — 선언적 도구 wiring

```python
config = HarnessConfig(
    capabilities=["retrieval.web_search", "retrieval.rag_query"],
    capability_params={"retrieval.web_search": {"max_results": 10}},
)
```

`s04_tool` 이 `CapabilityRegistry` 에서 매칭한 도구를 `tool_definitions` 에 합칩니다. 외부 패키지가 `entry_points` 로 `CapabilitySpec` 을 등록하면 자동으로 발견됩니다.

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

## 외부 패키지가 끼워넣는 16 지점

엔진 코드를 건드리지 않고 외부 패키지의 `pyproject.toml` 에 entry_points 항목을 추가하는 방식으로 합류시킬 수 있습니다.

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
| 9 | **OptionSource** | `xgen_harness.option_sources` (이식측) | `register_option_source()` |
| 10 | **SandboxVerifier** | `xgen_harness.sandbox_verifiers` | `register_sandbox_verifier()` |
| 11 | **PublishTarget** | `xgen_harness.publish_targets` (이식측) | `register_publish_target()` |
| 12 | **Phase** | `xgen_harness.phases` | `register_phase()` |
| 13 | **NodePlugin** | `xgen_harness.node_plugins` | `register_node_plugin()` |
| 14 | **Gallery / Tools** | `xgen_harness.tools` | wheel install 시 자동 발견 |
| 15 | **FanOutStrategy** | `xgen_harness.fan_out_strategies` | `register_fan_out_strategy()` |
| 16 | **EvaluationCriterion** | `xgen_harness.evaluation_criteria` | `register_evaluation_criterion()` |

추가로 *모델 가격 등록* 도 같은 패턴: `xgen_harness.model_pricing` 그룹 + `register_model_pricing()` (사내 vLLM 모델 등 비용 추적용).

각 그룹별 빈 본 섹션이 [pyproject.toml](pyproject.toml) 에 미리 마련되어 있어, 외부 기여자가 어떤 그룹 이름이 유효한지 한눈에 확인할 수 있습니다.

---

## 이식 통합 (xgen-workflow) — 엔진과 호스트의 책임 분리

엔진은 범용 primitive 만 제공합니다. 실서비스(xgen-documents, xgen-mcp-station, postgres, SSE) 와의 결선은 호스트 측(`xgen-workflow/controller/workflow/endpoints/`) 이 소유합니다.

| 엔드포인트 | 역할 |
|---|---|
| `GET  /api/harness/stages` | 13 Stage 정의 + 설정 스키마 (icon, fields, behavior) |
| `GET  /api/harness/tool-sources` | 등록된 ToolSource 메타 + `list_tools()` 결과 |
| `GET  /api/harness/options/{source}` | 동적 옵션 (mcp-sessions / rag-collections / providers …) |
| `POST /api/harness/execute/stream` | SSE 실행 (하네스 전용 — 레거시 워크플로우와 분리) |
| `POST /api/harness/compile` | wheel 컴파일 + 바이너리 다운로드 |
| `POST /api/harness/compile/publish` | compile + PublishTarget 발행 (mcp-station / gallery) |
| `POST /api/harness/dag/execute/stream` | 멀티 하네스 DAG orchestration |

**호스트 노드 주입** — 엔진은 xgen 노드 스키마를 직접 알지 못하므로, 호스트가 Protocol 을 통해 주입합니다:

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

## 버전 흐름

크게 네 단계로 정리됩니다.

```
Phase 1  13 Stage 구조      ─ Stage 디렉토리화 / s00_harness 본문 LLM     (v0.12 ~ v0.16)
Phase 2  발행·격리·정책      ─ MCP wheel / Sandbox / NOM / Policy Gate    (v0.17 ~ v0.21)
Phase 3  독립성 정리         ─ xgen 특화 호스트 이관 / ToolSource 통합     (v0.22 ~ v0.25)
Phase 4  라이브 검증 패치    ─ production 결함 일괄 fix                   (v0.26.x)
```

### Phase 1 — 13 Stage 구조 (v0.12 ~ v0.16)

캔버스 모델을 걷어내고 "13 Stage = 담당 영역" 구조로 전환한 시기입니다. 각 Stage 가 자기 capability·도구·리소스를 LLM 에게 점진적으로 노출하고, `s00_harness` 가 본문 LLM 호출의 단일 책임자가 되었습니다.

| 버전 | 핵심 |
|---|---|
| `v0.12.0` | 13 Stage 디렉토리화 + `s00_harness` Planner |
| `v0.13.0` | 단일 Provider + iterative planning |
| `v0.14.0` | `s00_harness` 본문 LLM 호출 owner + 3 모드 (`off`/`selected`/`autonomous`) |
| `v0.15.x` | `orchestrator_hint` + OrchestratorRegistry + fs_scanner 자동 발견 |
| `v0.16.x` | Sandbox / NOM / NodePlugin / ToolSynthesis + Pipeline Role |

### Phase 2 — 발행·격리·정책 (v0.17 ~ v0.21)

워크플로우를 wheel 로 컴파일해 MCP stdio 서버로 배포하는 흐름, 발행 전 격리 검증, 선언형 Guard 정책 시스템이 자리잡은 시기입니다. Stage / Tool / MCP / Plugin 을 단일 IR (`NOMGraph`) 로 통합했습니다.

| 버전 | 핵심 |
|---|---|
| `v0.17.0` | **Policy Gate** — 선언형 Guard × 4 훅 포인트 + `entry_points` 외부 Guard 합류 |
| `v0.18.0` | **MCP 양방향 연동** — 하네스 → wheel → MCP stdio 발행 / 마켓·Station → s04 카탈로그 흡수 |
| `v0.20.0` | **Sandbox Verifier** — `MCPStdioVerifier` + POSIX rlimit + SHA-256 재현성 해시 |
| `v0.21.0` | **NOM IR 허브** — `to_mcp_schema` / `to_sandbox_payload` / `to_wheel_snapshot` 세 변환을 단일 그래프로 |

### Phase 3 — 독립성 정리 (v0.22 ~ v0.25)

엔진이 xgen 서비스(workflow / mcp-station / documents) 와 직접 결선되어 있던 잔재를 호스트(이식측)로 옮긴 시기입니다. 도구 공급 채널도 네 갈래에서 `ToolSource` 단일 Protocol 로 정리했습니다.

| 버전 | 핵심 |
|---|---|
| `v0.22.0` | **엔진 독립성** — xgen 특화 코드 호스트 이관 + `ExternalNodeRef` Protocol + `REQUIRED_STAGES` 레지스트리 |
| `v0.23.0` | **MCP Tool Annotations** — `readOnlyHint` / `destructiveHint` / `idempotentHint` / `openWorldHint` 를 Tool ABC 의 정식 속성으로 |
| `v0.24.0` | **HITL Guard + Agent-controlled Compact Tool** — `destructiveHint=true` 도구 사용자 승인 모달 |
| `v0.25.0` | **ToolSource 단일 공급 채널** — s04 의 `mcp_sessions` / `custom_tools` / `node_tags` / `cli_skills` 4 하드코딩 제거 + `/tool-sources` 엔드포인트 |
| `v0.25.3` | **HarnessConfig 헬퍼** — `is_autonomous()` / `is_selected()` / `is_off()` 도메인 캡슐화 (리터럴 `== "autonomous"` 비교 추적 불필요) |

### Phase 4 — 라이브 검증 패치 사이클 (v0.26.x)

프로덕션 운영에서 한 번 검증할 때마다 한두 개씩 드러나는 결함을 즉시 메우는 패치 사이클입니다. 보고서 → 라이브 재검증 → 패치 → 다음 검증에서 또 결함 발견 → 패치, 의 빠른 반복으로 진행됩니다.

| 버전 | 무엇이 깨져있었나 | 어떻게 고쳤나 |
|---|---|---|
| `v0.26.0` | UI 클릭이 LLM 환경에 안 박히는 4 stage_param + label-only strategy 1개 + EventQueue 백프레셔 부재 | Dead UI 4건 제거 (`s01.provider` / `s02.memory_source` / `s06.files` / `s09.max_iterations`), label-only 1건 제거 (`s03.simple`) + 분기 신규 2건 (`s04.none` / `s10.noop`), queue 1000→8000 + drop 카운터 |
| `v0.26.1` | v0.26.0 에서 dead 로 제거한 `s06_context.files` 가 frontend UI 엔 살아있어서 사용자 클릭이 무효화 | 엔진에 진짜 wiring 추가 (`metadata_filter.file_name` 자동 라우팅) → 필드 부활 |
| `v0.26.2` | OpenAI strict schema 가 `properties` 없는 도구를 거부 → HTTP 400 (SynthesizedToolSource 자동 등록 도구 영향) | `providers/openai.py:_convert_tools` 가 `type=object` + `properties` 누락 시 `{}` 자동 보강 |
| `v0.26.3` | `s10_save` 가 dict 컬럼 (`input_data` / `output_data`) 으로 보내지만 실 DB 는 text (`input_text` / `output_text`) — 매 실행 `inserted_id=None` 으로 graceful 종료, `/executions` 빈 채 | record 컬럼명을 실 schema 에 맞춰 직렬화 (5K / 50K 자 truncate) |
| `v0.26.4` | OpenAI batch transport (`stream=False`) 가 STOP 이벤트 `.text` 로 응답 한 번에 yield, 엔진 STOP 핸들러는 `output_tokens` 만 처리 → 응답 텍스트 사라짐 | `core/llm_call.py:_single_call` STOP 핸들러에 `event.text` 처리 + `MessageEvent` emit 추가 |
| `v0.26.5` | Anthropic `thinking` 켤 때 `thinking_budget > max_tokens` 이면 무조건 HTTP 400 — engine default 도 동일 함정 (`max_tokens=8192 < thinking_budget=10000`) | thinking 활성 시 자동 보정 `max_tokens = budget_tokens + 1024` (사용자 설정 무시 아니라 안전 보장) |
| `v0.26.6` | DAG orchestrator 가 `PipelineState(tool_definitions=...)` 로 init — v0.11.22 도메인 그룹화 후 `dag.py:255` 동기화 누락 → 모든 DAG 노드 100% TypeError | init kwarg 제거, instance 생성 후 `state.tool_definitions = ...` setter 로 박음 |
| `v0.26.7` | `max_iter=1` + 도구 활성 시 LLM 이 첫 iter 에서 도구만 호출, 답변 텍스트 만들 두 번째 iter 가 없어 `output_length=0` 빈 응답 (default `max_iter=10` 환경에선 안 드러남) | Phase B 후 빈 응답 + 도구 실행 ≥ 1 이면 `tool_definitions=[]` 로 1회 보강 `main_call` (직후 `tool_definitions` 원복 → 다음 iteration / 외부 코드 영향 0) |
| **`v0.26.10`** | DAG orchestrator 가 sub-Pipeline 의 `DoneEvent` 도 그대로 외부 emitter 로 forward → `EventEmitter.stream()` 의 자동 break (events/emitter.py:102) 가 첫 노드 끝에서 발화 → 두 번째 노드 이벤트가 외부 클라이언트에 도달 못 함 + "DAG 실행 타임아웃" 으로 끊김 | `_forward_events()` 가 sub Pipeline 의 `DoneEvent` 만 skip. DAG 전체 `DoneEvent` 는 `run()` 마지막에 별도 emit (line 229) 하므로 정상 종료 신호 유지. 다른 이벤트 (Stage / Metrics / ToolCall / Error / …) 는 그대로 forward |
| **`v0.26.11`** | 외부 확장 6 결함 일괄 — `pyproject.toml` 의 6 그룹 lock-in 빈본 / `fan_out_strategies`·`evaluation_criteria` 의 silent contract / `model_pricing` 이 closed table / 보조 LLM 호출이 `state.token_usage` 에 누적 안 됨 / `aux_max_tokens=500` 매직넘버 4 곳 박제 | `entry_points` 6 그룹 (`orchestrators`·`sandbox_verifiers`·`tools(gallery)`·`phases`·`node_plugins`·`model_pricing`) 빈본 신설 + `register_model_pricing()` API + `aux_call()` 통합 헬퍼 (state.llm_call_count + cost 일관 누적) + `HarnessConfig.aux_max_tokens` 필드 |
| **`v0.26.12`** | `HarnessConfig.from_workflow` 에 `aux_max_tokens` 추출 누락 → 사용자가 `hc.aux_max_tokens=300` 박아도 default 500 그대로 적용 | `from_workflow` 에 `aux_max_tokens` 추출 1줄 추가 |
| **`v0.26.13`** | OpenAI provider 가 MCP 도구 schema 의 `type=["string","null"]` (배열 타입) / `anyOf+null` / `$ref` 패턴을 그대로 forward → `invalid_function_parameters` HTTP 400 (Tavily 류 MCP 도구 첫 호출에서 SSE 끊김). vLLM/Anthropic 은 관대해 회귀로 안 잡힘 | `providers/openai.py:_normalize_for_openai` 헬퍼 — type 배열에서 null 제거 + anyOf/oneOf null branch 평탄화 + $ref drop |
| **`v0.26.14`** | `_call_with_retry` (RateLimit / Overload / Provider 5xx) 가 SSE 로 retry 발생 안 알리고 `logger.warning` 만 → 클라이언트는 응답 지연만 보고 retry 사실 모름. 별개로 `s06_context` description 이 "토큰 윈도우 관리" 로 잘못 안내 | 세 분기에 `RetryEvent` SSE emit 추가 (attempt N/M + delay + 에러 첫 120 자 reason) + `s06_context` description 을 "RAG · DB · 폴더 · 파일 · GraphRAG → 답변 직전 컨텍스트 주입, 초과 시 Cascade L3~L5 압축" 으로 정정 |
| **`v0.26.15`** | OpenAI 가 enum 항목이 `[{value, label}]` dict 면 거부 (xgen-nodes options 가 dict). 별개로 `s06_context` reranker toggle 이 `bool(str(False))=True` 함정에 빠져 default `False` 인데 항상 rerank 발동 | `_normalize_for_openai` 가 enum dict → `.value` 평탄화. reranker toggle 은 `value is True` 또는 문자열 `'true'` 만 활성 (저장된 `'False'` 문자열 호환 유지) |
| **`v0.26.16` / `v0.26.17`** | v0.26.16 에서 Stage 코드에 한국어 description / 톤 통일을 박았는데 메모리 `feedback_stage_machine_only` (UI 자연어는 docstring 또는 프론트 i18n) 위반 | v0.26.17 에서 자가 원복 — UI 텍스트는 v0.26.15 직전 상태로. functional fix (Retry SSE / OpenAI enum / s06 reranker) 는 유지 |
| **`v0.26.18`** | v0.26.7 의 safeguard 가 `last_assistant_text` 비어있을 때만 추가 호출 → 짧은 intro (`"분석해드리겠습니다."` 37 자) + tool_use 패턴에선 truthy 라 skip → 도구 결과 들어왔지만 합성 답변 못 만들어 사용자 화면에 37 자만 도착 | safeguard 임계 일반화 — `len(last_assistant_text) < 200` + `tools_executed_count > 0` + `final_output 미설정` (tool_use 후 LLM follow-up 부재 케이스 전반 포섭) |

이전 변경: [CHANGELOG.md](CHANGELOG.md).

---

## 작동 기능 일람 (production 검증)

`xgen.x2bee.com/harness` 라이브 + 로컬 docker `saleskit` 인증으로 단계별 실 호출 검증된 기능 (2026-04-28 기준 v0.26.18 + 이식측 commit `7726c8b`).

### 1. 13 Stage 파이프라인 — 모두 실행

| Stage | 역할 | strategies | 검증 |
|---|---|---|---|
| `s00_harness` | 본문 LLM 호출 + iterative replan | `streaming` / `batch` (`TransportStrategy`) | ✅ |
| `s01_input` | 사용자 입력 분류·정리 | `default` / `with_classification` | ✅ |
| `s02_history` | 메모리 검색 (embedding) | `default` / `embedding_search` | ✅ |
| `s03_prompt` | 섹션 우선순위 조립 | `section_priority` | ✅ |
| `s04_tool` | 도구 게이팅 (3-level / eager / none) | `progressive_3level` / `eager_load` / `none` | ✅ `tools_count=42, tools_bound=43, sources_used=[mcp-sessions, xgen-nodes]` |
| `s05_strategy` | 응답 전략 (CoT / ReAct / capability planner / none) | 4 종 | ✅ |
| `s05_policy` | Guard 체인 검사 (4 훅 포인트) | hook-based | ✅ Skipped/Active 양쪽 |
| `s06_context` | RAG · DB · 폴더 · 파일 · GraphRAG 검색 + Cascade 압축 | `token_budget` / `sliding_window` / `microcompact` / `context_collapse_overlay` / `autocompact_llm` / `cascade` (6 종) | ✅ |
| `s07_act` | 도구 실행 (병렬 read / 직렬 write) | `default` / `parallel_read` | ✅ `tools_executed=1, success_count=1` |
| `s08_judge` | LLMJudge / rule_based scorer | `llm_judge` / `rule_based` / `none` | ✅ `score=0.25 verdict=retry` |
| `s09_decide` | loop 결정 (threshold / always_pass) | 2 종 | ✅ |
| `s10_save` | `harness_execution_log` 저장 | `default` / `noop` | ✅ |
| `s11_finalize` | 출력 포맷 + metrics emit | `default` / `format_json` | ✅ `10139ms · 13123tok · $0.0423` |

### 2. ToolSource 단일 채널 (v0.25.0+) — 4 소스 합류

| source_id | 카테고리 | production 검증 |
|---|---|---|
| `mcp-sessions` | MCP stdio 서버 — 세션별 도구 자동 디스커버리 | ✅ `current_time` 호출 → "2026-04-28 13:10:21 UTC" 반환 |
| `custom-api` | 사용자 저장 HTTP API 도구 | ✅ user-scoped (`x-user-*` forward) |
| `xgen-nodes` | 캔버스 노드 → 도구 변환 (mcp/tool/api/agent 카테고리) | ✅ 39 도구 / 노드 wrapping. langchain Tool factory 자동 invoke (BUG-10 fix) |
| `SynthesizedToolSource` | LLM 합성 도구 (`/auto-synthesize` → wheel 빌드 → publish) | ✅ `compile_nom_graph` + sandbox gate |

외부 확장: `register_tool_source(name, impl)` 또는 `entry_points("xgen_harness.tool_sources")`.

### 3. Provider — 빌트인 5종 + entry_points 외부 추가

| provider | streaming | batch | tool_use | thinking |
|---|:-:|:-:|:-:|:-:|
| `anthropic` | ✅ | ✅ | ✅ | ✅ (auto `max_tokens=budget+1024`) |
| `openai` | ✅ | ✅ | ✅ (Tavily 류 schema 정규화 — v0.26.13/15) | — |
| `google` (Gemini) | ✅ | ✅ | ✅ | — |
| `bedrock` | ✅ | ✅ | ✅ | — |
| `vllm` (OpenAI 호환) | ✅ | ✅ | 모델별 | — |

신규 모델 가격: `register_model_pricing()` 또는 `entry_points("xgen_harness.model_pricing")` (v0.26.11+).

### 4. Orchestrator — 5 패턴

| name | 동작 | 사용 예 |
|---|---|---|
| `linear` | 13 Stage 1 회 직선 | 짧은 질의 응답 |
| `iterative` | Phase B 루프 (`max_iterations`) | tool_use → judge → retry |
| `react` | Reasoning + Acting 인터리브 | 복합 추론 |
| `plan_execute` | Plan 작성 후 Stage 별 실행 | DAG 조립 단계 |
| `dag` | 다중 하네스 노드 토폴로지 (병렬 / 순차) | 멀티 에이전트 — `/dag/execute/stream` 검증 (`110 SSE lines, 15 stage enter/exit`) |

확장: `register_orchestrator()` 또는 `entry_points("xgen_harness.orchestrators")`.

### 5. Policy Gate — 6 Guard × 4 훅 포인트 (v0.17.0+)

```
Guards (6) × Hook points (4) = 24 조합
─────────────────────────────────────────
content                 │ post_response · pre_main
cost_budget             │ loop_boundary
hitl                    │ pre_tool
iteration               │ loop_boundary
token_budget            │ loop_boundary
tool_precondition       │ pre_tool
```

- `register_guard(name, factory)` 또는 `entry_points("xgen_harness.guards")`.
- 정책 비어있으면 자동 skip — 코드 분기 X.

### 6. RAG / Context — 다중 리소스 검색

| 리소스 | 옵션 소스 | 검증 |
|---|---|---|
| RAG collections (Qdrant) | `rag-collections` (16 production) | ✅ `chunks=N, top_k=4, rerank=optional` |
| Files (storage flatten) | `files` | ✅ `metadata_filter.file_name` 자동 라우팅 |
| Folders (collection group) | `folders` | ✅ |
| DB connections | `db-connections` | ✅ |
| Ontology collections | `ontology-collections` | ✅ |
| GraphRAG | (외부 등록) | ✅ |

자동 압축 Cascade L3 → L4 → L5 (microcompact / context_collapse_overlay / autocompact_llm) — `s06_context` 단에서 토큰 예산 초과 자동 감지.

### 7. Wheel Compile · Sandbox · Publish — 폐쇄 루프 (v0.10.0+)

```
HarnessConfig
    ↓ compile_workflow()
xgen-gallery-<name>-<ver>.whl    (entry_points 자동 주입)
    ↓ /install/verify  (격리 venv + MCPStdioVerifier + payload_hash)
검증 통과 → SHA-256 재현 해시
    ↓ /install/register or /compile/publish
mcp-station 세션 생성  (server_command='-c' inline pip install + serve)
    ↓ verifier 5 분 cron
harness_published_wheels.status = 'running' / 'error'
```

- **샌드박스 정책 3 단** (`HARNESS_SANDBOX_POLICY` env 또는 publish body): `strict` (실패 시 등록 중단) / `advisory` (리포트만) / `off` (스킵).
- **Verify-only 분리** (`/install/verify`) — 결과 카드 (payload_hash + tools 미리보기) 보고 수동 register 결정.
- **Auto-synthesize** (`/auto-synthesize`) — LLM 합성 도구 → NOMGraph → wheel → sandbox → publish 한 번에.

### 8. NOM IR 허브 (v0.21.0+)

`NOMGraph` 단일 그래프에서 3 변환:
- `to_mcp_schema()` — MCP stdio Tool 정의
- `to_sandbox_payload()` — 격리 검증 입력
- `to_wheel_snapshot()` — entry_points 메타

### 9. HITL — Agent-controlled Compact + Approval Modal (v0.24.0+)

- `destructiveHint=true` 도구 호출 직전 → `ApprovalRequired` SSE 이벤트
- 사용자 승인 모달 → `POST /approvals/{id}` decision (`accept` / `reject` / `edit_input`)
- 거부 시 LLM 다른 도구 시도 / 수정 시 인자 교체 후 재호출

### 10. SSE 스트리밍 — 14 종 이벤트 (v0.8.21+)

| 이벤트 종류 | 발생 시점 |
|---|---|
| `stage.enter` / `stage.exit` | 13 stage 입출 |
| `text.delta` | LLM 토큰 단위 스트림 |
| `tool.call` / `tool.result` | s07_act 단계 |
| `pipeline.complete` / `pipeline.error` / `pipeline.metrics` | 종료 |
| `evaluation` | s08_judge verdict + score |
| `verbose.service_lookup` / `verbose.capability_bind` | 자동 wiring 추적 |
| `verbose.stage_substep` | 14+ 종 substep (tool_call_start/complete, rag_fetch_*, llm_request_*, sources_discover_*, capability_*, service_lookup, aux_llm_*, ...) |
| `verbose.retry` | RateLimit / Overload / 5xx 재시도 (v0.26.14+) |
| `planning` | `s05_strategy` Plan 카드 (chosen / skipped / params / reasoning) |
| `log` | 호스트 logger tap → SSE 브리지 (이식측 책임) |

### 11. 16 확장 지점 — `entry_points` + Protocol

| group | 무엇이 추가되나 |
|---|---|
| `xgen_harness.stages` | Stage swap (외부 작업자 `STAGE4-LOTTE` 같은) |
| `xgen_harness.strategies` | Stage 별 전략 변형 |
| `xgen_harness.strategy_variants` | 디폴트 복사 v2 (v0.10.4+) |
| `xgen_harness.orchestrators` | 새 실행 패턴 (v0.26.11+) |
| `xgen_harness.tool_sources` | 새 도구 공급 채널 |
| `xgen_harness.option_sources` | 동적 드롭다운 옵션 |
| `xgen_harness.publish_targets` | wheel 발행 대상 (mcp-station / gallery / 외부) |
| `xgen_harness.galleries` | 설치된 갤러리 자동 발견 |
| `xgen_harness.guards` | Policy Gate 외부 Guard |
| `xgen_harness.fan_out_strategies` | multi-agent 분기 (v0.26.11+) |
| `xgen_harness.evaluation_criteria` | s08_judge 평가 기준 (v0.26.11+) |
| `xgen_harness.sandbox_verifiers` | wheel 검증 변형 (v0.26.11+) |
| `xgen_harness.tools` | 단일 도구 (gallery 패키지) (v0.26.11+) |
| `xgen_harness.phases` | Phase A/B/C 사이클 변형 (v0.26.11+) |
| `xgen_harness.node_plugins` | 노드 플러그인 (v0.26.11+) |
| `xgen_harness.model_pricing` | 사내 vLLM / 자체 호스팅 모델 가격 (v0.26.11+) |

### 12. 3 모드 — 자유도 vs 안정성

| `harness_mode` | 의미 | LLM 자유도 |
|---|---|---|
| `off` | 코드가 직접 도구·orchestrator·strategy 다 박음. LLM 은 본문만 | 0 |
| `selected` | 일부만 핀 (`active_strategies={...}`), 나머지는 LLM | 부분 |
| `autonomous` | 거의 다 LLM 결정 — Stage 별 capability 점진 노출 | 최대 |

`HarnessConfig.is_autonomous() / is_selected() / is_off()` 헬퍼로 비교 (v0.25.3+).

---

## 사용자 매뉴얼 (UI 사용자용)

엔진을 직접 호출하지 않고 XGEN 하네스 페이지(`http://xgen.x2bee.com/harness`) 만 사용하시는 경우, [docs/confluence/harness-user-manual.md](https://github.com/jinsoo96/xgen-harness-executor/blob/main/docs/confluence/harness-user-manual.md) 를 참고하시기 바랍니다.

---

## 라이선스

MIT
