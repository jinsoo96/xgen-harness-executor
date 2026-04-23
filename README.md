<div align="center">

# xgen-harness

### 12 Stage 에이전트 실행 프레임워크 — Stage = 환경 슬롯

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
> 12 Stage 가 환경 슬롯 (capability/도구/리소스/파라미터를 LLM 에 노출), 사용자는 **무엇을** 할지만 선언, 하네스가 **어떻게** 자동 조립.

**v0.16.6 기준** — Pipeline Role 체계 (Stage 이름 리터럴 0) + Planner 통제탑 (Auto/Selected/Off 3 모드) + 자동 오케스트레이터 (linear/iterative/plan_execute/react/dag) + Strategy × Capability 3층 구조.

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

→ 12 Stage (입력 → 이력 → 프롬프트 → 도구 → 전략 → 컨텍스트 → 본문 LLM → 판정 → 결정 → 저장 → 마무리) 가 default Strategy 로 1 바퀴 실행.

---

## 모드 3 종 — 무엇을 어떻게 설정하나

| 모드 | 코드 | 동작 | 언제 쓰나 |
|---|---|---|---|
| **Off (기본)** | `harness_mode="off"` | 12 Stage 정해진 순서, Plan 안 만듦, 본문 LLM 호출 1 회 | 빠른 단발 Q&A |
| **Selected** | `harness_mode="selected"` + `pinned_strategies={...}` | 사용자 핀한 Stage→Strategy hard-pin, 나머지 Planner 자율 | 일부만 강제, 나머진 자율 |
| **Auto** | `harness_mode="autonomous"` | Planner LLM 이 Stage/Strategy/도구/orchestrator_hint 자율 결정 | 복잡 요청 · RAG · 멀티턴 도구 |

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

## 12 Stage 카탈로그 — 기능 / 설정 / Strategy

### 초기화 그룹 (ingress, 1 회)

| # | Stage | 하는 일 | 주요 설정 | Strategy |
|---|---|---|---|---|
| 0 | **s00_harness** (Planner) | LLM 핸들 owner + 본문 호출 dispatcher (모드 별 책임) | `harness_mode`, `provider`, `model` | `streaming` * / `batch` |
| 1 | **s01_input** (필수) | 사용자 입력 추출 + 정규화 | `input_text`, `attached_files` | `default` * / `multimodal` |
| 2 | **s02_history** | 같은 interaction 의 이전 turn 가져옴 | `history_limit` | `last_n` * / `relevant` |
| 3 | **s03_prompt** | System prompt 주입 | `system_prompt`, `prompt_id` | `static` * / `templated` |
| 4 | **s04_tool** | LLM 노출 도구 카탈로그 | `mcp_sessions`, `custom_tools`, `cli_skills`, `node_tags`, `capabilities`, `custom_tools_mode` | `default` * / `progressive` / `auto` |

### 에이전트 루프 그룹 (loop, max_iterations 회 반복)

| # | Stage | 하는 일 | 주요 설정 | Strategy |
|---|---|---|---|---|
| 5 | **s05_strategy** | 각 Stage 의 Strategy 결정 | `pinned_strategies` (Selected) | `default` * / `pinned_first` / `llm_decide` / `cascade` |
| 6 | **s06_context** | RAG/온톨로지/DB 검색 → 컨텍스트 주입 | `rag_collections`, `rag_top_k`, `rag_ingestion_mode`, `ontology_collections`, `db_connections` | `microcompact` * / `context_collapse` / `autocompact_llm` / `cascade` / `progressive_3level` / `none` |
| 7 | **s07_act** ★ | 본문 LLM 호출 (Planner 가 직전 dispatch) + tool_use multi-turn | `max_tool_rounds`, `force_tool_use` | `default` * / `react` |
| 8 | **s08_judge** | 응답 품질 평가 (0~1 점수) | `validation_threshold`, `judge_model` | `llm_judge` * / `rule_based` / `none` |
| 9 | **s09_decide** (필수) | judge 결과 보고 loop_decision 설정 | — | `default` * / `always_complete` |

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

Auto 모드일 때 Planner 가 입력·카탈로그 보고 `Plan.orchestrator_hint` 결정 → Phase B loop 가 분기:

| hint | Phase B 동작 | 사용 케이스 |
|---|---|---|
| `linear` | 1 회 실행 후 종료 | 단발 Q&A |
| `iterative` (default) | 매 iter Plan replan + 12 Stage 1바퀴 | 멀티턴 도구 |
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

## 확장 — 외부 패키지가 끼워넣는 7 지점

| 지점 | entry_points 그룹 | 용도 |
|---|---|---|
| **Stage** | `xgen_harness.stages` | 새 Stage (예: 자체 Planner, 도메인 Stage) |
| **Strategy** | `xgen_harness.strategies` | 한 Stage 의 새 변형 |
| **Capability** | `xgen_harness.capabilities` | 선언적 도구 wiring (예: `retrieval.web_search`) |
| **Provider** | `xgen_harness.providers` | 새 LLM provider |
| **Orchestrator** | `xgen_harness.orchestrators` | 새 hint (위) |
| **Tool** | `xgen_harness.tools` | 단일 도구 |
| **NodeAdapter** | `xgen_harness.node_adapters` | 캔버스 노드 → Stage 어댑터 |

```python
# pyproject.toml
[project.entry-points."xgen_harness.strategies"]
my_compactor = "my_pkg.compactor:MyCompactor"
```

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

## MCP 도구 사용

```python
config = HarnessConfig(
    mcp_sessions=["my-playwright", "krra-search"],   # mcp-station 등록 세션 ID
)
```

`s04_tool` 이 mcp-station 에서 세션의 도구 목록 가져와 LLM 카탈로그에 합류.

---

## Capability 선언적 도구

```python
config = HarnessConfig(
    capabilities=["retrieval.web_search", "retrieval.rag_query"],
    capability_params={"retrieval.web_search": {"max_results": 10}},
)
```

`s04_tool` 이 capability registry 에서 자동 매핑 → tool_definitions 합류. 외부 패키지가 entry_points 로 capability 등록하면 자동 발견.

---

## v0.12 → v0.16 진화 요약

| 버전 | 핵심 |
|---|---|
| `v0.12.0` | REAL HARNESS Phase 1 — `s00_harness` Planner 도입 + 13 Stage 디렉토리화 |
| `v0.13.0` | REAL HARNESS Phase 2 — 단일 provider + iterative planning |
| `v0.14.0` | s00_harness 통제탑 승격 — 본문 LLM 호출 owner + 3 모드 |
| `v0.15.0~3` | 재귀적 자율주행 — orchestrator_hint + max_iterations + OrchestratorRegistry + fs_scanner 자동 발견 |
| `v0.16.0~6` | 자가증식 골조 (Sandbox/NOM/NodePlugin/ToolSynthesis) + Pipeline Role 체계 (Stage 이름 리터럴 12→0) |

이전 변경 (`v0.11.14 → v0.11.23`) 은 [CHANGELOG.md](CHANGELOG.md) 참조 — Claude Code 5-Level 압축 / tool_choice API / drift-free 연결선.

---

## 사용자 매뉴얼 (UI)

엔진을 직접 쓰지 않고 XGEN 하네스 페이지 (http://xgen.x2bee.com/harness) 사용자라면 → [docs/confluence/harness-user-manual.md](https://github.com/jinsoo96/xgen-harness-executor/blob/main/docs/confluence/harness-user-manual.md) (사용자 친화 한국어 매뉴얼)

---

## 라이선스

Apache 2.0

