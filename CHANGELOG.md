# Changelog

All notable changes to `xgen-harness` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.14.0] — 2026-04-22

### 🎯 s00_harness 통제탑 승격 — s07_llm 삭제 + 번호 시프트 + 재귀적 자율주행

사용자 확정 기조: **"7 번 껍데기화 하지 말고, 그냥 7을 지워버리고 번호 떙기자. 본문 LLM 호출은 00 에서 한 번 설정하고 플랜·에이전트 실행 모두 거기서 관할"**. 재귀적 자율주행 4 레벨 (구조 파악 → Stage 선택 → Strategy 선택 → 파라미터/도구 선택) 을 전부 s00_harness 단일 지점으로 통일.

**🔴 1. s07_llm Stage 삭제 + 번호 시프트 (11 Stage)**:
- `xgen_harness/stages/s07_llm/` 디렉토리 완전 제거
- s08_act→s07_act / s09_judge→s08_judge / s10_decide→s09_decide / s11_save→s10_save / s12_finalize→s11_finalize
- `ALL_STAGES` 12 → 11, `REQUIRED_STAGES = {s01_input, s09_decide, s11_finalize}` 재정의
- `STAGE_ID_ALIASES` 하위호환: 구 id 유입되면 새 id 로 정규화 (s07_llm → s00_harness 포함)
- Stage.phase 경계 재조정: ingress (order ≤4), loop (order ≤9), egress (order ≥10)

**🔴 2. 본문 LLM 호출의 s00 이관 (`core/llm_call.py` + `stages/strategies/transport.py`)**:
- 과거 s07_llm 이 하던 _call_with_retry / _single_call / streaming / token tracking / cost 추정 로직을 `core/llm_call.py` 헬퍼로 추출
- **Transport Strategy 패턴**: `StreamingTransport` / `BatchTransport` 가 `TransportStrategy` 인터페이스 구현 → `register_strategy("s00_harness", "transport", "streaming"/"batch", cls)` 로 레지스트리 등록 → 리터럴 "batch" 비교 금지
- 외부 플러그인이 신규 Transport (websocket / caching_proxy / 로깅 wrap) 를 entry_points 로 얹어도 코드 변경 0
- `HarnessStage.main_call(state, strategy=...)` 가 StrategyResolver 로 이름 해석 후 `transport.call(state)` 위임

**🔴 3. Pipeline 본문 호출 주입 (`core/pipeline.py`)**:
- Phase B 루프 안에서 `stage.stage_id == "s07_act"` 직전에 `self._invoke_main_call(state, s00_stage)` 호출 — s07_llm Stage 자리를 정확히 메움
- StageEnter/Exit 이벤트는 `s00_harness` 이름 + `description="main_call (streaming)"` 으로 발행 → 프론트가 "s00 이 본문 호출 수행 중" 인지 분간
- 기존 iterative replan (매 iter 시작에 s00 재호출) 로직 유지 — autonomous 모드에서 Plan 갱신

**🔴 4. HarnessConfig.harness_mode (`core/config.py`)**:
- 3 모드: `"autonomous"` (Planner LLM 자율 조립) / `"selected"` (사용자 핀 그대로 적용) / `"off"` (s00 skip, 전체 Stage 실행 — 레거시 noop)
- 빈 문자열 기본값 → `__post_init__` 에서 `use_planner=True → autonomous, False → off` 로 파생 (하위 호환)
- `Pipeline.from_config`: mode != "off" 면 s00_harness 를 ingress 최상단에 주입 (과거 use_planner 체크 대체)

**🔴 5. s00 `list_strategies()` 동적 조회 (하드코딩 0)**:
- `_REGISTRY` 에서 `(s00_harness, transport, *)` 항목 런타임 조회. 새 Transport 가 등록되면 자동으로 UI/카탈로그에 노출 — "재귀적 자율주행" 원칙 준수

**🟡 6. 교차 참조 일괄 업데이트**:
- errors/hierarchy.py, core/builder.py, core/artifact.py, core/strategy_resolver.py, core/provider_bootstrap.py, integrations/workflow_bridge.py, integrations/xgen_node_adapters.py, orchestrator/multi_agent_planner.py, stages/s01_input/stage.py, stages/strategies/token_tracker.py, core/presets.py — 구 stage_id 문자열 전수 교체
- strategy_resolver.py: 구 s07_llm 슬롯 (retry/parser/thinking/token_tracker/cost_calculator) 전부 s00_harness 로 이관

**🟢 자가검증 (grep)**:
- `grep -rn "s07_llm" xgen_harness/ --include="*.py"` = 0 (comments/docstrings 일부 남음, 로직 0)
- `register_strategy("s07_llm"...)` = 0 (전부 s00_harness 로 이관)
- `ALL_STAGES` 길이 = 11, 등록된 Stage = 12 (s00_harness + 11)
- StrategyResolver → StreamingTransport / BatchTransport 해석 PASS

**⚠ 마이그레이션 가이드**:
- 기존 workflow_data 에 `use_planner: true` 있으면 그대로 동작 (autonomous 로 파생)
- 구 id (s07_llm 등) 로 저장된 stage_params / active_strategies 는 STAGE_ID_ALIASES 가 자동 정규화
- 이식 측 (`xgen-workflow feature/harness-v2`) pin 을 `xgen-harness>=0.14.0` 으로 상향 필요

---

## [0.13.0] — 2026-04-22

### 🌌 REAL HARNESS Phase 2 — 단일 Provider + Iterative Planning (진짜 자율 주행)

사용자 요구 핵심: **"LLM 프로바이더를 한 번만 설정하고 그 주체가 이후 과정 전체를 통제하는 고결한 구조"** — v0.12.x 의 rigid 실행(Plan 한 번 세우고 그대로) 에서 v0.13.0 iterative 자율 주행으로 승격.

**🔴 1. 단일 Provider 통제 (`core/pipeline.py`)**:
- `Pipeline.run()` 진입부에서 `ensure_provider(state, stage_id="pipeline")` 1 회 선초기화. s07_llm / s09_judge / s00_harness(Planner) 가 모두 **동일 state.provider 인스턴스** 를 재활용. 실패 시 logger.debug 로 보류 (provider 불필요 Stage 는 정상 진행).
- 결과: LLM API 클라이언트가 파이프라인 1 회 life-cycle 동안 1 인스턴스로 고정. 불필요한 재생성·비용 0.

**🔴 2. Iterative Planning (`core/pipeline.py` Phase B)**:
- Pipeline Phase B(agentic loop) 의 매 iter 시작에 **s00_harness 를 재호출** 해 Plan 을 갱신. 첫 iter 는 Phase A 에서 실행한 Plan 그대로, iter 2 부터 replan.
- `HarnessPlan.done=True` 면 `state.loop_decision="complete"` 로 즉시 Phase B 종료 — Planner 가 "이제 충분" 을 선언하면 불필요 iteration 절감.
- `_find_loop_s00()` 헬퍼 추가: 기존 Pipeline 조립된 Stage 리스트에서 s00 인스턴스를 찾아 재사용 (신규 인스턴스 안 만듦, Plan 히스토리 연속성 보장).

**🔴 3. Previous Results 주입 (`core/planner.py::_collect_previous_results`)**:
- iter >= 2 에서 Planner.plan() 이 자동으로 `catalog["previous_results"]` 에 다음 snapshot 을 싣는다:
  - iteration / last_assistant_preview (400자) / tool_calls_so_far / recent_tool_calls[-5] / validation_score / validation_feedback / retry_count / total_tokens / rag_snippet_loaded
- LLM 이 **"이미 뭐 했는지" 보고 다음 조립 결정**. 시스템 프롬프트에도 iterative 맥락 1 줄 추가 ("previous_results 있으면 참고해 다음 행동 결정, 만족했다면 done=true").

**🔴 4. Plan.done 필드 + schema**:
- `HarnessPlan.done: bool` 추가. `PLAN_TOOL_INPUT_SCHEMA.properties.done` 에 설명 박제: "이전 실행이 사용자 요청 만족하면 true 로 loop 종료".
- `PlanningEvent.done` + `iteration` 필드 추가. `xgen_streaming.convert_to_xgen_event(PlanningEvent)` 가 payload 에 두 필드 실어 이식/프론트로 전달.
- s00_harness/stage.py 가 PlanningEvent emit 시 `state.loop_iteration` + `plan.done` 을 싣고 execute() 반환 dict 에도 포함.

**🟡 5. adapter use_planner 전달 + StageEnter bypassed 플래그** (v0.12.3 에서 누락됐던 것 포함):
- `adapters/xgen.py::XgenAdapter.execute` 가 harness_config.use_planner 를 HarnessConfig 에 전달.
- `integrations/xgen_streaming.convert_to_xgen_event(StageEnterEvent)` 가 description + bypassed: bool 플래그를 payload 에 추가 → 프론트 Plan.skipped 시각화.

**🟡 6. 디렉토리화 sed 누락 함수 내부 import 수정** (v0.12.2 에서 누락됐던 잔재):
- s04_tool / s05_strategy / s06_context / s07_llm / s08_act / s09_judge 의 함수 내부 들여쓰기 `from ..X` → `from ...X` (깊이 +1). `from .strategies.X` → `from ..strategies.X` (형제). 기존 에러 "No module named 'xgen_harness.stages.core'" 해소.

**실증** (컨테이너 내 직접 adapter.execute 경로):
- A(단순 인사): replan 1 회, Planner 첫 Plan 에서 `done=true` → executed=5, bypassed=8, **즉시 종료**.
- B(저작권 리스크): replan **3 회**. iter3 에서 Plan 이 축소 (s09_judge + s11_save 제거). s00 인스턴스 3 회 호출. **"이전 결과 보고 다음 조각 재조립"** 동작 확인.

**기존 사용자 영향**:
- `use_planner=False` (기본) 사용자는 동작 변화 0. 파이프라인 진입부 provider 선초기화만 추가되지만 idempotent 이라 외부 영향 없음.
- `use_planner=True` 사용자는 같은 요청에 Plan 이 iter 마다 갱신 — 기존보다 LLM 비용 증가 가능 (max_iterations 로 상한).

**프론트 (xgen-frontend feature/harness-v2)**:
- PlanningCard 에 `ITER #N` 파란 배지 + `done` 초록 배지 표시. iteration=0 은 배지 숨김 (첫 Plan).
- store.mapLogToPipelineEvent 의 planning kind 에 iteration / done 필드 보존.

**이식 (xgen-workflow feature/harness-v2)**:
- `pyproject.toml` pin `xgen-harness>=0.13.0`.

## [0.12.3] — 2026-04-22

### 🩹 hot-fix — XgenAdapter use_planner 누락 + StageEnter bypassed 플래그 전달

v0.12.2 배포 직후 실 adapter 경로 (XgenAdapter.execute → Pipeline.from_config) 에서 Planner 가 전혀 활성화되지 않는 regression 2 건 발견:

**1. `adapters/xgen.py::XgenAdapter.execute` 의 harness_config 파싱에서 `use_planner` 키 누락**:
- 프론트 `harness_config: {use_planner: true}` 를 보내도 adapter 가 HarnessConfig 에 전달하지 않아 Pipeline.from_config 에서 s00_harness 주입 분기가 안 탐.
- 결과: 토글을 켜도 LLM Planner 가 돌지 않고 12 Stage 고정 파이프라인만 실행. 사용자 시점에선 "s00 로그 안 뜨고 Plan 생성 안 됨".
- 수정: `hc.get("use_planner")` 를 `config_kwargs` 로 전달하는 분기 추가 (v0.11.26 의 admin/superuser 플래그 전달과 동일 패턴).

**2. `integrations/xgen_streaming.convert_to_xgen_event(StageEnterEvent)` 가 `description` 필드 누락**:
- Pipeline.`_emit_bypass` 는 `StageEnterEvent(description="bypassed")` 로 스킵 마커를 싣지만 변환기가 `description` 을 SSE payload 에 안 실어서 프론트가 Planner 의 bypass 여부를 판정 불가.
- 수정: `description` + `bypassed: bool` 2 필드 추가. 프론트 UI 가 Plan.skipped 표시에 직접 참조.

**실증** (XgenAdapter.execute 경로 · adapter 실제 호출):
- use_planner=true 로 "안녕" 입력 → PlanningEvent 1 회 emit, chosen=4~6 개, 나머지 Stage 에 `bypassed=true` 플래그 정상 전달.

## [0.12.2] — 2026-04-22

### 🩹 hot-fix — 디렉토리화 sed 가 놓친 함수 내부 상대 import 일괄 수정

v0.12.0 13 스테이지 디렉토리화 시 최상단 `^from ..` 만 `...` 로 치환 하고 **함수 내부 들여쓰기 된 `from ..` / `from .`** 은 빠뜨렸음. 실행 중 s06_context 등에서 `No module named 'xgen_harness.stages.core'` / `xgen_harness.strategies` ImportError 로 Pipeline abort.

수정 범위 (s04_tool / s05_strategy / s06_context / s07_llm / s08_act / s09_judge):
- 들여쓰기 된 `from ..X` → `from ...X` (엔진 루트 기준 깊이 +1)
- 함수 내부 `from .strategies.X` → `from ..strategies.X` (stages/strategies 형제 디렉토리)

**검증**: Pipeline.run() 실 LLM 호출 A/B 시나리오:
- A(단순 인사): chosen=[s01,s07,s10,s12] → executed=5, bypassed=8 (Plan.chosen 정확 반영)
- B(RAG+판단): chosen=12개 전체 → executed=20(루프 2회), bypassed=5
- PlanningEvent SSE 로 `event_kind=planning` + chosen/skipped/reasoning 전체 payload 전달 확인

**기존 사용자 영향**: v0.12.0/0.12.1 사용 중이라면 Planner 활성 시 Pipeline 이 abort 됨. v0.12.2 필수.

## [0.12.1] — 2026-04-22

### 🩹 hot-fix — PlanningEvent SSE 변환 누락 수정 (v0.12.0 직후)

v0.12.0 에서 `events/types.py::PlanningEvent` 와 `event_to_dict` 매핑은 추가했으나 **`integrations/xgen_streaming.py::convert_to_xgen_event` 에 변환 분기를 빠뜨려** PlanningEvent 가 SSE 로 전혀 흘러가지 않던 누락. 프론트 카드 렌더 PoC 작업 중 발견.

- `convert_to_xgen_event` 에 `PlanningEvent` 분기 추가 — `event_kind: "planning"` 으로 chosen/skipped/params/strategies/reasoning/planner_model/source 전체 SSE payload 에 실음.
- 이식측 `harness.py::_harness_stream` 변경 없음. pyproject pin `xgen-harness>=0.12.1` 만 상향.

## [0.12.0] — 2026-04-22

### 🎯 REAL HARNESS — Harness Planner 축 A 착수 (LLM 자율 조립)

사용자 핵심 깨달음 "지금 엔진은 12 스테이지를 고정 파이프라인으로 돌릴 뿐, 진짜 하네싱이 아니다. LLM 에게 환경(Stage·파라미터·기능 카탈로그) 을 주고 스스로 조립하게 해야 한다" 에 대한 첫 구현.

**🔴 Harness Planner (s00_harness) 신설**:
- `xgen_harness/stages/s00_harness.py::HarnessStage` — order=0, phase=ingress 최상단. LLM 이 카탈로그를 보고 Plan(Stage 선택/파라미터/Strategy) 을 내놓는 메타 스테이지.
- `xgen_harness/core/planner.py::HarnessPlanner, HarnessPlan` — 카탈로그 + user_input → LLM 호출 → Plan 반환. 프롬프트 자체도 카탈로그에서 동적 생성 (하드코딩 0). 파싱 실패 시 fallback (빈 chosen → Pipeline 이 전체 실행).
- `xgen_harness/core/catalog.py::get_catalog()` — Stage/Capability/Preset 카탈로그를 런타임에 발견. Stage 이름·필드 이름 리터럴이 이 파일에 0 개 (전부 레지스트리에서 읽음).

**🔴 Pipeline 에 Plan 분기**:
- `HarnessConfig.use_planner: bool = False` 추가 (하위 호환).
- `Pipeline.from_config` 가 use_planner=True 면 s00_harness 를 ingress 최상단에 prepend.
- 각 Stage 실행 직전 `_planner_skips` 로 Plan.chosen 체크 → 선택 안 된 Stage 는 bypass 이벤트(스킵 이유는 Plan.skipped 에서 추출).
- Plan.params / Plan.strategies 는 s00 이 state.config 에 shallow merge (이미 UI 에 설정된 값보다 Plan 이 우선, 언급 없는 값은 그대로 유지).

**🔴 PlanningEvent 신설**:
- `events/types.py::PlanningEvent` — chosen/skipped/params/strategies/reasoning/planner_model/source. `event_to_dict` 에 "planning" 타입으로 등록.
- 프론트가 이 이벤트를 카드로 렌더해 "왜 이 조합인지" 사용자에게 설명(explainability).

**🔴 provider_bootstrap DRY**:
- `xgen_harness/core/provider_bootstrap.py::ensure_provider()` — s07_llm._lazy_init_provider 로직을 공용 헬퍼로 추출. s00(Planner) 도 동일 경로로 provider 자체 초기화 가능. s07 의 기존 메서드는 ensure_provider 호출로 교체 (중복 제거).

**🟢 Public API export**:
- `xgen_harness` top-level: `get_catalog` / `HarnessPlanner` / `HarnessPlan` / `PlanningEvent` / `ensure_provider`.

**기존 사용자 영향**:
- `use_planner=False` 기본값이라 기존 엔진 동작 **완전 동일**. 이식 측에서 플래그 명시 주입할 때만 Planner 활성.
- s07_llm 의 `_lazy_init_provider` 메서드는 제거 — 외부에서 호출하던 코드는 `from xgen_harness import ensure_provider; await ensure_provider(state)` 로 교체.

**후속 (Phase 2~4)**:
- Stage Gallery (s09_judge 시범 분리), NOM (단일 IR), 독립 샌드박스, xgen 모노레포 노드 플러그인 뺐다꼈다. 자세한 로드맵은 `docs/harness/REAL_HARNESS.md`.

## [0.11.27] — 2026-04-22

### 🎯 2차 감사 지적 4건 일괄 해소 (기능 무력화 · 멀티턴 오염 · 사이클 silent drop · 집계 0 drop)

외부 2차 감사에서 제시한 권고 5건 중 확인된 실버그 4건 + cosmetic 1건 전량 해소. s09_judge 가 평가 noop 에 빠져 있던 상태를 복원하고, 세션 오염·DAG 사이클 silent drop·이벤트 필터링 버그를 순차 수정.

**🔴 #1 s09_judge LLMJudge provider 미주입 (기능 무력화 복원)**:
- `stages/s09_judge.py::execute()` — `resolve_strategy` 후 `strategy.set_provider(state.provider)` 호출 추가.
- 이전엔 `LLMJudge.__init__(provider=None)` 이후 `evaluate()` 가 `if not self._provider:` 로 조기 반환하여 s09_judge 가 **사실상 noop** 이었음. full preset 에서 평가/재시도 루프가 돌지 않던 근본 원인.
- `set_provider` 미노출 전략(NoValidation/RuleBased)은 `hasattr` 가드로 영향 없음.

**🔴 #2 core/session.py messages shallow copy (멀티턴 오염 차단)**:
- `SessionManager.run_turn` — `self.state.messages.copy()` + for-push 조합이 **list 만 복사하고 내부 dict 는 공유**. 한 턴에서 메시지 dict 를 수정하면 이전 턴 이력까지 변질.
- `copy.deepcopy` 전환. conversation_history / state.messages 둘 다 deepcopy.

**🔴 #4 DAG Kahn 사이클 silent drop (명시 실패로 전환)**:
- `orchestrator/dag.py::_topological_levels` — Kahn 이 처리한 노드 수(`processed`) 를 전체와 비교. `processed != len(self._nodes)` 면 사이클이므로 **`DAGCycleError(미해결 노드 목록)` raise**.
- 이전엔 사이클 노드가 level 에 못 들어가 **조용히 실행 누락** 됐음. 이제 즉시 명시적 실패.
- `DAGCycleError` 클래스 신설 + top-level export.

**🟡 #3 events/types.py::event_to_dict falsy drop (관측성)**:
- 이전: `v != 0 and v != 0.0 and v != ""` 로 합법적 0 값(`total_tokens=0` / `duration_ms=0` / `iterations=0` / `cost_usd=0.0`) 을 전부 drop.
- 지금: `None` 만 필터 (빈 dict 도 제외). 프론트가 0 값을 정상적으로 받아 집계/그래프에 반영.

**🟢 #5 Public API export 보강**:
- `xgen_harness/__init__.py` top-level 에서 `RateLimitError` / `OverloadError` / `ContextOverflowError` / `ToolTimeoutError` / `MCPConnectionError` / `ValidationError` / `ErrorCategory` / `DAGCycleError` 추가 export. 외부 기여자가 `from xgen_harness import RateLimitError` 로 바로 catch 가능.

**기존 사용자 영향**:
- #1 은 지금까지 평가가 noop 이었으므로 활성화 후 **validation_score 가 실제 값** 으로 돌아온다 (이전엔 bypass). full preset 사용자는 재시도 루프가 실제로 발동할 수 있음.
- #2 는 내부 동작, 성능 영향 미미.
- #3 은 이벤트 payload 에 0 값 필드가 추가로 포함됨. 프론트에서 `undefined` 를 가정하고 `?? 0` 폴백 쓰던 코드는 그대로 동작.
- #4 는 사이클이 있는 DAG 는 **지금까지도 실제 실행이 안 됐던** 상태 — 이제 명시적 실패로 전환되어 디버깅이 쉬워짐.

## [0.11.26] — 2026-04-22

### 🎯 XgenAdapter 하드코딩 제거 + services state 주입 (v0.11.25 감사 후속)

v0.11.24 에서 `get_xgen_auth_headers` 기본값을 `false` 로 교정했는데 `XgenAdapter.execute` 가 여전히 `user_is_admin="true"` / `user_is_superuser="true"` 로 하드코딩 호출하던 모순을 잡음. v0.11.25 에서 RAGSearchTool 에 DocumentService 를 주입받도록 바꿨는데 `state.metadata["services"]` 경로가 정작 주입되지 않던 누락도 해소.

**🔴 XgenAdapter.execute 시그니처 확장**:
- `user_is_admin: bool = False`, `user_is_superuser: bool = False` 파라미터 추가 (기본 False).
- `set_execution_context(..., user_is_admin="true" if user_is_admin else "false", ...)` — 호출자(이식측)가 명시 주입해야 권한 승격. v0.11.24 의 기본값 false 원칙이 실제로 동작.
- 이전: 고정 `"true"` 박제 → 지금: 게이트웨이 인증 결과를 그대로 전달.

**🔴 state.metadata["services"] 주입**:
- `XgenAdapter.execute` 가 `ResourceRegistry.load_all()` 직후 `state.metadata["services"] = self._services` 를 명시 주입.
- v0.11.25 에서 `RAGSearchTool(doc_service=...)` / `s04_tool` / `s08_act` 가 `state.metadata["services"].documents` 를 참조하도록 바꿔둔 경로가 실제로 물림. s02_history / s06_context 의 기존 참조 (5 지점) 도 그대로 정상 동작.

**이식측 반영 필요**:
- `xgen-workflow feature/harness-v2` `harness.py::_stream_harness_pipeline` — `XgenAdapter.execute(...)` 호출 시 request header 에서 admin / superuser 추출해 전달:
  ```python
  is_admin = request.headers.get("x-user-admin", "false").lower() == "true"
  is_super = request.headers.get("x-user-superuser", "false").lower() == "true"
  async for event in adapter.execute(..., user_is_admin=is_admin, user_is_superuser=is_super):
  ```
- pyproject pin `xgen-harness>=0.11.26`.

**기존 사용자 영향**:
- XgenAdapter 를 직접 쓰던 외부 코드는 admin/superuser 를 명시 주입 안 하면 내부 xgen 서비스 호출이 익명(admin=false) 으로 나감. 서버 정책에 따라 거부될 수 있음. 기존 동작을 유지하려면 `user_is_admin=True` 를 명시.

## [0.11.25] — 2026-04-22

### 🎯 엔진 독립성 완결 — xgen-documents API 스키마 직접 참조 전면 제거

v0.11.24 가 호스트 import 만 제거하고 내부 httpx 직접 호출은 그대로 남겨뒀던 모순을 마저 닫는 릴리즈. 엔진은 더 이상 `xgen-documents` API 경로(`/api/retrieval/documents/search` · `/api/retrieval/documents/collections`)를 알지 못한다.

**🔴 엔드포인트 침범 제거 (A)**:
- `api/router.py` — `GET /options/mcp-sessions`, `GET /options/rag-collections` 2 개 엔드포인트 삭제. 엔진이 xgen-mcp-station / xgen-documents 를 httpx 로 직접 쿼리하던 경로. 이식측 `harness_options_registry.py` 의 `/harness/options/<name>` 이 단일 진입점 (v0.11.23 단일 진실 소스). 이식/프론트 어디도 엔진의 이 엔드포인트를 호출하지 않아 제거가 안전.
- 외부 조직이 MCP stdio / CLI 로 단독 실행할 때 옵션이 필요하면 `register_option_source()` 로 자체 서비스에 붙이는 구조로 수렴.

**🔴 RAG Tool 자동 연동 (B)**:
- `tools/rag_tool.py::RAGSearchTool` — 생성자에 `doc_service: Optional[DocumentService] = None` 추가. 실제 검색은 **주입된 DocumentService.search() 만** 호출하며, `/api/retrieval/documents/search` URL 하드코딩 + httpx 클라이언트 블록 완전 삭제.
- DocumentService 미주입 시 `ToolError("DocumentService is not available...")` — 엔진은 호스트가 붙여주지 않은 서비스를 상상으로 부르지 않는다.
- `stages/s04_tool.py` / `stages/s08_act.py` — RAGSearchTool 생성 시 `state.metadata["services"].documents` 자동 주입.

**🔴 s02_history embedding_search (C)**:
- `stages/s02_history.py::_search_via_http` 완전 삭제. ServiceProvider.documents 미주입 시 `logger.info("embedding_search skipped")` 로 graceful skip.
- `get_service_url` / `_auth_headers` import 도 함께 제거.

**🔴 s06_context RAG fallback (D)**:
- `stages/s06_context.py::_fetch_rag` 메서드 전체 삭제 (약 50 라인). ServiceProvider.documents 미주입 시 `rag_context=""` 로 graceful skip.
- 엔진이 xgen-documents API 경로를 아는 유일한 남은 지점이었던 `/api/retrieval/documents/search` 스키마 호출이 완전히 사라짐.

**확인 — 엔진이 xgen-documents API 를 알지 않음**:
```
grep -r "/api/retrieval" xgen_harness/   # → 0 hits
grep -r "docs_url" xgen_harness/         # → 0 hits (provider / MCP 제외)
```

**기존 사용자 영향**:
- ServiceProvider.documents 를 등록하지 않은 독립 실행 환경에서는 RAG 검색 + embedding_search 가 graceful skip (에러 아님). xgen-workflow 이식측은 `XgenAdapter` 가 자동 주입하므로 영향 없음.
- RAGSearchTool 을 외부에서 직접 인스턴스화하던 코드는 `doc_service=` 를 넘겨야 실제 검색 가능 (기존 인자 호환 — doc_service 생략 시 조용히 실패만 함).

## [0.11.24] — 2026-04-22

### 🎯 감사 리포트 C+ 지적 9건 — 3대 기조 복원 (레거시 무침범 / 확장 끼워넣기 / 기여자 생태계)

외부 감사에서 C+ 등급으로 내린 "must-fix" 9건 중 실 코드 영향 8건 + 문서 규약 1건을 해소. 엔진 독립성·API 권한·확장 지점 공개 계약·에러 계층 실사용·이벤트 누수 차단을 v0.12 이전에 바로잡는 집중 릴리즈.

**🔴 엔진 독립성 복원 (#1)**:
- `adapters/resource_registry.py` — `from editor.node_composer import get_node_class_by_id` 직접 import 제거.
- 공식 확장 지점 신설 — `register_xgen_node_resolver(resolver_fn)` / `get_xgen_node_resolver()` / `XgenNodeResolver` 를 public API (`xgen_harness` top-level) 로 노출. 호스트(xgen-workflow) 측 어댑터가 부팅 시 resolver 를 주입하고, 엔진은 등록된 것만 호출.
- resolver 미등록 환경(독립 실행 / MCP 서버 / 테스트) 에서는 graceful 에러 문자열 반환 — `ResourceRegistry._call_xgen_node` 가 호스트 모듈 존재 여부에 의존하지 않음.

**🔴 API 권한 하드코딩 제거 (#2)**:
- `api/router.py::list_rag_collections` / `tools/rag_tool.py::_search_documents` / `stages/s02_history.py` / `stages/s06_context.py` / `integrations/xgen_services.py::DocumentService._auth_headers` — `"x-user-admin": "true"` 하드코딩 5곳 전부 `core.execution_context.get_xgen_auth_headers(user_id)` 공용 헬퍼로 수렴.
- 새 헬퍼의 기본값은 `admin=false` / `superuser=false` — ExecutionContext 에 **명시 주입된 경우에만** 권한 승격. 이전엔 ExecutionContext 미설정 시 모든 서비스 간 호출이 superuser 로 나가던 경로가 닫힘.

**🔴 ErrorEvent 원본 trace 노출 차단 (#3)**:
- `events/types.py::ErrorEvent` — `error_type` / `category` 필드 추가. `message` 에는 원본 `str(e)` 를 싣지 않도록 api/router.py 의 예외 핸들러를 `HarnessError` 기반 분기로 교체. 내부 트레이스는 `logger.exception` 으로만 기록.

**🔴 Gallery manifest 검증 (#4)**:
- `compile/gallery.py::discover_galleries` — 외부 패키지가 내려준 `manifest.package_name` / `manifest.dist_name` 을 정규식(`[A-Za-z_][A-Za-z0-9_]{0,63}` / PEP 503 호환) 으로 검증. 위반 시 warning + module_name 안전 폴백. path traversal · 임의 import 경로 주입 차단.

**🔴 AdvancedContextCompactor 공개 계약화 (#5)**:
- `stages/s06_context.py` 의 `_try_microcompact` / `_try_context_collapse` / `_try_autocompact` / `_try_cascade` 4 private 헬퍼를 `try_*` public 메서드로 승격. 외부 기여자가 `AdvancedContextCompactor` 서브클래싱 시 엔진이 공개한 메서드만 호출하도록 계약 명시.
- `stages/strategies/compactor_pd.py` docstring 재작성 — Stage private 헬퍼 의존 표기를 제거하고 공개 계약 설명으로 교체.

**🔴 errors 계층 실사용 (#7)**:
- `tools/rag_tool.py` — `raise RuntimeError(...)` 2건을 `ToolError(tool_name="rag_search")` 로 교체.
- `tools/mcp_client.py` — `MCPCallResult` dataclass 신설 (`ok`/`text`/`status`/`error_detail`). `call_tool_raw()` 가 구조화 결과 반환, `call_tool()` 은 하위 호환 str 래퍼. `MCPTool.execute` 가 `result.startswith("MCP call failed")` 문자열 매칭 대신 `r.ok` 로 분기.

**🔴 EventEmitter 누수·사용중단 API 제거 (#8)**:
- `events/emitter.py` — `subscribe()` 가 `Callable[[], None]` unsubscribe 토큰 반환. `unsubscribe(token)` · `clear_subscribers()` 추가.
- `_queue._queue` 내부 API 직접 접근 제거 → `_last_event` 추적으로 대체.
- 콜백 예외는 `logger.exception(...)` 으로 트레이스까지 기록 (삼킴 금지).

**🟡 DAG Orchestrator 역할 명시 (#6)**:
- `orchestrator/dag.py::DAGOrchestrator` docstring 재작성 — 3 진입점(`DAGOrchestrator.run()` / `MultiAgentExecutor` / `MultiAgentPlannerStage`)이 모두 `run()` 한 곳으로 수렴함을 명시. 실제 실행·병렬화·재시도·에러 복구 로직이 단일 지점임을 계약화.

**🟢 라이브러리 src 안 .md 제거 (#9)**:
- `xgen_harness_executor/docs/harness/00-PHILOSOPHY.md` / `NODE-WRAPPING.md` 를 `harness_xgen/docs/harness/` 로 이관. 엔진 레포 루트에는 README + CHANGELOG 만 남음 (PyPI 페이지용 허용 범위).

**이식측 반영 필요**:
- `xgen-workflow feature/harness-v2`: `harness.py::_stream_harness_pipeline` 에서 `XgenAdapter` 인스턴스화 직전 `register_xgen_node_resolver(editor.node_composer.get_node_class_by_id)` 1회 호출 추가. pyproject pin `xgen-harness>=0.11.24`.

**기존 사용자 영향**:
- `MCPClient.call_tool()` 시그니처는 str 반환 그대로 유지 (하위 호환).
- `ErrorEvent.message` 값이 원본 예외 문자열이 아닌 분류 메시지로 바뀜 — 콘솔에 원본 트레이스를 기대하던 디버깅 흐름은 `logger` (`harness.*`) 출력으로 전환 필요.
- `x-user-admin` 기본값 false 교정 — ExecutionContext 에 명시 주입 없이 xgen 내부 서비스를 호출하던 코드는 인증 실패할 수 있음 (정상 동작).

## [0.11.23] — 2026-04-22

### 🎯 options_source 전면 선언 → 이식 수동 매핑 자연 삭제 경로 활성화

사용자 지적 "하드코딩으로 쳐박는 거 / 이식을 안 하려고 발광" 에 대응. v0.11.21 에서 만든 이식 `register_option_source` 자동 역매핑이 엔진 stage_config 에 선언된 `options_source` 2 개만 의존 → 나머지 19 곳은 이식에서 여전히 수동 박혀 있었음. 이번 릴리즈에서 엔진이 UI 선택지 전체를 공식 선언해 수동 매핑이 drift 없이 제거되는 수렴 경로 확보.

**추가 선언 (엔진 stage_config)**:
- `s01_input.provider` — `options_source="providers"` (provider/model 선택)
- `s03_prompt.prompt_id` — `options_source="prompt-store"` (저장 템플릿)
- `s04_tool.custom_tools` — `options_source="tools"` (Custom API Tools)
- `s04_tool.cli_skills` — `options_source="local-cli-skills"` (Local CLI)
- `s04_tool.capabilities` — `options_source="capabilities"` (Capability 카탈로그)
- `s04_tool.node_tags` — `options_source="nodes-tags"` (노드 태그)
- `s06_context.ontology_collections` — `options_source="ontology-collections"`
- `s06_context.folders` — `options_source="folders"`
- `s06_context.files` — `options_source="files"`
- `s06_context.db_connections` — `options_source="db-connections"`

**효과**: 이식 `harness_options_registry.bootstrap_default_sources` 에서 spec.stage_id/stage_param_key 를 빈 문자열로 둬도 엔진 역매핑으로 자동 채워짐. drift 감지가 동작해 엔진/이식 간 불일치 시 경고 로그. 이식 측에서 11 라인 수동 박음을 안전하게 제거 가능 (v0.11.21 도입 자동 채움 infra 가 실제 효력 발휘).

**frontend**: 필드 추가만으로 자동 렌더 — 엔진이 UI 필드의 단일 진실 소스. 이식/프론트 코드 수정 0.

## [0.11.22] — 2026-04-22

### 🎯 확장성·연동성 있는 잔여 부채 해소 (사용자 "빠짐 없이" 후속)

v0.11.21 에서 유예했던 3 부채 (OpenAI output_tokens 환경차이 / PipelineState 도메인 분해 / bare except 전수) 를 일괄 해소. 모두 **확장 지점을 공개하는 방향** 으로 설계.

**🟢 Provider 확장점 — output_tokens 보정 (T2c 완전 해소)**:
- `providers/base.py::LLMProvider.count_tokens(text) -> (tokens, source)` 공식 확장점. 기본 구현은 chars/3 휴리스틱, `source="estimate_chars_3"`.
- `providers/openai.py::count_tokens` — tiktoken 설치 감지 시 `encoding_for_model` → `o200k_base` 순 자동 선택, `source="tiktoken"`. 미설치 환경은 base 로 폴백.
- `stages/s07_llm.py` — USAGE/STOP 이벤트 모두에서 output_tokens 수신 실패 시 `provider.count_tokens(result_text)` 로 보정. `state.metadata["output_tokens_sources"]` 에 출처(`usage`/`tiktoken`/`estimate_chars_3`) 남겨 관측자가 추정 여부 판단 가능.
- 외부 provider 가 자기 tokenizer 를 갖고 있으면 `count_tokens` 만 override → 자동 참여.

**🟢 PipelineState 도메인 그룹 (code review 안티패턴 #1 해소)**:
- `core/state.py` — `ToolGroup` / `ValidationGroup` dataclass 신설. `tool_definitions`/`tool_index`/`tool_schemas`/`pending_tool_calls`/`tool_results`/`tools_executed_count` 6 필드를 `state.tool.*` 로 재구성. `validation_score`/`validation_feedback`/`retry_count` 3 필드를 `state.validation.*` 로 재구성.
- 기존 경로 `state.tool_definitions` 등은 **property shim (getter+setter)** 으로 유지 → Stage 코드 0 라인 수정. `add_tool_result` / `flush_tool_results` 내부는 `state.tool.results`/`state.tool.pending_calls` 로 이주.
- 외부 기여자가 ToolGroup / ValidationGroup 을 서브클래싱해 캐시 정책 / 평가 메타를 확장 가능.

**🟢 s06_context 물리 분해 (code review 안티패턴 #2 1 단계)**:
- `stages/strategies/compactor_pd.py` 신설 — `AdvancedContextCompactor` ABC 와 4 구현체 (Microcompact/ContextCollapseOverlay/AutocompactLLM/Cascade) 이관. compactor.py 는 stateless (TokenBudget/SlidingWindow) 만 남김.
- `compactor.py` 가 4 구현체를 re-export 해 하위 호환 유지 → 기존 import 경로 무영향.

**🟢 bare except 전수 정리 (code review 안티패턴 #3)**:
- `tools/mcp_client.py` / `capabilities/matcher.py` / `adapters/node_adapters.py` (2건) / `adapters/resource_registry.py` / `adapters/xgen.py` / `orchestrator/dag.py` / `providers/openai.py` (3건) / `stages/s06_context.py` (2건) / `integrations/xgen_services.py` (3건) / `core/config.py` (2건) / `core/stage_config.py` (2건) / `core/strategy_resolver.py` (2건) / `core/pipeline.py` / `api/router.py` / `compile/snapshot.py` / `compile/wheel.py` (2건) / `compile/external_inputs.py` / `compile/gallery.py` (2건) / `compile/deps.py` (4건) — 전부 `except Exception as e:` + `logger.debug/warning` 또는 명시적 `_e` 바인딩으로 교체.
- `core/config.py` / `core/stage_config.py` / `compile/deps.py` / `capabilities/matcher.py` 에 모듈 로거 신규 추가.
- 남은 유일한 swallow 는 `events/emitter.py::EventEmitter.emit` 의 subscriber 콜백 (이미 `logger.exception` 적용됨).

**무침범**: 기존 Stage/이식/프론트 코드는 0 라인 수정. property shim + re-export 로 backward compat 유지.

## [0.11.21] — 2026-04-22

### 🎯 이식 연결선 + 확장성 보강 (사용자 10 블록 지시)

사용자 지시로 엔진 ↔ 이식 사이 "자동 반영 착시" 를 깨는 3 갭 (context_window forwarding / metrics.output_tokens / s06 compactor 외부 교체 불가) 을 일괄 해소. 더불어 code review B+ 지적의 타입 gate / bare except 정리 일부를 선행 반영.

**🔴 연결선 수정 (T2b / T2c / code review 1)**:
- `adapters/xgen.py` — HarnessConfig `context_window` / `thinking_enabled` / `thinking_budget_tokens` 를 top-level hc 에서 수신해 `config_kwargs` 로 전달. Pilot #10 의 "stage_params 우회" 근본 제거. 파싱 실패 시 기본값 유지.
- `core/config.py` — `HarnessConfig.from_workflow` 가 `context_window` 수신 (int 파싱 + 최소 1024 가드). `_safe_int` 유틸 신설.
- `stages/s07_llm.py` — `output_tokens` 누적 로직 수정. Anthropic `STOP` / OpenAI `USAGE` 이벤트 양쪽에서 **선착 1회만** 집계 → MetricsEvent 스트리밍 토큰 `0` 증상 해소.

**🔴 확장성 수정 (code review A → 확고화)**:
- `stages/strategies/compactor.py` — `AdvancedContextCompactor` ABC 신설. state/pd_stores/provider 접근이 필요한 전략을 위한 `apply(state, stage, budget_used, results)` 시그니처. 4 기본 구현체 추가:
  - `MicrocompactCompactor` (L3)
  - `ContextCollapseOverlayCompactor` (L4)
  - `AutocompactLLMCompactor` (L5)
  - `CascadeCompactor` (자동 에스컬레이션)
- `core/strategy_resolver.py` — 위 4 개를 `register_strategy("s06_context","compactor",…)` 슬롯에 등록. 외부 기여자가 `active_strategies` / `stage_params.s06_context.strategy` 로 자기 구현 swap 가능.
- `stages/s06_context.py` — execute() 진입 시 resolver 로 먼저 dispatch 시도. 등록된 Advanced 가 있으면 위임, 없으면 기존 inline if/elif fallback. cascade inline 블록을 `_try_cascade()` 메서드로 추출 (L3/L4/L5 와 대칭).

**🟡 코드 품질 (code review B+ 탈출)**:
- `pyproject.toml` — `[tool.mypy]` 추가 (relaxed baseline). `xgen_harness.core.*` 만 `check_untyped_defs=true` 로 선제 승격. `[tool.pytest.ini_options]` 추가.
- `stages/strategies/tool_router.py::CompositeToolRouter.list_available` — bare `except Exception: pass` → `logger.warning` 로 교체.
- `core/registry.py::_discover_plugin_stages` — 엔트리포인트 백엔드 실패 swallow → `logger.debug` 로 교체.
- `core/state.py::emit_verbose` — 관찰 이벤트 실패 swallow → `logger.debug` 로 교체.

**⏭ 다음 릴리즈로 유예 (사유 명시)**:
- `s06_context.py` 물리 분해 (`strategies/compactor_pd.py` 이관) — AdvancedContextCompactor 4 구현체가 stage helper 에 의존해 분해 범위가 크다. resolver 슬롯 외부 교체가 확보된 이상 필수가 아니라 응집도 이슈 → v0.11.22 후보.
- `PipelineState` 100+ 필드 도메인 분해 — 모든 Stage 의 필드 접근 경로 수정이 필요해 회귀 위험. 슬롯 단위 migration helper 설계 후 진입 필요 → v0.11.22 후보.
- bare `except` 전수 정리 — 대표 3 건 반영, 나머지는 호출자 계약 문서화 후 일괄 → v0.11.22.

**이식측 반영 필요 여부**: pyproject 의 `xgen-harness>=0.11.21` 핀 상향 1 줄. 연결 갭 3 종은 엔진만 수정으로 전파 가능 (`HarnessConfig` top-level 필드 전달 + s07 내부 집계).

## [0.11.20] — 2026-04-22

### 🎯 벤치 사이클 #19 — 코드 감사 후속 정리 (확장성·하드코딩·연동성)

Iter#25 감사 (B등급) 에서 지적된 중대 3 + 중간 5 항목 대응. 기능 변경 없이 품질·확장성·안전성 강화.

**🔴 중대 수정**:
- `stages/s03_prompt.py` **고유명사 하드코딩 제거**. 기존 collection 토큰 (`masahoe`, `krra`, `assort`, `x2bee`, `상품` 등 프로젝트 고유명사) 를 중립 명사 (`doc`/`report`/`regulation`/`product`/`stock` 등) 로 교체. **확장 지점** `citation_auto_doc_tokens` / `citation_auto_prod_tokens` 를 stage_param 으로 추가해 이식측이 도메인 특화 토큰 주입 가능.
- `stages/s06_context.py` **`_cascade_*_threshold_override` state.metadata 임시키 leak 방지**. `_try_microcompact/_try_context_collapse/_try_autocompact` 헬퍼 시그니처에 `threshold_override: float | None = None` 인자 추가. cascade 는 인자로 직접 전달. 예외 발생 시 잔존 키 누출 불가.
- `stages/s03_prompt.py` — auto-router 가 **실험적** 표시 명시 (stage_config description).

**🟡 중간 수정**:
- `stages/s07_llm.py` **force_tool_use circuit breaker 추가**: `state.loop_iteration >= 1` (2회차+) 부터 `tool_choice=auto` 로 격하. Iter#22 에서 발견한 `required` 무한 루프 방지. `tool_definitions` 없는데 `force_tool_choice` 설정되면 경고 로그.
- `providers/anthropic.py` **`tool_choice="none"` 처리**: Anthropic 공식 미지원이므로 `tools` 자체를 드롭해 OpenAI 의미론 맞춤 (기존 `{"type":"none"}` 은 400 에러).
- `providers/langchain_adapter.py` **tool_choice forward 추가**. `bind_tools(tool_choice=...)` 로 전달. LangChain < 0.2 호환 fallback (TypeError 시 warning + plain bind).
- `stages/s06_context.py::list_strategies()` **dispatcher 와 완전 동기**. `microcompact`/`context_collapse_overlay`/`autocompact_llm`/`cascade` 를 list 에 추가 (이전엔 token_budget/sliding_window 만).
- `stages/s06_context.py` **context_window AttributeError 방어**: `getattr(config, 'context_window', 200_000)` 로 HarnessConfig 이전 버전 호환.
- `stages/s06_context.py` **`chars_per_token` override** 노출. 영어/한국어 토큰 비율 조정 가능.
- `core/stage_config.py` — 신규 필드 3 종 (`citation_auto_doc_tokens` / `citation_auto_prod_tokens` / `chars_per_token`) 등록.

**무침범**: 모든 변경이 **기존 사용자 동작 보존**. 새 override 필드는 default 가 `[]` 또는 기존 값 → 기존 프로젝트 영향 0. 이식/프론트 무수정.

## [0.11.19] — 2026-04-22

### 🎯 벤치 사이클 #18 — tool_choice API (L3 Microcompact 실전 완결)

Iter#21 에서 `rag_ingestion_mode=tool_only` 로 system prompt RAG 주입 93% 축소 성공했으나, LLM 이 `tool_choice="auto"` 로 여전히 도구 호출 거부 (gpt-4o-mini 가 "정보 없음" 답변).

**해결**: Provider layer 에 `tool_choice` 파라미터 추가 + s04 에 `force_tool_use` 옵션.

**변경 (Provider 3종 동기화)**:
- `providers/base.py::chat()` 시그니처에 `tool_choice: Optional[str] = None` 추가
- `providers/openai.py`: body 에 `tool_choice` 전달 (auto/required/none/{name})
- `providers/anthropic.py`: Anthropic 형식 변환 (`{"type":"any"}` = required)
- `providers/langchain_adapter.py`: 시그니처 호환

**변경 (Stage / Config)**:
- `stages/s04_tool.py`: `force_tool_use` 파라미터 → `state.metadata["force_tool_choice"]="required"` 세팅
- `stages/s07_llm.py`: `state.metadata["force_tool_choice"]` 읽어 `provider.chat(tool_choice=...)` 전달
- `core/stage_config.py`: `force_tool_use` toggle 필드 등록

**L3 실전 활로 조합**:
```python
stage_params = {
    "s04_tool": {"rag_tool_mode": "tool", "force_tool_use": True},
    "s06_context": {"rag_ingestion_mode": "tool_only", "strategy": "microcompact",
                    "microcompact_threshold": 60, "microcompact_keep_recent": 3},
}
```

**무침범**: 기본값 모두 False/None → 기존 프로젝트 영향 없음. 이식/프론트 무변경 (stage_config 자동 렌더).

## [0.11.18] — 2026-04-22

### 🎯 벤치 사이클 #17 — rag_ingestion_mode (L3 Microcompact 실전 활로)

**문제**: Iter#20 에서 `rag_tool_mode=tool` 설정에도 LLM 이 rag_search 도구 호출 0회. 원인: s06 가 RAG 를 system prompt 에 여전히 주입 → LLM 이 prompt 만으로 답변 가능 → 도구 호출 불필요 판단.

**해결**: s06 에 `rag_ingestion_mode` 옵션 추가:
- `system_prompt` (기본, 하위 호환) — 기존 동작
- `tool_only` — system prompt 주입 skip. LLM 은 도구 호출로만 RAG 접근 → tool_result 누적 → L3 microcompact 발동 조건 충족
- `both` — 둘 다 (s07 에서 기존 `rag_tool_mode=both` 와 동등)

**자동 전환**: `rag_tool_mode=tool` 설정 시 `rag_ingestion_mode` 자동 `tool_only` 로 정정 (사용자 의도 존중).

**변경 파일**:
- `stages/s06_context.py`: 3-way 분기 + 자동 전환 로직
- `core/stage_config.py`: `rag_ingestion_mode` select 필드 등록
- `__init__.py`: `__version__` 0.11.18

**무침범**: 기본값이 `system_prompt` 라 기존 프로젝트 무수정. 이식/프론트 0 변경.

## [0.11.17] — 2026-04-22

### 🎯 벤치 사이클 #16 — Citation Auto-Router (실험 옵션) + L2/L3 필터 벤치 확장

**⚠ 실험적 기능 — auto 는 아직 prod 권장 아님**. s03 stage 가 s06 RAG 주입 **전** 실행되는 제약으로, RAG context 없이 collection 이름 / 파라미터 힌트만으로 판정하는 휴리스틱. Iter#17 검증에서 cross-stage param read 불가로 실제 효과 미미. 사용자가 시나리오별 명시 (`citation_mode=off` 또는 `strict`) 하는 것이 권장.

Iter#13, #14, #16 벤치에서 발견된 도메인 의존성 ("단답 도메인에선 off 최적, 문서 인용 도메인에선 strict 최적") 을 **자동 감지** 하는 휴리스틱 라우터 추가.

**신규**:
- `citation_mode = "auto"` (기본 off 에 추가 옵션)
- `_detect_citation_need(state)` — RAG context 의 문서형/상품형 신호 비교:
  - doc_score = `YYYY년도` 연도 패턴 × 2 + Document-Metadata/제목/작성자/마지막 수정자 × 3 + 파일 확장자 × 2
  - prod_score = `G#####` 상품 코드 + `원`/`₩`/`숫자.0` (상품 가격)
  - 결정: `doc_score > prod_score × 0.5` 그리고 `doc_score ≥ 3` 이면 strict, 아니면 off

**벤치 근거**:
- assort (product 도메인): prod_signal 다수 → auto → off ✓ (Iter#14 +332% 재현)
- krra (공공문서 도메인): 연도 + metadata 다수 → auto → strict ✓ (Iter#13 cite 1.94/turn 재현)

**변경**:
- `stages/s03_prompt.py` `_detect_citation_need()` 휴리스틱 추가 + `auto` 분기
- `core/stage_config.py` `citation_mode` options 에 `auto` 추가 + description
- `__init__.py` __version__ 0.11.17

**무침범**: 기존 off/enabled/strict 동작 그대로. auto 는 opt-in. 이식/프론트 무변경.

## [0.11.16] — 2026-04-21

### 🎯 벤치 사이클 #15 — Cascade 디폴트 튜닝 (Pilot #11 반증 반영)

Pilot #11 (tool-heavy, context_window=2400) 에서 **cascade 조기 발동(L3=70) 이 baseline 대비 답변 품질 -19% 악화**를 관측. 원인은 압력 낮은 상황에서도 L4/L5 가 메시지를 overlay/summary 로 교체 → LLM 이 원본 맥락 직접 접근 불가.

**수정**:
- `cascade_l3_threshold` 기본 70 → **80** (baseline token_budget 의 `compaction_threshold=80` 과 동기. 자연 compact 시점 이후에만 cascade 개입)
- `cascade_l4_threshold` 기본 85 → **90**
- `cascade_l5_threshold` 기본 95 → **97**
- 임계는 여전히 override 가능 (stage_params).

**의미**: cascade 는 "baseline 이 포기하는 지점부터 구원" 역할을 명시. 압력 낮을 때 간섭 금지.

**변경 파일**:
- `stages/s06_context.py` (디폴트 3 임계 + 근거 주석)
- `core/stage_config.py` (슬라이더 default + description)
- `__init__.py` (__version__ 0.11.16)

## [0.11.15] — 2026-04-21

### 🎯 벤치 사이클 #14 — Strategy Cascade (Claude Code Cascade 이식)

단일 전략 택1 방식을 넘어, 토큰 압력에 따라 **L3 → L4 → L5** 를 자동 선택하는 `cascade` 전략 추가.

**메커니즘**:
- `budget_used ≥ cascade_l3_threshold` (기본 70%) → **L3 microcompact** 선제 시도 (tool_result 교체, 경량)
- `budget_used ≥ cascade_l4_threshold` (기본 85%) → **L4 context_collapse_overlay** 추가 발동 (비파괴 overlay)
- `budget_used ≥ cascade_l5_threshold` (기본 95%) → **L5 autocompact_llm** 최후 수단 (child LLM 9-section)

한 턴당 L3 + (L4 또는 L5) 최대 2 단계. L5 는 회로 차단기 (연속 실패 3 회 시 스킵) 유지. `results["cascade_applied"]` 에 발동 계층 리스트 기록.

**변경**:
- `stages/s06_context.py`
  - 기존 3 전략 바디를 `_try_microcompact` / `_try_context_collapse` / `_try_autocompact` 헬퍼로 추출 (DRY)
  - `strategy = "cascade"` 분기 추가 (L3 + L4/L5 연쇄)
  - cascade 내부 임계 override 는 `state.metadata` 임시 키로 공용 헬퍼에 전달
- `core/stage_config.py`
  - `strategy` select 에 `cascade` 추가
  - 신규 슬라이더 3종: `cascade_l3_threshold` (70) / `cascade_l4_threshold` (85) / `cascade_l5_threshold` (95)
- `__init__.py` — `__version__` **0.11.15** (이전 0.11.1 박제 해소)

**무침범**: 기존 4 전략은 공개 dispatcher 에 그대로 유지. 이식 / 프론트 무변경.

---

## [0.11.14] — 2026-04-21

### 🎯 벤치 사이클 #13 — L3 Microcompact (Claude Code 5-Level 완전 이식)

Claude Code 5-Level Compression Pipeline 의 **L3 Microcompact** 구현. 이로써 L1/L3/L4/L5 를 본 엔진에 모두 반영. L3 는 L1 (tool result 저장소) 과 짝을 이뤄 "교체 정책" 담당.

**메커니즘**:
- 토큰 사용률이 `microcompact_threshold` (기본 75%) 초과 시 발동.
- messages 내 `tool_result` 블록 중 **최근 `keep_recent` 개 제외** 나머지를 placeholder 로 교체.
- 원본은 이미 v0.11.9 의 L1 이 `pd_stores["tool_result"]` 에 보존 중이므로 L3 는 단순 교체만 수행 (비파괴).
- placeholder 는 `fetch_pd(kind='tool_result', id=<tool_use_id>)` 호출 힌트 포함 → LLM 이 필요 시 복원.

**변경**:
- `stages/s06_context.py`: strategy 디스패치에 `microcompact` 분기 추가.
- `core/stage_config.py`:
  - `strategy` select 에 `microcompact` 옵션 추가 (L3 위치).
  - `microcompact_threshold` (slider 50~95, 기본 75).
  - `microcompact_keep_recent` (number 1~20, 기본 5).

**Claude Code 5-Level 완전 이식 완료**:
- L1 Tool Result Budget → v0.11.9 (Cycle #8) — 50KB preview + pd_stores 보존
- L2 History Snip → token_budget 전략에 내재
- L3 Microcompact → **v0.11.14 (Cycle #13)** — 오래된 tool_result 선별 교체
- L4 Context Collapse → v0.11.11 (Cycle #10) — 비파괴 overlay
- L5 Autocompact → v0.11.13 (Cycle #12) — child LLM 9-section summary

**다음**: prompt cache 연동한 L3 2 경로 (cache cold/hot) 는 anthropic API 캐시 도입 시점에 후속.

## [0.11.13] — 2026-04-21

### 🎯 벤치 사이클 #12 — L5 Autocompact (Claude Code child agent summarizer)

5-Level Compression Pipeline 의 **마지막 레벨 L5 Autocompact** 를 s06 에 구현. 87% 토큰 임계 초과 시 child LLM 이 대화 전체를 읽고 **9-section 구조화 summary** 생성, messages 를 `[first, summary, last_N]` 로 축소. 원본은 `pd_stores["history"]` 에 보존 (비파괴). 연속 실패 3 회 시 회로 차단.

**변경**:
- `stages/s06_context.py`:
  - strategy 디스패치에 `autocompact_llm` 분기 추가.
  - `_autocompact_summarize()` 헬퍼: 메시지 직렬화 → child LLM 호출 → 9-section 결과.
    - Sections: Primary Request / Key Decisions / Tools Used / Errors-Fixes / Files Touched / Data Mentioned / User Preferences / Open Issues / Next Steps.
    - state.provider 없으면 규칙 기반 fallback.
    - `state.metadata["autocompact_failures"]` 카운터로 회로 차단 (Claude Code 와 동일 패턴).
- `core/stage_config.py`:
  - `strategy` select 에 `autocompact_llm` 옵션 추가.
  - `autocompact_threshold` (slider 50~95, 기본 87).
  - `autocompact_keep_tail` (number 1~10, 기본 3).

**Claude Code 5-Level Compression Pipeline 완전 이식 완료**:
- L1 Tool Result Budget → v0.11.9 (Cycle #8)
- L2 History Snip → 내재 (compaction 일부)
- L3 Microcompact → 향후 (cache-aware 2 경로)
- L4 Context Collapse → v0.11.11 (Cycle #10)
- **L5 Autocompact → v0.11.13 (Cycle #12)**

본 세션 11 사이클로 하네스는 Claude Code 의 압축 파이프라인 핵심 4/5 를 갖춤. L3 는 prompt cache 연동 필요로 후속.

## [0.11.12] — 2026-04-21

### 🎯 벤치 사이클 #11 — RR2 Intent Routing (자동 metadata_filter)

Pilot #8 에서 **metadata_filter 가 하네스 tuned 를 legacy 수준으로 끌어올린 핵심 메커니즘** 임이 증명됨. 그러나 현재는 엔지니어가 수동으로 filter 값을 설정해야 함. 이 사이클은 쿼리에서 의도를 자동 분류해 filter 를 생성하는 경량 규칙 엔진을 s05 에 도입.

**변경**:
- `stages/s05_strategy.py`:
  - `execute()` 진입 시 `_apply_intent_routing(state)` 호출.
  - `stage_params.s05_strategy.intent_rules` = `[{"keywords":[...], "filter":{...}}, ...]` 선언 (dict 또는 JSON 문자열).
  - 매칭된 첫 rule 의 filter 를 `state.metadata["auto_metadata_filter"]` 에 저장.
- `stages/s06_context.py`:
  - metadata_filter 결정 우선순위 확장 — 기존 `stage_params.s06_context.metadata_filter` (명시) 우선, 없으면 `state.metadata["auto_metadata_filter"]` (intent routing) fallback.
- `core/stage_config.py`: s05 에 `intent_rules` (textarea) 필드 추가.

**영향**:
- 엔지니어가 사전에 규칙을 선언하면 쿼리마다 metadata_filter 수동 지정 불필요.
- LLM 기반 자동 분류는 후속 (비용 · 지연 고려). 현재 MVP 는 키워드 매칭.
- 명시 filter 가 항상 우선 → 회귀 안전.

**측정 계획 (Pilot #9)**: Pilot #8 의 `harness_tuned` 에서 metadata_filter 를 **intent_rules 자동 생성** 으로 바꿨을 때 동일 정답률 재현 여부.

## [0.11.11] — 2026-04-21

### 🎯 벤치 사이클 #10 — L4 Context Collapse Overlay (비파괴 압축)

Claude Code 5-Level Compression Pipeline 의 **Level 4 (Context Collapse)** 에 해당하는 비파괴 압축 전략을 s06 에 신설. 기존 `token_budget` / `sliding_window` 는 메시지를 **삭제** 하지만, `context_collapse_overlay` 는 중간 메시지를 `state.pd_stores["history"]` 에 **보존** 하고 messages 는 `[first, overlay_marker, *last_N]` 로 축소. 에이전트가 `fetch_pd(kind='history', id=...)` 로 복원 가능.

**변경**:
- `stages/s06_context.py`: strategy 디스패치에 `context_collapse_overlay` 분기 추가.
  - 임계 (기본 90%) 초과 + messages > keep_tail+1 시 발동.
  - old = messages[1:-keep_tail] 를 pd_stores["history"]["msg_<iter>_<idx>"] 로 이관.
  - overlay 마커 메시지로 교체 — 접힌 id 목록 + fetch_pd 힌트 포함.
  - results["context_collapsed"] = N 기록.
- `core/stage_config.py`:
  - s06 `strategy` 필드 신설 (select, `token_budget` / `sliding_window` / `context_collapse_overlay`).
  - `context_collapse_threshold` (slider 50~95, 기본 90).
  - `context_collapse_keep_tail` (number 1~10, 기본 3).

**영향**:
- 기본 전략 여전히 `token_budget` → 회귀 0.
- `context_collapse_overlay` 선택 시 긴 멀티턴 대화에서도 **원본 소실 없음**.
- Claude Code 의 "90% collapse → non-destructive view" 패턴과 동일 철학.

**한계**: 현재 overlay 는 단순 마커 (역할+첫 120자 미리보기). 다음 사이클에서 LLM child agent 로 9-section summary 생성 (Claude Code L5 Autocompact 패턴) 고려.

**테스트 경로**: 긴 대화 시뮬레이션으로 검증 (Pilot #7 예정).

## [0.11.10] — 2026-04-21

### 🎯 벤치 사이클 #9 — RAG Progressive Disclosure (pull-side)

v0.11.9 에서 도입한 PD 프레임워크의 두 번째 인스턴스. s06 이 RAG 청크 본문을 통째로 system_prompt 에 박던 것을, 선택적으로 "인덱스 한 줄만" 노출하고 본문은 `pd_stores["rag"]` 에 보관하도록 확장.

**변경**:
- `stages/s06_context.py`:
  - `rag_pd_mode` 파라미터 신설 (`eager` / `progressive`). 기본 `eager` → 완전한 회귀 안전.
  - `progressive` 모드: 각 청크를 `[i] id=<col>#<n> · src (score) · snippet…` 한 줄로만 system_prompt 에 배치. 본문은 `state.pd_store(kind="rag", id=<col>#<n>, full=chunk_text, meta={collection, index, source, score, chars})`.
  - 맨 아래에 `fetch_pd(kind='rag', id='<col>#1')` 호출 예시 힌트 삽입.
  - `rag_pd_snippet_size` 로 snippet 크기 조정 (기본 120 자).
- `core/stage_config.py`: s06 에 `rag_pd_mode` (select) / `rag_pd_snippet_size` (number) 필드 추가.

**영향**:
- `eager` 기본값으로 두어 기존 워크플로우 회귀 0.
- `progressive` 로 전환 시 RAG 10 청크 × 평균 500 자 = 5000 자 → 10 줄 × 120 자 = ~1200 자로 축소. 첫 루프 system_prompt 토큰 약 76% 절감.
- LLM 이 꼭 필요한 청크만 `fetch_pd` 로 pull → 불필요한 청크는 아예 LLM 이 보지 않음.
- 다중 루프 (s06 은 iteration>1 시 bypass) 구조에서는 인덱스는 유지되고 본문은 pd_stores 에 누적 → 후속 루프가 재사용.

**측정 계획 (Pilot #5)**: 동일 15 쿼리에 eager vs progressive 비교 — prompt_tokens / answer_quality / fetch_pd 실사용 빈도.

**참고**: Claude Code 의 Skills 3-Level PD (L1 YAML frontmatter → L2 body → L3 references) 와 유사 패턴. RAG 는 인덱스 + snippet (L1) → fetch_pd (L2) → 본문 삽입 (L3) 으로 매핑.

## [0.11.9] — 2026-04-21

### 🎯 벤치 사이클 #8 — Progressive Disclosure 프레임워크 + L1 Tool Result Budget

**배경**: Claude Code 소스 유출로 드러난 하네스 엔지니어링의 2 축 PD 구조 (pull-side revelation + push-side compaction) 를 본 엔진에도 도입. 본 릴리스는 공용 PD 저장소 + `fetch_pd` 빌트인 + 첫 인스턴스인 tool result L1 예산.

**변경**:

**공용 PD 프레임워크 — `core/state.py`**
- `PipelineState.pd_stores: dict[kind, dict[id, {preview, full, meta}]]` 추가.
- 헬퍼 3 종 — `pd_store(kind, id, preview, full, meta)` / `pd_fetch(kind, id)` / `pd_list(kind)`.
- kind 는 자유 문자열: `tool_result` / `rag` / `history` / `db_schema` / `gallery` 등 확장 가능.

**`fetch_pd` 빌트인 — `tools/builtin.py`**
- `FetchPDTool` 신설. state 참조를 보유하여 동일 턴에서 live 하게 pd_stores 조회.
- `fetch_pd(kind, id)` 로 원본 반환. id 생략 시 kind 의 가용 id 목록 반환.
- 에이전트가 preview 에서 원본 필요할 때 선택적 pull.

**자동 등록 — `stages/strategies/discovery.py`**
- `ProgressiveDiscovery` 가 `discover_tools` / `search_tools` 와 함께 `fetch_pd` 도 카탈로그에 추가.
- `state.metadata["tool_registry"]["fetch_pd"]` 등록으로 s08 디스패치 대상.

**L1 Tool Result Budget — `stages/s08_act.py`**
- 개별 결과가 `tool_result_preview_threshold` (기본 50000 자) 초과 시 preview 만 messages 에 넣고 원본을 `pd_stores["tool_result"][tool_use_id]` 에 보존.
- preview 끝에 `fetch_pd(kind='tool_result', id='...')` 힌트 삽입.
- 기존 누적 `result_budget` 2 차 방어는 유지 (여러 작은 결과 합 폭주 방지).
- Claude Code 5-Level Compression Pipeline 의 L1 (Tool Result Budget) 에 해당. **원본 소실 없음**.

**UI 노출 — `core/stage_config.py`**
- s08 에 `tool_result_preview_threshold` / `tool_result_preview_size` 필드 추가.
- `result_budget` 설명을 "2차 방어" 로 갱신. behavior 에 L1 패턴 명시.

**다음 계획**:
- 사이클 #9: RAG Progressive Disclosure (s06 청크를 pd_stores["rag"] 로). Push-side compaction 효과 측정.
- 사이클 #10: L4 Context Collapse overlay (토큰 압력 기반 자동 요약, 원본 비파괴 보존).

**참고**: 2026-04-21 Claude Code leak 분석 (5-Level Compression Pipeline) 에 기반.

## [0.11.8] — 2026-04-21

### 🎯 벤치 사이클 #7 — RR1: `metadata_filter` + 서버 단 rerank 요청 전파

Pilot #1/#2 분석에서 "정답 파일이 top-5 에 0%" 라는 구조적 병목이 드러났습니다. 이는 상위-k 후처리 도구 (score_threshold/rerank) 로는 해결 불가능하며, **검색 범위 자체를 좁혀야** 합니다. xgen-documents `DocumentSearchRequest` 를 재확인한 결과 `filter` / `rerank` / `rerank_top_k` 3 필드를 이미 request 단위로 지원하고 있었습니다 (하네스 엔진만 이를 모르고 있던 상태).

**변경**:
- `core/services.py`: `DocumentService.search` Protocol 에 `filter`, `rerank`, `rerank_top_k` 3 파라미터 추가. 기본값 유지로 회귀 안전.
- `integrations/xgen_services.py`: `XgenServiceProvider.search` payload 에 filter / rerank / rerank_top_k 조건부 포함 (None/False 면 생략).
- `stages/s06_context.py`: s06 이 `stage_params.s06_context.metadata_filter` 를 읽어 전달. dict 또는 JSON 문자열 (UI textarea) 모두 허용. reranker / rerank_top_k 는 이제 서버 단 rerank 요청으로 합류 (Cycle #2 의 client-side rerank 블록은 서버 미지원 구현체를 위한 폴백으로 유지).
- `core/stage_config.py`: s06_context 에 `metadata_filter` (textarea) UI 필드 추가. ConfigPanel 자동 노출. behavior 문구 갱신.

**영향**:
- `stage_params.s06_context.metadata_filter = {"file_name": "products.csv"}` 같은 필터로 검색 범위 제한 가능.
- 쿼리 의도를 미리 분류해 적절한 파일로 범위 좁히면 Pilot #1/#2 가 드러낸 "정답 파일 부재" 병목 해결 경로 확보.
- 기존 client-side rerank 는 폴백으로 보존 → ServiceProvider 구현체가 request-level rerank 미지원인 경우 대비.

**출처**: `bench/reports/2026-04-21-pilot-assort.md` 의 RR1 제안 + `bench/reports/2026-04-21-pilot-v2-and-infra.md` 의 expected_file_in_top5=0% 관찰.

## [0.11.7] — 2026-04-21

### 🎯 벤치 사이클 #6 — UI 노출 (ConfigPanel 자동 반영)

사이클 #1 ~ #5 에서 흡수한 엔진 파라미터가 실제 하네스 프론트 ConfigPanel 에서 보이도록 `stage_config.py` 필드 선언을 확장했습니다. 프론트는 이 엔진 메타데이터를 `/api/agentflow/harness/stages` 로 받아 자동 렌더링하므로, 프론트 레포 변경 없이 노출됩니다.

**변경 — `core/stage_config.py`**:

**s03_prompt** 에 필드 1 종 추가.
- `citation_mode` (select, `off/enabled/strict`). 기본 `off`. strict 모드 설명 명시.

**s06_context** 에 필드 4 종 추가.
- `score_threshold` (slider 0~1, 기본 0.0). precision 도구 성격 표기.
- `rerank_top_k` (number 1~20, 기본 4). 미설정 시 rag_top_k 사용.
- `reranker` (toggle, 기본 off). xgen-documents 리랭커 활성 스위치.
- `enhance_prompt` (textarea, 기본 ""). RAG 컨텍스트 뒤 이어 붙일 지시 프롬프트.

`behavior` 설명도 갱신하여 각 필드의 동작을 ConfigPanel 내부에 노출.

**영향**: 엔진 UI 표면이 레거시 `document_loaders` / `agents` 수준과 동등해집니다. 프론트는 `StageField` 스펙 기반 자동 렌더이므로 이식 / 프론트 레포 무변경. 엔진 단독 반영.

**검증**: `GET /api/agentflow/harness/stages` 응답에 신규 필드 노출 확인, ConfigField 컴포넌트가 select/slider/toggle/number/textarea 타입 모두 이미 지원.

**출처**: 본 세션 사이클 #1 ~ #5 의 UI 노출 마무리.

## [0.11.6] — 2026-04-21

### 🎯 벤치 사이클 #5 — H1: `response_filtering` 3 키 지원 (api_tool)

레거시 api_loader 노드의 응답 후처리 3 키 (`enable_response_filtering`, `response_filter_path`, `response_filter_fields`) 를 하네스 api_tool 어댑터에 흡수했습니다. 이전 하네스는 `response_filter` 단일 dot-path 만 지원하여, 경로 추출은 가능하지만 "추출된 list[dict] 에서 관심 필드만 남기기" 는 할 수 없었습니다. 결과 LLM 프롬프트에 불필요한 필드가 그대로 흘러들어 토큰 낭비가 발생했습니다.

**변경**:
- `adapters/node_adapters.py` `_build_api_tool`: spec 에 `enable_response_filtering` · `response_filter_path` · `response_filter_fields` 3 키 추가. 기존 `response_filter` 는 `response_filter_path` 의 별칭으로 그대로 동작 (하위 호환).
- `adapters/resource_registry.py` `_call_api_tool`: 응답 후처리 파이프라인 확장.
  1. `response_filter_path` 로 dot path 추출.
  2. `response_filter_fields` 가 있으면 list[dict] 의 각 dict 에서 해당 필드만 유지.
- `enable_response_filtering=True` 명시 또는 `response_filter_path` 가 비어있지 않으면 필터 활성 (기본 off).

**영향**: API 도구 응답에서 관심 필드만 남겨 LLM 프롬프트 토큰 비용 절감 + 노이즈 감소. 기존 `response_filter` 를 쓰던 워크플로우는 그대로 동작.

**출처**: `bench/reports/2026-04-21-gap-analysis.md` H1.

## [0.11.5] — 2026-04-21

### 🎯 벤치 사이클 #4 — H4: `enhance_prompt` (s06)

레거시 `document_loaders.enhance_prompt` 에 대응하는 설정을 s06_context 에 추가했습니다. RAG 컨텍스트가 system_prompt 에 주입된 뒤, 사용자가 지정한 "응답 향상 지시" 를 덧붙입니다.

**변경**:
- `stages/s06_context.py`: RAG 컨텍스트 주입 블록 뒤에 `enhance_prompt` 처리 추가.
  - `stage_params.s06_context.enhance_prompt` 값이 비어있지 않으면 `<enhance_prompt>...</enhance_prompt>` 블록으로 이어 붙임.
  - Stage 출력 `results["enhance_prompt_applied"] = True` 기록.

**영향**: RAG 기반 응답의 톤·형식·관점을 쿼리 단위로 유도할 수 있습니다. 기본값 빈 문자열이므로 회귀 없음.

**출처**: `bench/reports/2026-04-21-gap-analysis.md` H4.

## [0.11.4] — 2026-04-21

### 🎯 벤치 사이클 #3 — H3: `citation_mode` (off / enabled / strict)

레거시 `agents` 노드의 `strict_citation` (bool) 에 대응하는 설정을 하네스 s03_prompt 에 추가했습니다. 기존 `citation_enabled` 는 on/off 2 값이라 "인용은 권장" 과 "인용에 없는 정보는 답하지 말 것" 을 구분할 수 없었습니다.

**변경**:
- `stages/s03_prompt.py`: `citation_mode` 파라미터 신설 (`off` / `enabled` / `strict`).
  - `off`: 인용 지시 없음.
  - `enabled`: 기존 `citation_enabled=True` 와 동일한 `[DOC_n]` 인용 형식 권장.
  - `strict`: enabled 규칙 + `<grounding_rules>` 블록 추가 — 제공 문서 밖 정보는 답하지 않고 명시적으로 "찾을 수 없다" 고 응답하도록 강제.
- `citation_enabled` 는 하위 호환으로 유지. `citation_mode` 가 없으면 `citation_enabled=True → enabled`, `False → off`.

**영향**: `stage_params.s03_prompt.citation_mode="strict"` 로 두면 RAG 문서 바깥의 환각 가능성이 감소합니다. 기본값은 `citation_enabled` 에서 유도되므로 회귀 없음.

**출처**: `bench/reports/2026-04-21-gap-analysis.md` H3.

## [0.11.3] — 2026-04-21

### 🎯 벤치 사이클 #2 — H2-b: rerank 호출 Protocol 정합 + `rerank_top_k` 신설

s06_context 의 rerank 호출이 `DocumentService.rerank` Protocol 과 불일치였던 것을 교정했습니다. 기존 구현은 `rerank(query=..., text=rag_context, provider=reranker_name)` 로 호출했으나 Protocol 은 `rerank(query, documents: list[str], top_k, user_id)` 을 받고 `[{"index", "score"}]` 를 반환합니다. 이로 인해 ServiceProvider 경로에서 rerank 호출이 `TypeError` 로 실패한 뒤 warning 만 남기고 원본 rag_context 로 폴백되는 상태였습니다. xgen-documents 의 `/embedding/reranker/rerank` 엔드포인트 스펙도 확인하여 실제 서버 계약에 맞췄습니다 (reranker provider 는 서버 기동 시 설정, 요청 단위에서 받지 않음).

**변경**:
- `stages/s06_context.py` rerank 블록 재작성.
  - `rag_context` 를 2 줄 이상 공백 구분자로 청크 분리 후 `documents: list[str]` 로 전달.
  - 반환된 `{"index", "score"}` 배열로 청크 재정렬.
  - `rerank_top_k` 파라미터 신설. 기본값은 `rag_top_k` 와 동일.
  - `reranker` 파라미터는 "rerank 활성 토글" 로 의미 축소 (truthy 시 rerank 호출). 실제 reranker provider 는 xgen-documents 서버 설정을 따름.

**영향**:
- `stage_params.s06_context.reranker` 를 `"vllm"` 등의 값으로 두면 지금부터 실제로 rerank 가 동작합니다 (이전엔 silent fail).
- `stage_params.s06_context.rerank_top_k` 로 재순위 상위 k 개 제한 가능.
- `results["reranked"]` / `results["rerank_top_k"]` 가 Stage 출력에 기록됩니다.

**출처**: `bench/reports/2026-04-21-gap-analysis.md` H2-b + 호출 불일치 회귀 교정.

## [0.11.2] — 2026-04-21

### 🎯 벤치 사이클 #1 — H2-a: `score_threshold` end-to-end 연결

레거시 `document_loaders` 노드에 있는 `score_threshold` 파라미터가 하네스 s06_context 에서 **읽기만 하고 실제 검색에는 전달되지 않던** 구간을 수정했습니다. `XgenServiceProvider.search` 는 payload 에 `score_threshold: 0.0` 을 하드코딩하고 있었고, `DocumentService.search` Protocol 에도 해당 파라미터가 없어 호출 지점에서 의도한 임계가 묵살되었습니다.

**변경**:
- `core/services.py` `DocumentService.search` 시그니처에 `score_threshold: float = 0.0` 추가.
- `integrations/xgen_services.py` `XgenServiceProvider.search` 가 `score_threshold` 를 인자로 받아 payload 에 전달 (하드코딩 제거).
- `stages/s06_context.py` 가 정상 경로(`doc_service.search`) 호출 시 `self.get_param("score_threshold", state, 0.0)` 을 읽어 전달.

**영향**: `stage_params.s06_context.score_threshold` 를 실제로 설정하면 xgen-documents 검색 단계에서 유사도 필터가 적용됩니다. 기본값 0.0 이므로 기존 동작에는 변화 없음 (회귀 안전).

**출처**: `bench/reports/2026-04-21-gap-analysis.md` 의 H2.

## [0.11.1] — 2026-04-21

### 🧹 Strategy 품격 — 하드코딩 제거 / 진짜 구현체 / 공개 API 일관성

v0.11.0 까지 `ThresholdDecide`/`AlwaysPassDecide` 는 이름표만 있고 실제 판단은 `DecideStage.execute()` 안에 if/else 로 박혀있었다. `ContentGuard` 는 `return True` 만 하는 빈 껍데기. `ParallelToolExecutor` 는 `__all__` 에 없어 외부 import 시 실패.

**수정**:

1. **DecideStrategy 인터페이스 신설** (`stages/interfaces.py`) — `async def decide(state, params) -> dict` 계약
2. **ThresholdDecide 진짜 구현** — Guard 체인 / pending_tool_calls / validation_score / last_assistant_text 판단 전부 Strategy 내부로 이관. 루프 상수(LOOP_COMPLETE 등) 는 `strategies/_decide.py` 가 원본, `s10_decide.py` 는 re-export.
3. **AlwaysPassDecide 도 진짜 `decide()` 메서드** — 이름 문자열 비교로 분기하지 않음
4. **ContentGuard 진짜 구현** — 사용자 정의 정규식 매칭 + 옵션 PII 감지 (이메일/한국 휴대폰/주민번호/카드번호) + 검사 대상 선택 (input/output/both). 기본값(패턴 없음 + PII off) 에서는 항상 통과 → 하위 호환.
5. **create_guard_chain 확장** — `content_blocked_patterns` / `content_detect_pii` / `content_check_target` 3개 옵션 전달
6. **DecideStage.execute() 완전 위임** — Strategy resolve → params 수집 → `await strategy.decide(state, params)` 반환. 내부 분기 0줄. 예외 시 `LOOP_ERROR` 로 좀비 방지.
7. **ParallelToolExecutor 공개 API 승격** — `__all__` 에 추가, `ThresholdDecide` / `AlwaysPassDecide` 도 최상위 export

**영향**:
- 기존 `threshold` / `always_pass` 선택은 자동으로 새 구현체 사용 (slot/impl 이름 동일)
- `from xgen_harness.stages.s10_decide import LOOP_COMPLETE` 하던 외부 코드 계속 동작 (re-export)
- 사용자가 stage_params 에 content_* 설정 안 하면 ContentGuard 항상 통과 → 행동 변화 없음

**검증**:
- `test_compile.py` 12/12 PASS
- `test_capabilities.py` 33/34 PASS (나머지 1건은 pytest-asyncio 설정 이슈, 기존부터 실패)
- 새 smoke: ThresholdDecide 가 텍스트 응답 / pending tool / 점수 미달 / 재시도 한도 4 케이스 전부 올바른 decision 반환

---

## [0.11.0] — 2026-04-20

### ⚡ BREAKING + BACKWARDS COMPATIBLE — Stage ID 리네이밍

Stage 내부 id 7개를 더 직관적인 이름으로 변경. display name 도 12개 전부 정리. 기존 저장된 워크플로우와 외부 갤러리 wheel 은 **alias 레이어**로 계속 동작 (v0.12+ 에서 구 id 제거 예정).

**변경 매핑**:
- `s02_memory` → `s02_history`
- `s03_system_prompt` → `s03_prompt`
- `s04_tool_index` → `s04_tool`
- `s05_plan` → `s05_strategy`
- `s08_execute` → `s08_act`
- `s09_validate` → `s09_judge`
- `s12_complete` → `s12_finalize`

유지: `s01_input`, `s06_context`, `s07_llm`, `s10_decide`, `s11_save`

**새 display name** (EN/KO):
- Input/입력, History/이력, Prompt/프롬프트, Tool/도구, Strategy/전략, Context/컨텍스트,
  LLM/LLM, Act/실행, Judge/판정, Decide/결정, Save/저장, Finalize/마무리

**구현**:
- `core/stage_config.py` — `STAGE_ID_ALIASES` dict + `canonical_stage_id(sid)` helper
- `core/stage_config.py::get_stage_config` — 입력 sid 를 canonical 로 정규화
- `core/config.py::HarnessConfig.from_workflow` — `disabled_stages` / `stage_params` /
  `active_strategies` / `strategy_variants` 키를 전부 alias 해석 → 구 id 저장된
  워크플로우도 자동으로 새 id 로 로드
- `stages/` 7개 파일 rename + 각 클래스 `stage_id` property 갱신
- `core/stage.py::STAGE_DISPLAY_NAMES` + `STAGE_DISPLAY_NAMES_KO` 새 이름

**테스트**: `test_compile.py` 26/26 PASS + alias 하위호환 단위 PASS.

---

## [0.10.4] — 2026-04-20

### Added — Strategy Variants (디폴트 건드리지 않고 복사해서 v2)

외부 작업자·사용자가 기본 Strategy 를 수정하지 않고 "이름만 다른 복사본" 을 만들어 쓸 수 있게. 기존 `register_strategy` 는 파이썬 패키지 레벨 등록이라 런타임/워크플로우별 커스터마이즈가 불가능했음. variants 는 **HarnessConfig 필드** 로 선언되며 실행 시 `resolve_strategy` 가 base impl 로 태우고 params 를 configure 에 병합.

- `core/config.py::HarnessConfig.strategy_variants` 필드 추가
  - 형식: `{stage_id: [{"name": "progressive_v2", "base": "progressive_3level", "params": {...}, "label": "내 커스텀"}]}`
  - `from_workflow` 가 harness_config dict 에서 직접 파싱, 미선언 시 빈 dict (하위호환)
- `core/stage.py::Stage.resolve_strategy` — `active_strategies[stage_id]` 값이 variant 이름이면:
  1) `variant.base` 로 Strategy 클래스 조회 (기본 레지스트리 재사용)
  2) `strategy_config` 에 `variant.params` 병합 → `cls.configure(config)` 호출
  3) variant 가 없으면 기존 경로 그대로 (완전한 하위호환)
- 엔드포인트/라이브러리 API 변경 없음. 이식측·프론트는 payload 에 `strategy_variants` 키만 추가하면 됨.

### Added — DAG Orchestrator 외부 노출 (이식측 관점)

엔진 `orchestrator/dag.py::DAGOrchestrator` 는 v0.8.x 에 이미 있었지만 s05 multi_agent 내부에서만 쓰였음. 이식측 `/harness/dag/execute/stream` 엔드포인트가 이걸 외부에 노출해 **여러 하네스를 DAG 로 엮어 실행** 가능. 엔진 자체 변경은 없고 **외부 사용 패턴을 문서화**하는 의미.

기존 v0.10.3 아키텍처 계승 (s01 축소, provider lazy-init, 컴파일러 drift-free).

---

## [0.10.3] — 2026-04-20

### Changed — s01 입력 스테이지 철학 재정립 (하드코딩 연동 제거)

0.9.x 이후 s01 에서 provider / model / temperature 를 아직 stage_param 필드로 노출하고 있었다. 철학은 "s01 = 사용자 입력 정규화 전용, LLM 설정은 s07 또는 HarnessConfig top-level" 인데 UI 가 s01 을 클릭해서 Provider 를 고르도록 유도하고 있었음 — 사용자가 지적한 **하드코딩 연동**.

- `core/stage_config.py::STAGE_CONFIGS["s01_input"]` — `fields` 에서 `provider` / `model` / `temperature` 3개 제거. description/behavior 도 "LLM 프로바이더 초기화" → "사용자 입력 정규화" 로 재표기.
- `stages/s01_input.py::InputStage.execute` — `config.provider = self.get_param(...)` / `model` / `temperature` 재대입 라인 제거. 결과 dict 에서도 provider/model/temperature 기록 제거. s01 은 이제 해당 값을 **읽지도 쓰지도 않는다**.
- HarnessConfig top-level 의 provider/model/temperature 가 단일 진실 소스. s07 이 lazy-init 시 직접 참조.

결과: 사용자 관점에서 "s01 = 입력만", "provider/model = 전역 Harness Config 드로어" 로 자연스러운 위치. 이식측 프론트는 `config-panel.tsx` 에 Provider/Model/Temperature 입력 UI 를 새로 올리고 `stage-detail-panel.tsx` 의 s01 하드코딩 리다이렉트를 제거.

---

## [0.10.2] — 2026-04-20

### Changed — drift-free 연동 (프리픽스 재조합 제거)

0.10.1 까지는 이식측/프론트가 `xgen-gallery-<name>` / `xgen_gallery_<name>` 프리픽스를 스스로 재조합했다. 엔진 규약(`GALLERY_DIST_PREFIX`)이 바뀌면 드리프트 — 이식측과 프론트가 따라올 수 없음. 엔진이 확정값을 내려주는 방향으로 전환.

- **compile 산출 wheel 의 `manifest()` 반환값에 `dist_name` / `package_name` 추가** — UI 가 재조합할 필요 없이 엔진이 확정한 이름 그대로 사용.
- **`InstalledGallery` 데이터클래스에 `dist_name` / `package_name` 필드 추가** — manifest 가 확정값을 내려줬다면 우선, 없으면 `module_name` 에서 파생. `discover_galleries()` 결과가 그대로 "pip install 명령 렌더" 에 쓸 수 있음.
- **`GALLERY_DIST_PREFIX` / `GALLERY_PKG_PREFIX` public export** — 외부 소비자가 정말 필요하면 상수로 import, 하드코딩 금지.

결과: 이식측/프론트 코드에서 `"xgen-gallery-" + name` 같은 문자열 조합이 사라진다. 엔진이 명명 규약을 바꿔도 양쪽이 자동 반영.

### Tests

- 17/17 → 26/26 확장 유지. 설치·discover 테스트 2개에 `dist_name`/`package_name` 검증 assertion 추가.

---

## [0.10.1] — 2026-04-20

### Added — 컴파일러 단계 5·6 + xgen-gallery 컨벤션 통합

0.10.0 (MVP wheel) 위에 MCP stdio 서버 래퍼, 갤러리 discover, PlateerLab/xgen-gallery React 컴포넌트 규약 자동 생성 3개를 한 번에 얹었다. 고결한 구조의 다른 축들 — MCP 불러오기, 설치 갤러리 자동 발견, UI 호환성 — 이 다 닫힌다.

- **단계 5 — MCP stdio 서버 래퍼** (`xgen_harness/compile/mcp_server.py`)
  - 컴파일 wheel 의 CLI 에 `serve-mcp` 서브커맨드 추가. `pip install 'xgen-gallery-<name>[mcp]'` 한 줄로 MCP 모드 활성화.
  - 노출 tool 1개 — `run_workflow(input: string, overrides?: object)`. input schema 는 external_inputs 덮어쓰기 지원.
  - `mcp` 패키지 없을 땐 친절한 `MCPNotInstalledError` 메시지 (optional extra 기반).
  - Claude Desktop/Code/Cline 의 MCP 서버로 `{"command": "xgen-gallery-foo", "args": ["serve-mcp"]}` 한 줄 연결 가능.
- **단계 6 — 갤러리 discover** (`xgen_harness/compile/gallery.py`)
  - `discover_galleries()` — `entry_points("xgen_harness.galleries")` 스캔해 설치된 갤러리 카탈로그 즉시 반환.
  - `get_gallery(name)` 로 단건 조회. PyPI / 사내 인덱스 / 로컬 wheel 어느 채널로 설치됐든 동일 발견 — 3채널 불가지론.
  - 개별 갤러리 로드 실패는 skip + 경고 콜백(`on_error`) — 하나가 깨져도 카탈로그는 살아있음.
- **xgen-gallery 컨벤션 자동 생성** — compile 시 소스 트리 루트에 `.xgen-gallery/demo.json` + `examples/quickstart.py` 자동 생성 (`include_gallery_hints=True` 기본). PlateerLab/xgen-gallery React 컴포넌트가 이 규약으로 데모 탭 자동 렌더 — 별도 설정 없이 GitHub push 만으로 UI 노출.
- **하드코딩 제거 — 유동성 확보**:
  - `deps.py` 의 빌트인 버전 핀을 모듈 상수로 노출: `MCP_MIN_VERSION = ">=0.9"`, `QDRANT_MIN_VERSION = ">=1.7"`. 외부에서 `xgen_harness.compile.deps.MCP_MIN_VERSION = ">=1.0"` 한 줄 override.
  - `wheel.py` 의 `requires-python` 을 엔진 자신의 `pyproject.toml` 에서 **동적으로 읽어서** 상속. 엔진이 Python 버전 올리면 컴파일 산출물도 자동 반영. `compile_workflow(..., requires_python=">=3.11")` 로 명시적 override 가능.
  - `snapshot.py` 의 stale `">=0.9.3"` 하드코딩 fallback 제거. 엔진 `__version__` 못 읽을 때 unbounded("") 반환.

### Added — 이식측 `/harness/galleries`

`controller/workflow/endpoints/harness.py` 에 `GET /harness/galleries` 추가. `xgen_harness.discover_galleries(on_error=...)` 위임 — 프론트 "설치된 갤러리 카드 뷰" / capability "Gallery" 카테고리 렌더 소스.

### Tests

- `test_compile.py` 17 → 26 PASS (+9). MCP 래퍼 3, 갤러리 discover 2, requires_python 2, xgen-gallery 규약 2.

---

## [0.10.0] — 2026-04-20

### Added — 워크플로우 컴파일러 (MVP)

하네스 워크플로우 하나를 `xgen.compile(wf)` 한 줄로 `pip install` 가능한 wheel 로 변환한다. 받는 쪽은 `pip install xgen-gallery-<name>` 후 `await gallery.run("입력")` 한 줄. 받는 패키지는 엔진(`xgen-harness`) 만으로 실행되어 이식 레이어(`xgen-workflow`) 와 독립.

- **신규 모듈 `xgen_harness.compile`** — 4 서브모듈.
  - `external_inputs.py` — 선언(A) + `${VAR}` 자동 스캔(B) 병행. `PROVIDER_API_KEY_MAP` 레지스트리 경유로 secret 타입 자동 확정. 키 suffix 힌트(`_URL/_ENDPOINT` → url)로 스캔 품질 보강.
  - `snapshot.py` — `WorkflowSnapshot` 데이터클래스. `compile_version=1.0`, JSON 직렬화 + validate(PEP 503/440).
  - `deps.py` — `DependencyResolver` + `register_dependency_rule()` 외부 확장 통로. 빌트인 룰 5종 (xgen-harness / provider SDK / MCP / RAG / capability extras) 전부 레지스트리 항목으로. 외부 패키지가 `register_dependency_rule("my_vendor", ...)` 한 줄로 자기 의존성 선언.
  - `wheel.py` — 소스 트리 생성 + `python -m build --no-isolation` 호출. 순수 문자열 템플릿(Jinja 의존성 회피). `WheelBuildResult` 반환.
- **`HarnessConfig.external_inputs` 필드 추가** — `to_dict`/`from_dict` 자동 순회로 직렬화 무료. `from_workflow` 에도 한 줄 통과.
- **공개 API**: `xgen_harness.compile(...)` / `compile_workflow(...)` / `build_wheel(snapshot, ...)` / `load_snapshot(path)`.
- **산출 wheel 구조**:
  - `xgen_gallery_<name>/` 에 `snapshot.json` + `env.example` + `cli.py` + `__init__.py` 탑재.
  - `[project.scripts]` 로 `xgen-gallery-<name>` CLI (`run` / `info` 서브커맨드) 제공.
  - `[project.entry-points."xgen_harness.galleries"]` 로 설치 갤러리 자동 발견 — 단계 6 대비.

### Verified — 로컬 + 폐쇄망 실전 검증

- 단위/통합 테스트 17/17 PASS (`test_compile.py`).
- 실제 워크플로우 컴파일 → 격리 venv `pip install` → `manifest()` / `arun()` import 확인.
- `${OPENAI_API_KEY}` / `${MY_API_URL}` 자동 스캔 → secret/url 타입 확정 + env.example 자동 생성 확인.
- RAG collections 있는 워크플로우에 `qdrant-client>=1.7` 자동 포함 확인.
- **폐쇄망 시나리오**: `pip download` 로 transitive wheel 8개 확보 → `pip install --no-index --find-links wheelhouse/` 로 PyPI 완전 차단 상태에서 설치 + 실행 성공.

### 고결한 구조 원칙 (설계 의도)

- **노드/갤러리 변경 = 엔진 재배포 금지**. `pip install xgen-gallery-*` 한 줄로 즉시 추가/교체.
- **3채널 배포** — 공개 PyPI + 사내 인덱스 + 로컬 wheel 모두 동일 산출물.
- **엔진 독립 실행** — 컴파일 산출 wheel 은 `xgen-harness` 만 import 하면 돌아감 (이식 레이어 거치지 않음).
- **하드코딩 제거**: provider/env/dep/capability 모두 레지스트리 기반. 외부 기여자가 엔진 소스 수정 없이 자기 리소스 주입.

### Not Yet

- 단계 5 (MCP stdio 서버 래퍼) — 다음 릴리스.
- 단계 6 (`xgen-gallery` 중앙 메타 규약) — entry_points 채널만 선결 연결, 인덱스는 후속.
- 단계 7 (UI `/harness/compile` 엔드포인트 + 프론트 Deploy 모달) — 이식측 작업.

상세 설계: `docs/harness/2026-04-20-workflow-compiler.md`.

---

## [0.9.3] — 2026-04-20

### Fixed — `__version__` 문자열 누락

`pyproject.toml` 은 0.9.2 로 올라갔지만 `xgen_harness/__init__.py` 의 `__version__` 이 0.8.38 에 머물러 있어 `import xgen_harness; xgen_harness.__version__` 가 구 버전을 반환하던 문제. v0.9.2 이전 릴리스(0.8.38~0.9.1)에서도 동일하게 놓쳤던 이슈를 여기서 잡음. 기능 변경 없음 — 0.9.2 와 동일한 엔진 + 버전 메타데이터만 정상화.

---

## [0.9.2] — 2026-04-20

### Changed — Stage 책임 재정의 Phase 2 (v0.9.0 후속)

v0.9.0 에서 선언한 철학을 실제 코드까지 관철. `s01_input` 의 backward-compat 로직을 완전히 제거하고, `PROVIDER_CONTEXT_LIMITS` 같은 stage-level 하드코딩 딕셔너리를 providers 레지스트리로 이관.

- **`s01_input` 축소 완료** — PHILOSOPHY §2 선언대로 "입력 정규화" 단일 책임.
  - 제거: provider 생성 / API key 해석 / base_url 해석 / MCP 디스커버리 / workflow_data 스캔.
  - 유지: 입력 검증 / 첨부 파일 → content block / 첫 user 메시지 push / 복잡도 분류.
  - `provider / model / temperature` 는 `config` 에만 기록 — 실제 해석은 `s07_llm._lazy_init_provider()`.
- **`s04_tool_index` — MCP workflow_data fallback 흡수**. `stage_params.mcp_sessions` 가 비어 있으면 `_collect_mcp_sessions_from_workflow(workflow_data)` 로 `mcp/*` 노드를 자동 스캔. (이전엔 s01 이 하던 일). `should_bypass` 도 동일 로직으로 업데이트.
- **`PROVIDER_CONTEXT_LIMITS` → providers 레지스트리**. 하드코딩 딕셔너리를 `s07_llm.py` 에서 제거하고 `providers/__init__.py` 로 이동. 외부 provider 플러그인이 `register_provider(name, cls, context_limit=…)` 한 번으로 UI 드롭다운 / API key env / 컨텍스트 한도까지 선언 가능.
  - 신설 헬퍼: `get_context_limit(provider)` — 레지스트리 → `XGEN_HARNESS_DEFAULT_CONTEXT_LIMIT` env → `DEFAULT_CONTEXT_LIMIT_CHARS(500_000)` 순 조회.
  - `register_provider` 시그니처 확장: `default_model`, `models`, `api_key_env`, `context_limit` 선언형 인자 (모두 optional). 엔진 소스 수정 0 으로 새 provider 통합.

### Docs

- **`docs/harness/EXTENSION_POINTS.md` §1 Stage** — `register_stage` 시그니처 + Stage 클래스 계약(order/phase/execute/list_strategies) + 런타임 등록 예제 정리. 외부 기여자가 entry_points 와 런타임 API 중 선택 가능.
- **`docs/harness/EXECUTOR-ENDPOINTS.md` 상단 고지** — "엔진은 이 URL 을 강제하지 않는다. `/api/agentflow/harness/...` 는 xgen-workflow 이식 레이어의 현재 규약일 뿐. 외부 이식측은 자사 규약으로 자유롭게 마운트 가능." 엔진 / 이식측 분리 명문화.
- **`docs/harness/NODE-WRAPPING.md` 신설** — 캔버스 노드가 `_XgenNodeRef` 디스패치 타입으로 말려서 LLM 도구가 되는 전체 알고리즘 (bootstrap / 6-step build / 3 invariants / 실제 예제 / 외부 확장 경로).

### PHILOSOPHY 정합성

- v0.9.0 문서 §2 "s01 비담당" 항목이 이제 실제 코드와 일치. "deprecated but functional" 상태 해소.
- "Stage 내부의 프로바이더별 상수 딕셔너리" 패턴을 providers 레지스트리로 일원화. 앞으로 Stage 에 `PROVIDER_*` prefix 의 dict 추가 금지.

### Changed — 하드코딩된 `"anthropic"` 기본값 전면 제거

엔진 곳곳에 박혀있던 `provider="anthropic"` 기본값을 `providers.get_default_provider()` 런타임 해석으로 교체. 외부 기여자가 OpenAI / Bedrock / vLLM 기반 환경을 기본으로 쓸 때 엔진 소스 수정 없이 `XGEN_HARNESS_DEFAULT_PROVIDER` env 한 줄로 전역 기본값 변경 가능.

- 영향 파일: `core/config.HarnessConfig` (`__post_init__` 에서 런타임 해석), `core/session.HarnessSession.from_dict`, `core/builder.PipelineBuilder`, `adapters/xgen.XgenAdapter`, `api/router` (ExecuteRequest / OrchestratorRequest / SSE 분기), `orchestrator/multi_agent.MultiAgentExecutor`.
- 해석 순서: `XGEN_HARNESS_DEFAULT_PROVIDER` env → `openai` → `anthropic` → 레지스트리 첫 항목 → `"openai"`.

### Backward compatibility

- `harness_config` 포맷 / 저장된 워크플로우 JSON / UI 계약 변경 없음.
- MCP 세션이 UI 선택 없이 workflow_data 에만 존재하는 기존 워크플로우도 그대로 동작 — 수집 주체만 s01 → s04 로 이동.
- `provider` 를 명시 전달해온 호출자 영향 없음. 빈 문자열 / 누락일 때만 런타임 해석이 돈다.

---

## [0.9.1] — 2026-04-20

### Fixed — 엔진 내 하드코딩 경로 제거 (연동성·확장성 원칙 위배 해소)

v0.9.0 이관 과정에서 기존 `s01_input` 의 API key 파일 폴백 경로 `/app/config/{env_var}.txt` 를 `s07_llm` 에도 복제해 **두 Stage 에 동일 경로 하드코딩**이 박힘. 엔진 "하드코딩 금지 / 연동성 최우선" 원칙 위배. 단일 진실 소스로 이관:

- **`providers.resolve_api_key_from_file(provider)` 신설** — API key 파일 폴백 경로를 **providers 레지스트리** 가 소유. Stage 는 경로를 모른다.
- **env override**: `XGEN_HARNESS_API_KEY_FILE_DIR` 로 파일 디렉터리 override 가능. 미설정 시 `/app/config` 기본 (backward compat).
- **`s01_input._resolve_api_key`** / **`s07_llm._lazy_init_provider`** — 둘 다 경로 직접 구성 코드 제거하고 `resolve_api_key_from_file()` 호출로 통일.
- 결과: 엔진 코드에서 `/app/config` 출현은 **providers/__init__.py 의 기본값 한 곳만** (docstring + fallback). Stage 코드에는 0.

### PHILOSOPHY §1 "책임 침범 금지" 강화

API key 해석 경로를 Stage 가 소유하지 않고 providers 레지스트리가 소유 — "외부 provider 플러그인이 추가될 때 각 Stage 에 경로 로직이 박혀있으면 매번 같이 수정해야 하는" 확장성 문제 해결.

---

## [0.9.0] — 2026-04-20

### Changed — Stage 책임 재정의 (철학 바로잡기)

각 Stage 의 책임/비책임이 선언된 문서가 없어 `s01_input` 이 LLM provider 생성까지 떠안고 `s03_system_prompt` 가 RAG 검색을 직접 호출하는 등 책임 경계가 오염돼 있던 문제 정리.

- **`docs/harness/00-PHILOSOPHY.md` 신설** — 12 Stage 의 "한 줄 정의 / 담당 / 비담당 / 의심되면 여기로" 를 단일 기준 문서로 선언. 상위 4 원칙(SRP, 책임 침범 금지, Artifact 전달, Strategy 분기) + 결정 트리 포함. 새 Stage/Strategy 제안 전 필독.
- **`s03_system_prompt` — RAG 검색 코드 전면 제거**. `_fetch_rag_via_service()` / `_fetch_rag_context()` 삭제. 이제 `state.rag_context` 를 **읽기만** 하고 섹션 조립 책임만 유지. 실행은 `s06_context` 단독.
- **`s07_llm` — provider lazy init 추가**. `state.provider` 가 없으면 `_lazy_init_provider()` 가 API key / base_url 해석 후 생성. `s01_input` 이 먼저 생성해두면 재사용(backward compat). 향후 `s01` 축소 시 `s07` 단독 담당.
- **중복 실행 제거** — 이전에는 사용자 입력 1건에 대해 s03 와 s06 가 각각 RAG 검색을 실행해 Documents API 를 2번 호출. v0.9.0 부터 s06 한 번만 호출.

### Backward compatibility

- `harness_config.provider / model / temperature / system_prompt` 포맷 변경 없음.
- 기존 저장된 워크플로우 JSON 영향 없음.
- `s01_input` 내부 provider 생성 로직은 그대로 유지 (deprecated but functional). v1.0 에서 제거 예정.

### Audit

- **Strategy 43개 전수 감사**: 41개 실제 구현 확인. 2개 marker (`ThresholdDecide` / `AlwaysPassDecide`) 는 Stage 내부 로직이 strategy 이름으로 분기하는 의도된 디자인.
- **`s03` / `s06` RAG 중복 경로** 제거.

### 후속 예정 (v0.9.x)

- `s01_input` provider 생성 / MCP discovery 제거 → `s07_llm` / `s04_tool_index` 전담 (v0.9.2 목표).
- `s04_tool_index` vs `s05_plan` 의 declared / discovery capability 책임 분할 명문화.
- Stage ID 문자열 하드코딩 재감사 (레지스트리 조회로 전환).

---

## [0.8.38] — 2026-04-20

### Added — 캔버스 노드 파라미터 manual/auto 토글 (dispatch 단절 해결)

이전까지 캔버스 노드가 `_tool_executors` 에 등록은 되지만 `ResourceRegistry.execute_tool()` dispatch 에 분기 없어 **실행 안 됨**. 이번 릴리즈로 실행 경로가 열리고, 파라미터마다 사용자가 **🤖(LLM)** 와 **✏(직접 입력)** 를 파라미터 단위로 토글 가능.

- **`node_control_policy.json`** — 카테고리 × 노드 × 파라미터별 control 정책. 3종: `manual`(LLM 금지) / `auto`(사람 입력 무의미) / `switchable`(사용자 선택). `synthetic_auto` 로 `query`/`a,b` 같은 input port 를 LLM 스키마에 승격. **노드 `.py` 파일 0 수정** — 전부 라이브러리 오버레이.
- **`_apply_node_overrides(spec_id, category, params_def, node_overrides, base_params)`** — 파라미터별 mode 결정 + `manual` 은 `_XgenNodeRef.params` 로, `auto` 는 `input_schema.properties` 로 분리. manual 키는 LLM 스키마에서 **숨김** (덮어쓰기 차단).
- **`ResourceRegistry.execute_tool()` dispatch** — `_XgenNodeRef` 분기 신설. 병합 순서 `{...tool_input, ...params}` — manual 이 마지막에 spread 돼 LLM 이 우회 주입해도 무시. `_call_xgen_node(instance_id, spec_id, category, merged)` 가 `editor.node_composer.get_node_class_by_id` 로 실제 노드 실행. 라이브러리 독립 환경에서는 graceful 에러 문자열 반환.
- **`ResourceRegistry.get_node_overrides()`** — builder 가 `harness_config.node_overrides` 조회. `load_all()` 시작부에서 스냅샷.
- **builder 5종 통합 리팩토링** — document_loaders / file_system / tools / arithmetic / ml 이 `_register_xgen_node_tool()` 공통 헬퍼를 쓰도록 정리. 중복 60줄 → 각 builder 15줄.
- **`_XgenNodeRef`** 에 `spec_id` / `control_map` 필드 추가. dispatch/디버깅에 사용.
- **env override** — `XGEN_HARNESS_NODE_POLICY_PATH` 로 policy JSON 경로 runtime 교체 가능 (외부 회사 custom 정책 주입).

### Fixed
- **레거시 캔버스 노드 호환 — 정책 미지정 노드는 `switchable + default_mode=manual`** 기본 적용 (기존 동작 보존).
- **manual-lock 보호** — `control: 'manual'` 파라미터는 사용자가 override 에 `mode='auto'` 를 주입해도 무시 (UI + backend 이중 방어).

### Packaging
- `pyproject.toml [tool.setuptools.package-data]` — `xgen_harness/integrations/*.json` 휠에 포함.

### Tests
- `test_node_parameter_control.py` — 14 케이스 PASS (policy 로드 / 카테고리별 control / manual 숨김 / switchable 토글 / manual-lock 방어 / synthetic_auto / builder 등록 / dispatch 병합 순서 / graceful unavailable).

### Notes (이식 측 / 프론트)
- xgen-workflow `/harness/options/node-control-policy` 엔드포인트 추가 (policy JSON 그대로 반환).
- xgen-frontend `useHarnessStore.node_overrides` + `fetchNodeControlPolicy` + `setNodeParamMode/Value` 액션.
- s04 `ResourceSelector` 에 `XgenNodeInlineList` (아이템 인라인, 🤖/✏ 토글) — 새 Stage/탭/모달 추가 없음.

---

## [0.8.37] — 2026-04-20

### Added — 대화 이어하기 + UX 가시성 4종

- **XgenAdapter.execute** — `conversation_history: Optional[list]` 파라미터 신규. PipelineState.conversation_history 로 전달 → s02_memory 가 이미 주입 경로 갖고 있어 **라이브러리 본체 변경 0, 호출자만 인입**.
- **stage_config `progressive_threshold` 메타 노출** — `get_stage_config('s04_tool_index')` / `get_all_stage_configs()` 응답에 search_tools 임계치(기본 12) 자동 주입. UI 라이브 배지 전제.
- **discovery 상수 공개화** — `_SEARCH_TOOLS_THRESHOLD` → `SEARCH_TOOLS_THRESHOLD` (공개) + `get_progressive_threshold()` getter. 외부 전략 교체 시에도 일관 키.
- **`_inject_stage_meta(stage_id, cfg)`** 내부 훅 — 스테이지별 UI 메타를 단일 지점에서 주입. 추후 다른 Stage 에 threshold/limit 노출 시 동일 함수 확장.

### Notes
- 외부 회사가 `adapter.execute(..., conversation_history=[{role, content}, ...])` 로 직접 과거 턴 주입 가능.
- 이식 측 `/harness/threads/{interaction_id}` 가 이 파라미터 채우는 소비자 (이식 레이어 참고).

---

## [0.8.36] — 2026-04-19

### Fixed — s06_context variable shadow
- `s06_context.execute` 의 `results` dict 가 RAG `for col in rag_collections:` 루프 안 `results = await doc_service.search(...)` 에 의해 **list 로 덮어씌워지던 regression** fix (v0.8.35 에서 ServiceProvider 우선 경로 추가하며 생김).
- 증상: `'list' object has no attribute 'get'` — `rag_fetch_complete` substep emit 시점 또는 return 값 조합 시.
- fix: 루프 내 변수 `search_hits` 로 분리.

---

## [0.8.35] — 2026-04-19

### Changed — 어댑터 고결성 audit 결과 fix
- **`pyproject.toml`**: 9개 entry_points 그룹 명시 (`xgen_harness.{stages,strategies,node_adapters,option_sources,tool_sources,providers,capabilities,fan_out_strategies,evaluation_criteria}`). 외부 작업자가 어떤 그룹으로 등록해야 하는지 lock-in.
- **`s06_context`**: ServiceProvider.documents 우선, httpx 직접 호출은 ServiceProvider 미주입 환경의 폴백으로 강등. `extract_source/text/score` 헬퍼로 응답 정규화.
  - 이전: 무조건 `_fetch_rag` (xgen-documents 스키마 직접 호출) → 라이브러리가 특정 회사 endpoint 알게 됨.
  - 지금: 외부 회사가 ServiceProvider.documents 자기 구현 주입하면 그걸로 라우팅, xgen 환경이 아니어도 작동.

### Notes
- 라이브러리 고결도 audit: C → B (어댑터 분리도 개선, magic URL fallback 은 `adapters/xgen.py` 안에서만 사용 — 라이브러리 본체 무침범)

---

## [0.8.34] — 2026-04-19

### Fixed — Multi-agent sub-agent SSE forwarding
- **`orchestrator/dag.DAGOrchestrator._run_node`**: sub-pipeline 의 모든 Stage 가 verbose 이벤트를 발행하도록 `state.event_emitter` 직접 주입 + `state.config` 전달.
- forward_task 가 무한 루프하지 않도록 `pipeline.run` 종료 후 sub-emitter `close()` 보장 (try/finally).
- forward_task 종료 timeout 2초 + 미종료 시 cancel — 메인 pipeline 이 sub-emitter 에 묶여 hang 안 됨.

이전: sub-agent 실행은 됐지만 메인 SSE 스트림에 sub 의 stage_enter/exit/substep 이벤트가 안 흐름 → UI 에서 DAG 진행 안 보임.
지금: 메인 EventLog 에 `[RAG[col_name]] Stage Start/Done` 형태로 sub-agent 진행 시계열 표시.

---

## [0.8.33] — 2026-04-19

### Fixed — UI 클릭이 실제로 동작하게 (이전엔 7개 param 이 stage 에서 무시됨)
- **`s06_context`**:
  - `folders`: 선택 폴더 안 컬렉션을 `rag_collections` 에 자동 펼침 (DocumentService.list_collections 위임)
  - `ontology_collections`: GraphRAG 검색 → DocumentService.ontology_query → `<graph_rag>` 블록 system_prompt 주입
  - `reranker`: RAG 결과를 DocumentService.rerank 로 재정렬
- **`s04_tool_index`**:
  - `custom_tools`: ResourceRegistry 의 사용자 도구를 tool_definitions 로 편입
  - `cli_skills` / `node_tags`: metadata 로 노출 (capability/s08 가 참조 가능)
  - `node_tags`: tool_definitions 필터 (선택 태그를 가진 도구만 통과)

이전: UI 가 7개 param 저장 → DB 저장은 OK 였으나 **stage.get_param 호출 0개** → 실제 동작 0.
지금: 7개 모두 stage 에서 사용 → 클릭이 실제 RAG/도구 흐름에 반영.

---

## [0.8.32] — 2026-04-19

### Added — Progressive Disclosure Level 0 (`search_tools`)
- **`tools/builtin.SearchToolsTool`** 신설 — Anthropic sandbox 패턴 차용. 키워드/카테고리 매칭으로 도구 카탈로그 검색.
- **`stages/strategies/discovery.ProgressiveDiscovery`**: 카탈로그 ≥ 12 개 일 때만 자동으로 `search_tools` 빌트인 추가. 작은 워크플로우는 system_prompt 비대화 안 됨.
- **`stages/s08_execute._dispatch_tool`**: `search_tools` 디스패치 추가 (인스턴스 `state.metadata.tool_registry["search_tools"]` 우선, 없으면 즉시 생성).

### Fixed — agent audit 결과 잔여
- 진짜 진정한 "환경만 주고 에이전트가 점진 발견" 흐름 — 0(검색) → 1(메타) → 2(스키마) → 3(실행) 4단계.

---

## [0.8.31] — 2026-04-19

### Changed — 전수 audit fix (하드코딩/중복/silent-except 정리)
- **`s07_llm.RETRY_DELAYS`**: stage_params override 가능 — `retry_delays_rate_limit/overload/server` 키로 사용자 임계값 주입.
- **`providers/base.normalize_base_url`**: anthropic/openai 의 base_url 정규화 로직(`/v1` 자동 조립) 단일 헬퍼로 통합. 두 provider 의 5줄씩 중복 제거.
- **`utils/docs.py`**: `extract_source / extract_text / extract_score` — RAG 결과 dict 정규화 헬퍼. resource_registry / s03_system_prompt / rag_tool 4곳의 중복 추출 로직 통합.
- **`adapters/resource_registry._call_api_tool`**: response_filter dot-path 추출 시 KeyError → silent fallback + debug 로그. 이전엔 except pass.
- **`stages/s09_validate.register_evaluation_criterion`**: 외부 작업자가 평가 기준을 한 줄로 추가하는 공개 API. ALL_CRITERIA dict 가 단일 진실 소스.

---

## [0.8.30] — 2026-04-19

### Changed — multi_agent_planner audit fix
- **Fan-out 전략 레지스트리**: `register_fan_out_strategy(name, builder, description)` 공개 API. if/elif 분기 제거, 외부 작업자가 한 줄로 새 전략 추가 가능.
- **sub_cfg 클론 헬퍼**: `_clone_config_for_sub` — `dataclasses.asdict` 로 base_config 의 모든 필드 복제 후 system_prompt / artifacts / stage_params 만 override. 이전엔 5개 필드만 복사해 다른 설정이 누락됐음.
- **system_prompt 템플릿 분리**: `DEFAULT_SUB_PROMPT_TEMPLATE` 상수 + `sub_agent_prompt_template` stage_param 으로 외부 override 가능.
- **stage_id 문자열 상수화**: `PLAN_SLOT / TOOL_INDEX_SLOT / CONTEXT_SLOT` — 문자열 리터럴 반복 제거.
- **type guard 추가**: `_collect_rag_collections` 가 dict / str / None 모두 안전하게 처리.

---

## [0.8.29] — 2026-04-19

### Added — Stage 확장성 본격 구현 (외부 Stage swap-in + 멀티에이전트)
- **`core/registry.py`**: entry_points 키에 `__` 구분자 지원. `"s04_tool_index__lotte"` 같이 등록하면 같은 슬롯에 새 artifact 로 swap-in. 디폴트는 안 깨짐.
- **`orchestrator/complexity.py`**: `ComplexityDetector` — 사용자 입력 길이 / 다중 인텐트 키워드 / RAG 컬렉션 수 / capability 수 / 도구 수 5 신호로 escalate 결정. 모든 가중치/임계값 stage_params override 가능.
- **`orchestrator/multi_agent_planner.py`**: `MultiAgentPlannerStage` — `s05_plan` 슬롯의 새 artifact `multi_agent`. 복잡도 escalate 시 RAG 컬렉션 별 sub-agent 자동 fan-out → DAGOrchestrator 병렬 실행 → 결과를 system_prompt 부록으로 주입 → s07_llm 이 종합. 캔버스 데이터 의존 0.

### Docs (별도 트리)
- `docs/harness/STAGE_CONTRACT.md` — 외부 작업자가 보고 그대로 따라 만들 수 있는 1페이지 Stage 계약서.
- `xgen-harness-stage-sample/` — 외부 Stage 샘플 패키지 (`s04_tool_index/lotte` artifact). pip install → entry_points 자동 발견 → UI swap 검증 완료.

### Frontend (xgen-frontend feature/harness-v2 동시 배포 예정)
- `index.tsx` 에서 `registerStageSelector / listRegisteredStageSelectors / ResourceSelector` 외부 export — 외부 패키지가 자기 Stage UI 를 plug-in 으로 등록 가능.

---

## [0.8.28] — 2026-04-19

### Added — Verbose substep events 확대 + xgen 노드 메타 어댑터
- **`s06_context`**: RAG fetch 시작/완료 시 `StageSubstepEvent` 발행 (`rag_fetch_start` / `rag_fetch_complete`).
- **`s08_execute`**: 각 도구 호출 전후로 `StageSubstepEvent` 발행 (`tool_call_start` / `tool_call_complete` + `tool_name`/`chars` 메타).
- **`s04_tool_index`**: MCP discovery 시작/완료 substep 추가 (`mcp_discover_start` / `mcp_discover_complete`).
- **`integrations/xgen_node_adapters.py`**: 5개 metadata-only NodeAdapter 추가 — `agents` / `chat_models` / `memory` / `routers` / `interaction`.
  - tool_def 는 발행하지 않고 `ResourceInfo` 만 등록 (해당 카테고리는 Stage 내부 로직이 직접 처리).
  - `/options/__list__` / capability UI 가 카테고리 존재를 인지할 수 있게 됨.

### Changed — UI Generic helper / Runtime tab
- **`ResourceSelector.tsx`**: 6개의 `toggle*` 함수를 하나의 `toggleIn(stageId, fieldId, current, id, setStageParam)` 헬퍼로 통합.
- **`StageDetailPanel.tsx`**: `RuntimeSection` 추가 — 선택한 Stage 의 마지막 `stage.exit` 출력(JSON) + verbose substep 이벤트를 패널 안에 표시.

---

## [0.8.27] — 2026-04-19

### Added — DocumentService 전면 확장 (xgen-documents 전수 연동)
- `DocumentService` Protocol 에 **`embed_query` / `rerank` / `list_folders` / `ontology_query`** 4 메서드 추가.
- `XgenDocumentService` 구현 — xgen-documents 전 엔드포인트 위임:
  - `embed_query`: `/api/embedding/query-embedding`
  - `rerank`:      `/api/embedding/reranker/rerank`
  - `list_folders`:`/api/folder/list`
  - `ontology_query`: `/api/ontology/graph-rag/multi-turn`
  - 기존: `search` (retrieval), `list_collections`

### Docs
- `README.md` 에 **인터페이스 구조도** 추가 (Config→Pipeline→Runtime→실행→Result + 각 박스별 API 매핑 + 확장 통로 표).

---

## [0.8.26] — 2026-04-19

### Added — xgen 노드 카테고리 전수 NodeAdapter 등록
- **`integrations/xgen_node_adapters.py` 신규**: xgen-workflow 의 노드 카테고리(functionId 기준) 를 NodeAdapter 로 bulk 등록.
  - `document_loaders` — vectordb/ontology/tool_selector 9종 → rag 계열 tool_def
  - `file_system` — filesystem_storage/minio/table_data_mcp 5종 → file I/O tool
  - `tools` — 20+ 노드 → generic tool (parameters → JSON Schema 자동 변환)
  - `arithmetic` — math/calculator
  - `ml` — ML 예측 도구
  - 빌트인 2종(`api_loader`, `db_query`) + 위 5종 = 총 **7 카테고리**
- **확장**: 새 카테고리 추가 = `_XGEN_CATEGORY_ADAPTERS` dict 한 줄 + builder 함수. 핵심 불변.
- **외부 플러그인**: `entry_points(group="xgen_harness.node_adapters")` 에 자체 `NodeAdapter` 등록하면 자동 반영.
- `XgenAdapter` import 시점에 `bootstrap_xgen_node_adapters()` 자동 호출 (멱등).

### Why
- v0.8.25 는 api_tool/db_tool 2 종만 어댑터 — 이게 "온톨로지 예시" 의도를 좁게 반영한 실수. 실제로는 xgen-workflow 의 document_loaders(ontology 포함), file_system, tools, arithmetic, ml 등 **tool-like 전 카테고리**가 하네스에서 자동 감지되어야 함. 본 릴리스로 전수 커버.

### 원칙
- `NodeAdapter` 레지스트리는 라이브러리 빌트인. xgen 특화는 `integrations/xgen_node_adapters.py` 에서 등록 (레이어 분리).
- MCP 는 기존 mcp_sessions 경로로 이미 수집됨 (중복 등록 안 함).
- agents/chat_models/model/memory/routers 는 Stage 자체의 내부 행동(s07 llm, s02 memory, s10 decide) 이라 tool 어댑터 대상 아님.

---

## [0.8.25] — 2026-04-19

### Changed — NodeAdapter 레지스트리 패턴 (통로화)
- `ResourceRegistry._load_api_tools` 안의 `if func_id in (...)` 하드코딩 분기를 **NodeAdapter 레지스트리** 로 전환.
- **`adapters/node_adapters.py` 신규**:
  - `NodeAdapter` dataclass (name / function_ids / build / resource_type / description)
  - `register_node_adapter(adapter)` 공개 API
  - `get_adapter_for(func_id)` 조회
  - `bootstrap_default_node_adapters()` — api_tool / db_tool 빌트인 2종 + `entry_points(group="xgen_harness.node_adapters")` 자동 발견
- `_load_api_tools` 는 이제 **분기 0**: 레지스트리 조회 한 번.

### Why
- 새 xgen 노드 타입(예: `ontology_retrieval`, `web_search`, `vector_search`, `embedding_lookup`) 연동 시 이전엔 `_load_api_tools` 함수 본체 수정 필요. 지금은 `register_node_adapter(NodeAdapter(...))` 한 줄.
- 외부 패키지가 자체 노드 타입을 PyPI 로 배포하고 `entry_points` 등록만 하면 하네스에서 자동 인식.

### 원칙
- **확장 = 플러그인 등록**. 핵심 코드(`ResourceRegistry._load_api_tools`) 불변.
- **if/elif 하드코딩 제거**. 레지스트리 조회로 dispatch.
- 이식 측(xgen-workflow harness.py) 의 `options/{source}` 도 별도 세션(`1fdce10`)에서 동일 패턴으로 전환 — 대칭 구조 완성.

---

## [0.8.24] — 2026-04-19

### Changed — 잔여 하드코딩 정리
- **`api/router.py`**: `/ws` 엔드포인트의 model 기본값 `"claude-sonnet-4-20250514"` → `PROVIDER_DEFAULT_MODEL` 참조. `OrchestratorRequest.model` Pydantic 기본값 `""` sentinel 로 교체.
- **`orchestrator/multi_agent.py`**: `MultiAgentExecutor.__init__` 의 `default_model` 하드코딩 제거. 빈 값 전달 시 `PROVIDER_DEFAULT_MODEL` 에서 해석.
- **`core/config.py:193`**: `from_dict` 의 model fallback 하드코딩 `""` 로 교체. 이전 v0.8.17 에서 openai_model / anthropic_model 만 sentinel 로 바꿨는데 model 필드 누락 — 이번에 통일.

### 잔여 의도 유지 (정당)
- `providers/anthropic.py:26`, `providers/openai.py:24` — 프로바이더 클래스 생성자의 provider-specific 기본값 (직접 인스턴스화 편의).
- `core/builder.py:6`, `providers/langchain_adapter.py:13`, `providers/__init__.py:161` — docstring 예시 (실행 코드 아님).
- `stages/strategies/token_tracker.py` — 모델별 가격표. Anthropic/OpenAI 공식 요금으로 사실 정보.
- `providers/__init__.py` 의 `PROVIDER_DEFAULT_MODEL` / `PROVIDER_MODELS` — 단일 진실 소스 레지스트리 자체.

---

## [0.8.23] — 2026-04-19

### Fixed — verbose_events 가 HarnessConfig 로 전달되지 않던 누수
- **`adapters/xgen.py`**: `config_kwargs` 에 `verbose_events` 누락 → HarnessConfig dataclass 기본값 False 사용 → `emit_verbose` 가 항상 no-op. v0.8.22 에 추가한 4종 이벤트 발행이 0 건이었던 이유. `hc.get("verbose_events", False)` 를 `config_kwargs` 에 명시 전달.

### Why
- v0.8.20 에서 emitter 를 선제 주입했으나 Config 의 verbose_events 자체는 전달 안 됨. emit_verbose 가 `state.config.verbose_events` 체크해서 무시.

---

## [0.8.22] — 2026-04-19

### Added — Verbose 이벤트 실제 발행 경로 완성
- **`PipelineState.emit_verbose(event)`** 헬퍼 (`core/state.py`): `verbose_events=True` + emitter 있을 때만 발행, 아니면 no-op. Stage/어댑터 한 줄 호출.
- **`Pipeline._execute_stage`**: Stage `on_error` 복구 성공 시 `RetryEvent` 발행. 에이전틱 루프 retry 결정 시에도 `RetryEvent` 발행 (`pipeline_loop`).
- **`s04_tool_index._bind_capabilities`**: 선언된 capability 각각 `CapabilityBindEvent(source="declaration")` 발행.
- **`s05_plan._discover_and_bind_capabilities`**: 자연어 발견으로 바인딩된 capability 각각 `CapabilityBindEvent(source="discovery", score=<매칭점수>)` 발행.
- **`adapters/xgen.py`**: `publish_capabilities` 후 `CapabilityBindEvent(source="auto_publish")` 요약 이벤트 발행.
- **`s07_llm`**: `llm_request_start` / `llm_response_complete` `StageSubstepEvent` 발행 (스테이지 내부 블랙박스 해소 샘플).

### Why
- v0.8.21 까진 verbose 이벤트 타입 정의 + SSE 변환 + `ServiceLookupEvent` 발행만 연결. `CapabilityBindEvent`, `StageSubstepEvent`, `RetryEvent` 는 타입만 있고 실제 발행 없음 → verbose 모드가 반쪽. 이번 릴리스로 4종 모두 런타임 관찰 가능.

### 호환성
- `verbose_events=False` (기본) 면 emit_verbose 가 즉시 return → 기존 출력 0 변화.
- Stage 추상 시그니처 / 외부 API / public 심볼 0 변화.

---

## [0.8.21] — 2026-04-18

### Fixed — verbose 이벤트 SSE 변환 누락
- **`integrations/xgen_streaming.py`**: `convert_to_xgen_event` 가 `ServiceLookupEvent`, `CapabilityBindEvent`, `StageSubstepEvent`, `RetryEvent` 4종을 모르는 타입으로 처리해 `None` 반환 → SSE 에서 필터링 누락. 각 이벤트를 `{type: "log", data.event_kind: <name>, ...}` 로 변환 추가. 이제 verbose_events=true 실행 시 SSE 에 실제로 나타남.

---

## [0.8.20] — 2026-04-18

### Fixed — verbose emitter 주입 시점 앞당김
- **`adapters/xgen.py`**: v0.8.19 에서 emitter 주입이 Pipeline 생성 시점(단계 7)이라, 그 전에 호출되는 `get_api_key`/`_resolve_adapter_setting` (단계 3~5) 에서는 이벤트 발행 안 됨. emitter 생성 + services.config 주입을 단계 2(harness_config 해석 직후)로 앞당김. 이제 execute 초반부터 ServiceLookupEvent 가 SSE 에 나타남.

---

## [0.8.19] — 2026-04-18

### Fixed — verbose 이벤트 실제 발행 경로 연결
- **`adapters/xgen.py`**: `HarnessConfig.verbose_events=True` 인 경우 pipeline 의 `EventEmitter` 를 `services.config` (XgenConfigService) 에 주입. 기존에는 타입만 정의되고 실제 발행은 None. 이제 `ServiceLookupEvent` 가 SSE 스트림에 실제로 나옴 — Redis vs env 조회 경로를 런타임에 추적 가능.

---

## [0.8.18] — 2026-04-18

### Fixed — OpenAI provider base_url endpoint 자동 조립
- **`providers/openai.py`**: `base_url` 이 base(예: `https://api.openai.com/v1`)만 와도 `/chat/completions` 자동 append. 지금까진 `OPENAI_API_URL` 상수(full endpoint URL)만 기대 → Redis `OPENAI_API_BASE_URL=https://api.openai.com/v1` 주입 시 404 발생. Anthropic provider 와 동일 패턴으로 통일.

### Why
- v0.8.17 에서 `s01_input._resolve_base_url` 이 Redis 에서 base URL 선제 주입 시작. persistent_configs 의 관례는 base URL (`/v1`) 저장이나, provider 는 full endpoint 기대 → 미스매치. 자동 조립으로 양쪽 포맷 모두 수용.

---

## [0.8.17] — 2026-04-18

### Added — Stage 계약/이벤트/레지스트리 확장
- **`StageInput`, `StageOutput`, `STAGE_IO_SPECS`, `get_stage_io`** public export (`__init__.py`): 외부 기여자가 Stage 서브클래스 작성 시 I/O 계약을 명시 선언 가능. Pipeline 이 실행 전 `validate()` 로 누락 필드 검출.
- **Verbose 이벤트 4종** (`events/types.py`): `ServiceLookupEvent` (Redis vs env 경로), `CapabilityBindEvent` (선언/발견/자동발행), `StageSubstepEvent` (스테이지 내부 단계), `RetryEvent` (재시도/폴백). `HarnessConfig.verbose_events=True` 에서만 발행 (기본 False, 하위 호환).
- **`ConfigService.get_setting(key, default)`** Protocol 메서드 (`core/services.py`): Redis → .env → default 순서 강제. 구현체는 `ServiceLookupEvent` 로 source 발행.
- **`PROVIDER_MODELS`, `get_provider_models(provider)`** (`providers/__init__.py`): provider 당 여러 모델 레지스트리. UI 드롭다운이 자동 반영, 새 provider 등록 시 목록만 추가하면 끝.
- **Strategy entry_points 자동 발견** (`core/strategy_resolver.py`): 외부 패키지가 `xgen_harness.strategies` entry_point 로 Strategy 등록 가능 (이름 형식: `stage_id:slot:impl`).
- **ArtifactRegistry.describe_all 에 `current_artifact`** 필드 + 중복 등록 경고.

### Fixed — Redis 우선 정책 누수 제거
- **`s01_input._resolve_base_url`** (`stages/s01_input.py`): `providers/__init__.py:70` 의 `os.environ.get({PROVIDER}_API_BASE_URL)` env-only 누수 수정. ServiceProvider → env 순 주입. 어댑터/스테이지 레이어에서 선제 해석.
- **model/temperature/max_tokens 기본값 Redis polling** (`adapters/xgen.py`): `_resolve_adapter_setting(key)` 헬퍼 추가. 해석 순서: UI → agent_config → Redis(`{PROVIDER}_*_DEFAULT`) → env → 코드 기본.

### Changed — 하드코딩 단일 진실 소스 통일
- **model 기본값 하드코딩 9곳 제거** (`session.py`, `builder.py`, `api/router.py`, `adapters/xgen.py`): 모두 `PROVIDER_DEFAULT_MODEL` 레지스트리 참조로 교체. 남은 2곳(dataclass 기본값, provider 생성자)만 유지.
- **`HarnessConfig.model/openai_model/anthropic_model`** 기본값을 `""` 로 변경: 어댑터/스테이지가 `PROVIDER_DEFAULT_MODEL` 에서 런타임 해석. 새 provider 추가 시 config.py 수정 불필요.
- **`stage_config.py` s01_input select options 배열 제거**: `_inject_dynamic_options()` 가 `get_provider_models()` 로 자동 주입. static 배열 = 오해 소지 제거.

### Docs
- **`V2_TESTING.md`** 신규: 책임 분리 매트릭스 (라이브러리/이식 측/프론트엔드) + 이식 실구동 3 시나리오.
- `REFACTORING_PLAN.md` / `EXECUTION_DESIGN.md`: 0.8.13 배포 시 추가분 유지.

### 원칙
- **라이브러리 ≠ 인프라 유지**: SQL/dialect/connection 해석 0. 추상 식별자만 다루고 구현체가 자동 발견.
- **Redis → env 역순 금지**: 모든 설정 조회에서 xgen-core Redis 가 부팅 고정 .env 보다 우선.
- **외부 API / 엔드포인트 / 12 Stage ID / 추상 시그니처 / public 심볼 0 변화**.

---

## [0.8.16] — 2026-04-18

### Fixed — E2E 테스트 중 발견한 s04 bypass 누수
- **`s04_tool_index.should_bypass`**: `builtin_tools` 체크 누락 수정. 사용자가 `stage_params.s04_tool_index.builtin_tools=["discover_tools"]` 를 명시해도 기존 로직에선 tools/RAG/MCP/capability 모두 없으면 bypass → builtin 도구가 LLM 에 노출되지 않아 tool_call 불가. 5개 플래그(tools/rag/mcp/caps/builtins) 중 하나라도 있으면 실행하도록 수정. 이로써 **Progressive Disclosure 의 Level 1 인덱스 + `discover_tools` 빌트인 만으로도 tool-loop 동작 가능**.

---

## [0.8.15] — 2026-04-18

### Added — DB 추상화 레이어 + 자동 인식
- **`DatabaseService.get_schema_summary(connection_name, max_tables)`** Protocol 추가 (`core/services.py`): 다중 DB 연결의 스키마 요약을 한 줄 텍스트로 반환. 라이브러리는 `connection_name` 같은 추상 식별자만 다룸.
- **`XgenDatabaseService.get_schema_summary`** 자동 발견 구현 (`integrations/xgen_services.py`): SQL/dialect 박지 않고 db_manager 가 가진 introspection 메서드를 다중 후보로 순차 시도 — 1순위 자체 요약 메서드(`get_schema_summary`/`describe_schema`/`describe_connection`), 2순위 dialect-agnostic 테이블 목록(`list_tables`/`get_tables`/`tables_in_schema`), 3순위 SQLAlchemy inspector. 어떤 DB 엔진이든 자동 인식.
- **`XgenAdapter` 자동 라우팅**: top-level `db_connections` 를 `s06_context.stage_params` 로 자동 주입 (rag_collections/mcp_sessions 패턴 동일).

### Changed — 미구현 TODO 2건 해결 (하드코딩 0)
- **`s11_save`**: 기존 TODO("실제 DB 저장")를 `services.database.insert_record()` 위임으로 해결. ServiceProvider.database 가 주입되면 직접 저장, 없으면 graceful skip — 어댑터 레벨 별도 경로(`harness.py:_save_execution_record`)와 공존.
- **`s06_context`**: 기존 TODO("DB 스키마/데이터 컨텍스트 추가")를 `services.database.get_schema_summary()` 위임으로 해결. db_connections 가 선언되면 스키마 요약을 `<db_context>` 블록으로 system_prompt 에 자동 주입.

### 원칙
- 라이브러리 ≠ 인프라 유지 — SQL/dialect/connection 해석 0건. 추상 식별자만 다루고 실제 introspection 은 db_manager 의 자동 발견.
- 어떤 DB 엔진(PostgreSQL/MySQL/Oracle/SQLite/SQLAlchemy)이든 db_manager 메서드 시그니처만 맞으면 자동 동작.
- graceful fallback — `services.database` 또는 introspection 메서드 미발견 시 모두 skip, 기존 동작 보존.

---

## [0.8.14] — 2026-04-18

### Fixed
- **API 키 조회 순서 정책 위반 수정** (`adapters/xgen.py`): `XgenAdapter.execute()` 가 `os.environ` (환경변수) 을 `ServiceProvider.config` (Redis/xgen-core) 보다 먼저 조회하던 누수를 수정. 관리자 UI 에서 런타임 변경한 값(Redis)이 .env 부팅 고정값에 무시되던 버그 제거. 정책: ExecutionContext (per-request override) → ServiceProvider (Redis 우선) → .env 폴백.

### Docs
- `xgen_harness/EXECUTION_DESIGN.md` 신규: 12 스테이지 구조, Progressive Disclosure, Capability 자동 조립, DAG 멀티에이전트, xgen 통합 흐름, 이식 방법, 효과 — 모든 결정에 "왜" 부착.
- `xgen_harness/REFACTORING_PLAN.md` 신규: 외부 API/스테이지 ID/시그니처 무손상 전제로 R1~R8 리팩토링 안 + Side-by-side 패턴 + 5 Phase 실행계획 + 우선순위 매트릭스.

---

## [0.8.13] — 2026-04-17

### Fixed
- **Strategy 기본 등록 자동 트리거**: 이전엔 `StrategyResolver.default()` 를 호출해야만 기본 40+ Strategy 가 등록되고, 직접 `StrategyResolver()` 로 생성하면 빈 레지스트리 상태였음. `resolve()` 호출 시 `_ensure_defaults_registered()` 로 자동 1회 트리거 — 실전 파이프라인에서 Strategy 찾기 실패 위험 제거.

### Docs
- README 확장성 감사표: v0.8.12 기준 + 통합 예외 복구 A등급 추가, 성숙도 **97%**.

---

## [0.8.12] — 2026-04-17

### Changed — 통합 수준 예외 복구 강화
- **`Pipeline._execute_stage`**: 이전엔 `HarnessError` 만 `on_error` 훅 경유. 일반 `Exception`(RuntimeError 등)은 바로 `PipelineAbortError` 로 래핑되어 외부 플러그인 Stage 가 자체 복구 기회 없었음. 이제 일반 예외도 `on_error` 호출 후 dict 반환 시 복구, None 반환 시 전파.
- 효과: 외부 기여자가 만든 Stage 가 예상치 못한 예외를 던져도 `on_error` 로 gracefully 처리 가능 → 파이프라인 강건성 향상.

### Tests
- `test_exception_paths.py` 7건 (단위 수준)
- `test_integration_runtime.py` 5건 (통합 수준)
  - Cost Guard 실전 차단, Iteration Guard 실전 차단, Loop back-jump, Stage 예외 on_error 복구, 50개 동시성 스트레스

---

## [0.8.11] — 2026-04-17

### Changed — 하드코딩 제거 (허브 정신 완성)
- **`_extract_agent_config_from_nodes`** (`core/config.py`): `if provider == "openai" / elif provider == "anthropic"` 분기 제거. `providers.get_default_model()` + `list_providers()` 로 레지스트리 기반 해석. 새 프로바이더 추가 시 config.py 수정 불필요.
- **`get_stage_config`** (`core/stage_config.py`): s01_input 의 provider/model UI 드롭다운 options 하드코딩 제거. `_inject_dynamic_options()` 가 `list_providers()` + `get_default_model()` 호출해 런타임 주입. 새 프로바이더 등록하면 UI 에 자동 반영.
- 효과: 라이브러리 본체에서 하드코딩된 프로바이더/모델 목록 **0 건**.

### 문서
- `README.md` 확장성 감사표 업데이트 (성숙도 85% → 95%)
- 구성 저장/로드 섹션 추가
- 허브 정신 일관성 체크리스트 추가

---

## [0.8.10] — 2026-04-17

### Changed
- `HarnessConfig.to_dict` / `from_dict` 를 `dataclasses.fields()` 자동 발견 방식으로 리팩토링. 이전 버전은 필드를 수동 나열해 HarnessConfig 에 새 필드 추가 시 직렬화 코드도 같이 수정해야 했던 하드코딩 문제. 이제 dataclass 에 필드만 추가하면 to_dict/from_dict 에 자동 반영됨 (허브 정신 일관성).
- 현재 22개 dataclass 필드 전부 자동 감지 확인.

---

## [0.8.9] — 2026-04-17

### Added — 직렬화 (Save/Load)
- **`HarnessConfig.to_dict() / to_json() / save(path)`** + **`from_dict() / from_json() / load(path)`** — Builder 로 만든 설정을 JSON 으로 저장하고 다시 로드해 Pipeline 실행까지 가능.
- **`PipelineBuilder.to_dict() / to_json() / save(path)`** + **`from_dict() / from_json() / load(path)`** — Fluent Builder 의 최종 상태를 파일에 영속화.
- `_schema_version: 1` 필드로 향후 스키마 버전 관리 대비.
- `test_serialization.py` E2E: Builder → save → load → Pipeline 실행 + 커스텀 Stage 실제 호출 확인.

### 노트
- api_key, Tool ABC 인스턴스, EventEmitter 는 직렬화에서 제외 (보안/실행 시 재주입 필요).
- REQUIRED_STAGES 는 from_dict 시 자동 제거 (비활성화 불가 스테이지).

---

## [0.8.8] — 2026-04-17

### Fixed — 플러그인 확장성 실동작 결합
- **`register_stage()` 로 등록한 커스텀 Stage 가 Pipeline 에 반영되지 않던 버그**. `Pipeline.from_config()` 가 `ArtifactRegistry.default()` 를 매번 새로 만들어 전역 싱글톤에 등록된 플러그인을 보지 못했음. `_get_default_registry()` 싱글톤을 사용하도록 수정.
- `register_stage("s04_tool_index", "lotte", LotteStage)` + `HarnessConfig.artifacts={"s04_tool_index": "lotte"}` 경로 실제 호출 확인 (E2E 테스트 추가).
- `register_strategy()` + `StrategyResolver.resolve()` 경로는 기존에 정상 동작했던 것 회귀 테스트로 고정.

### Added
- `Pipeline.from_config(registry=...)` 선택 파라미터 — 테스트/격리용 registry 주입 가능.
- `test_plugin_extensibility.py` — 외부 기여자 시나리오 (Lotte 예시) 3종 E2E 테스트.

### 실측 검증
- 커스텀 Stage 등록 → artifacts 선택 → Pipeline 실행 시 호출됨 ✅
- 커스텀 Strategy 등록 → StrategyResolver.resolve → 인스턴스 반환 ✅
- artifacts 미지정 시 default 유지 (회귀 0) ✅

---

## [0.8.7] — 2026-04-17

### Fixed
- **API 키 전파 순서 역전**: `XgenConfigService.get_api_key` 가 환경변수(.env)를 먼저 읽고 Redis(xgen-core Config)를 뒤에 읽어, 관리자가 UI 에서 런타임 변경한 API 키가 반영되지 않던 문제. **Redis → .env → 폴백** 순서로 정정. (main `589249d` "시스템 설정 Redis 우선 조회" 정책과 정렬)

---

## [0.8.6] — 2026-04-17

### Fixed
- **Documents 검색 422 스키마 오류**: `RAGSearchTool._search_documents` / `XgenDocumentService.search`가 `{query, collection_names:[], top_k}`로 보냈는데 실제 xgen-documents API(`POST /api/retrieval/documents/search`)는 `{query_text, collection_name, limit}` (단수) 를 요구. 페이로드 스키마 정정.

---

## [0.8.5] — 2026-04-17

### Fixed — xgen 서비스 연동 마무리
- **Documents API 401**: `XgenDocumentService.search/list_collections` + `RAGSearchTool._search_documents`가 `x-user-id` 헤더를 보내지 않아 401 반환되던 문제. `ExecutionContext`의 `user_id`를 헤더로 전달하도록 통합.
- **DB 도구 `__raw_query__` 버그**: `ResourceRegistry._call_db_tool`이 존재하지 않는 가상 테이블을 조회하던 문제. `XgenDatabaseService.execute_raw_query` 메서드 신설 + 사용.
- **top-level `rag_collections` 미인식**: 사용자가 `harness_config.rag_collections`로 선언하면 s04가 `stage_params.s04_tool_index.rag_collections`에서만 읽어 미반영되던 문제. Adapter가 top-level → s04 stage_params로 자동 매핑. `mcp_sessions`, `rag_top_k`, `rag_tool_mode`도 동일 적용.

### Added
- `XgenAdapter.execute()`에서 `set_execution_context`에 `user_id`, `user_is_admin`, `user_is_superuser` 전달.
- `XgenDatabaseService.execute_raw_query(query, params, limit)` 신설.

---

## [0.8.4] — 2026-04-17

### Fixed
- **RAG 컬렉션 등록 누락**: `_load_rag_collections`가 `services.documents.list_collections()` 실패(401/empty) 시 selected를 등록하지 않던 문제. 이제 selected는 항상 등록되고, list API는 description enrich 용도로만 사용.

---

## [0.8.3] — 2026-04-17

### Fixed
- **OpenAI 프로바이더 도구 호출 버그**: Anthropic assistant content의 `tool_use` 블록이 OpenAI `tool_calls` 필드로 변환되지 않아, 2번째 LLM 호출 시 "tool must be a response to a preceding message with tool_calls" 400 에러 발생하던 문제. `_convert_messages`에서 assistant tool_use → tool_calls, user tool_result content list 평탄화 처리 추가.

---

## [0.8.2] — 2026-04-17

### Fixed
- XgenAdapter가 `HarnessConfig` 생성 시 `capabilities` / `capability_params` / `active_strategies`를 전달하지 않던 버그. 프론트에서 선언한 capability가 s04_tool_index에 도달하지 않아 `capabilities_declared=0`으로 찍히던 문제.
- Adapter → HarnessConfig 전달 누락 필드 3종 추가.

---

## [0.8.1] — 2026-04-17

### Fixed
- PyPI의 이전 0.8.0 번호가 빈 내용으로 등록되어 있어 Capability 모듈이 실제로 설치되지 않던 문제 해결.
- 내용 변경 없음 — v0.8.0과 기능 동일, 버전만 재배포.

---

## [0.8.0] — 2026-04-17

### Added — Capability System (선언형 도구 자동 조립)

핵심 철학: **사용자는 "무엇을 할지(capability)"만 선언, 하네스가 Tool을 자동 조립**.

#### 새 모듈 `xgen_harness.capabilities`

- `CapabilitySpec` / `ParamSpec` / `ProviderKind` (8종) — 기능 명세 스키마
- `CapabilityRegistry` — 4개 인덱스(name/category/tag/provider) + alias, thread-safe, 전역 싱글톤
- `CapabilityMatcher` — 3단계 매칭: exact_tag → keyword → llm_fn. 한국어 조사 대응 부분일치
- `materialize_capabilities()` / `merge_into_state()` — capability name → Tool 인스턴스 → PipelineState 반영
- `ParameterResolver` — 런타임 파라미터 자동 채움 (우선순위: provided → context → llm → default)

#### 핵심 public API

```python
from xgen_harness import (
    CapabilitySpec, ParamSpec, ProviderKind,
    CapabilityRegistry, CapabilityMatcher, MatchStrategy,
    ParameterResolver, ResolveResult,
    materialize_capabilities, merge_into_state,
    get_default_registry, set_default_registry,
)
```

#### `HarnessConfig` 확장

- `capabilities: list[str]` — 선언된 capability 이름 리스트
- `capability_params: dict[str, dict]` — capability별 override 파라미터
- `from_workflow()`에서 자동 로드

#### Stage 통합

- **s04_tool_index**: `_bind_capabilities()` 훅. `config.capabilities` → CapabilityRegistry 조회 → tool_factory 호출 → state에 주입
- **s05_plan**: `planning_mode="capability"` 모드 + `capability_discovery` 플래그. 자연어 intent → Matcher → 자동 탐색/바인딩
- **s08_execute**: `_enrich_with_capability()` 훅. 도구 실행 직전 ParameterResolver로 누락 args를 context에서 자동 채움

#### Adapter 자동 발행

- `ResourceRegistry.publish_capabilities()` — 로드된 모든 자산(MCP/API/DB/Gallery/RAG)을 CapabilitySpec으로 변환 후 레지스트리에 자동 등록
- `XgenAdapter.execute()`에서 `load_all()` 직후 자동 호출

#### 이벤트

- `MissingParamEvent` (SSE type: `missing_param`) — 필수 파라미터 누락 시 UI 되물음 신호

### 바인딩 경로 (3가지)

1. **선언** — `config.capabilities = [...]` → s04 바인딩
2. **발견** — 자연어 intent → s05 Matcher 탐색 → 자동 바인딩
3. **자동 발행** — xgen 자산 → Adapter가 publish → 1/2에서 사용 가능

### Tests

42개 테스트 PASS (10 기존 + 32 신규):
- `test_capabilities.py` (9) — schema / registry / matcher
- `test_capability_stage.py` (8) — s04 통합
- `test_capability_resolver_plan.py` (10) — ParameterResolver + s05 capability 모드
- `test_capability_adapter.py` (5) — ResourceRegistry publish

### 주의사항

- **무침범**: 기존 `config.tools`, `stage_params`, `active_strategies` 전부 동작 유지
- **라이브러리 ≠ 인프라**: 라이브러리는 빈 Registry로 시작, Adapter가 자산 주입
- Matcher의 `min_score` 기본 0.4 — 한국어 짧은 intent는 0.3으로 낮추거나 tag 풍부하게 등록 권장

---

## [0.7.0] — 2026-04-17 (오전)

### Added
- RAG 도구 모드 (presearch/tool/both) — 에이전트가 rag_search 도구로 직접 호출
- 컨텍스트 크기 제한 + 중간 자동 압축 (anthropic/openai/google: 500K, vllm: 50K)
- Citation 지시 주입 (`[DOC_N]` 형식)

### Changed
- 비용 계산 PRICING 단일 소스화 (token_tracker.py)
- `provider_name` 하드코딩 제거

## [0.6.0] — 2026-04-17

### Added
- 9개 Stage 파라미터 실연동: max_history, planning_mode, criteria, output_format, save_enabled 등

## [0.5.3] — 2026-04-16 (야간)

### Added
- XgenAdapter → workflow_data.stage_params (top-level) 병합

## [0.5.2] — 2026-04-16

### Added
- XgenAdapter → HarnessConfig에 stage_params/preset/disabled_stages 전달

## [0.5.1] — 2026-04-16

### Changed
- ServiceRegistry 완전 분리 — 라이브러리에서 인프라 가정 제거

## [0.5.0] — 2026-04-16

### Added
- ServiceRegistry + ExecutionContext + Plugin System
- 하드코딩 전면 제거

## [0.4.0] — 2026-04-16

### Added
- ResourceRegistry (MCP/API/DB/Gallery/RAG 통합)
- 버그 수정 다수

## [0.3.0]

### Added
- XgenAdapter + Provider Registry + Gallery Tools

## [0.2.0]

### Added
- ServiceProvider + workflow_bridge

## [0.1.0]

### Added
- 초기 배포 — 12스테이지 파이프라인
