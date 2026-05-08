# Changelog

All notable changes to `xgen-harness` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.5.3] — 2026-05-08

### 📊 EventLog 디버깅 친화 강화 (사용자 호소: "디버깅 가능할 정도 / 정책 안 먹힘 / 도구 선택 안 보임")

- **s04_tool**: `ToolDeferredEvent` emit 조건에서 `has_explicit_selection` 제거 — selected_tools 비어있어도 도구 발견 시 항상 emit. eager / deferred 카운트가 EventLog 에 항상 노출.
- **s05_policy**: stage.execute 진입 시 활성 가드 list 를 `StageSubstepEvent(substep="guards_active")` 로 emit. "정책 안 먹힘" 호소 정합 — 어떤 가드가 박혀있는지 사용자가 EventLog 에서 즉시 확인.
- 동작 변경 0 (이벤트 가시성만 강화).

## [1.5.2] — 2026-05-08

### 📝 Stage Configuration fields description 친화 톤 재작성 (patch)

v1.5.1 에서 stage 의 description_ko / behavior 까지는 갈음했지만, 각 stage 의 **Configuration fields** 의 `description` 텍스트는 여전히 기술 키워드 잔존. 사용자 호소: "설정 fields description 이 뭔 소리지 모름".

- s07_act: result_budget / preview_threshold / preview_size description 친화 톤
- s08_decide: max_retries / judge_enabled / judge_threshold / criteria / evaluation_strategy / evaluation_prompt_template / evaluation_system_prompt 7 개 모두
- s09_finalize: save_enabled / table_name / input_text_cap / output_text_cap 4 개

기술 키워드 제거 매트릭스:
- `judge_then_loop strategy` / `Strategy 카드 'X' 픽` → "켜면 / 활성화하면" 같은 자연어
- `register_evaluation_criterion()` / `entry_points(xgen_harness.X)` → "외부 플러그인으로 추가 가능"
- `harness_execution_log` / `PERSIST_DEFAULTS['X'] override` → "데이터베이스에 영구 저장 / 비우면 기본값"
- `pd_stores` / `fetch_pd(kind='X')` → "별도 저장소에 보존 / LLM 이 필요할 때 다시 조회"
- `Claude Code L1 패턴` → 이름 노출 X

엔진 동작 변경 0 (텍스트만).

## [1.5.1] — 2026-05-08

### 📝 9 Stage UI 텍스트 사용자 친화 톤 재작성 (patch)

v1.4.1 / v1.5.0 에서 stage_config.py 의 description_ko / behavior 갱신했지만 여전히 버전 명시 (v1.X.0 / R3) / 코드 path (`pd_stores` / `fetch_pd` / `state.*` / `s06_context.*`) / 영어 약어 (Cascade L3/L4/L5 / microcompact / autocompact / progressive disclosure / ToolSearch) 잔존. 일반 사용자가 "뭔 소린지 모름" 호소.

전 9 stage (s00~s09) 의 `description_ko` / `behavior` 항목을 일반 사용자가 읽을 수 있는 자연어 한국어로 재작성:

| Stage | 변경 |
|---|---|
| s00_harness | "Harness 통제탑 / Provider / streaming/batch / Planner OFF" → "에이전트가 답변할 때 LLM 모델을 호출합니다 / 일시 오류 시 자동 재시도" |
| s01_input | "content block 정규화" → "LLM 이 이해하는 형식으로 변환" |
| s02_history | "harness_execution_log → execution_io → chat_session 폴백" → "여러 저장소 자동 폴백 (실행 로그 → 입출력 기록 → 채팅 세션)" |
| s03_prompt | "Identity → Rules → Planning → Tools → RAG → History" → "역할 → 규칙 → 사고 모드 → 도구 안내 → 참고 자료 → 대화 이력" |
| s04_tool | "selected_tools / eager / deferred / ToolSearch / progressive disclosure" → "명시 선택한 도구 / 그 외 도구는 이름만 보이고 에이전트가 필요할 때 직접 불러옴" |
| s06_context | "Cascade L3→L4→L5 / pd_stores / fetch_pd / R3" → "단계적 자동 압축 / 검색 결과는 요약만 먼저, 본문은 필요할 때 다시 가져옴" |
| s07_act | "L1 Tool Result Budget / preview_threshold / pd_stores / discover_tools" → "큰 도구 결과는 미리보기로 줄이고 원본은 별도 보관" |
| s08_decide | "judge_then_loop strategy / Policy Gate block" → "비용/반복 한도 시 종료 / 품질 평가 점수 미달이면 재시도" |
| s09_finalize | "MetricsEvent / DoneEvent / persist strategy / harness_execution_log" → "토큰 사용량 / 비용 / 소요 시간 통계 발행 / 옵션: 데이터베이스에 영구 저장" |

엔진 동작 변경 0 (텍스트만). 외부 기여자용 detail (entry_points / 플러그인 등록) 은 한 줄 정도로 압축 보존.

## [1.5.0] — 2026-05-08

### 🚨 BREAKING — Ontology / GraphRAG R3 도구 위임 (RAG 와 isomorphic)

v1.4.0 R3 가 RAG 측만 도구 위임으로 정합화하고 Ontology / GraphRAG 측은 옛 자동 사전 검색 그대로였음. 사용자 지적: "온톨로지 GraphRAG 의 검색 로직은 복잡한데 (multi_turn_rag 의 child LLM ReAct 멀티턴) 그걸 매 턴 자동으로 호출하는 게 말이 되냐". 정확. v1.5.0 = ontology 도 같은 R3 정신.

#### 신규 빌트인 — `tools/ontology_tool.py:QueryGraphTool`
- `query_graph(question, collection)` — backend `multi_turn_rag.query()` 위임
- progressive PD: 본문은 `state.pd_stores["graph"]` 에 보관, LLM 에는 인덱스+snippet 만 (200자 default)
- LLM 은 `fetch_pd(kind='graph', id='<col>::<hash>')` 로 본문 lazy fetch

#### s04_tool/stage.py
- `ontology_collections` + `ontology_tool_mode` stage_param 받아서 `query_graph` 빌트인 등록
- DocumentService.ontology_query 가용 시에만 등록 (없으면 graceful skip)
- rag_search 등록과 isomorphic 패턴 — 코드 흐름 통일

#### s06_context/stage.py
- `ontology_query` 자동 호출 (line 326-348) 을 `ontology_tool_mode in ('context','both')` 일 때만 실행
- default `'tool'` 이면 SKIP — s04 가 등록한 빌트인이 LLM 손에 있어 LLM 이 결정해서 호출
- 백워드 호환: 사용자가 명시적으로 `context` / `both` 박으면 자체 호출 동작 (기존 흐름)

#### 효과 (v1.4.0 R3 와 동일)
| 항목 | v1.4 (RAG 측만) | v1.5 (Ontology 측도) |
|---|---|---|
| 매 턴 자동 호출 | ❌ (RAG 도구화 후 자율) | ❌ (Ontology 도 자율) |
| LLM 자율성 (호출 결정) | ✅ | ✅ |
| Progressive PD | ✅ rag | ✅ rag + graph |
| pd_stores kind | `tool_result` / `rag` / `history` / `db_schema` / `gallery` | + `graph` 신규 |

#### 비고
- 백엔드 `multi_turn_rag` (child ReAct) 그대로 유지 — 캔버스 호환 영향 0
- 이중 LLM 호출 자체는 잔존하지만 LLM 이 호출 결정 → 비용 폭증 X (질문이 그래프 무관하면 호출 안 함)
- 빌드 안 된 컬렉션 박힌 경우 백엔드가 unavailable / 빈 결과 반환 → LLM 이 처리. 사용자가 "빌드된 것만" 박도록 frontend 옵션 source 필터는 v1.5.x 후속

## [1.4.1] — 2026-05-08

### 📝 stage_config.py UI 동작방식 박스 텍스트 v1.4 정합 갱신

v1.4.0 commit 에서 코드 동작은 모두 변경됐으나 (eager/deferred 분리 / ToolSearch 빌트인 / R3 RAG 도구 위임 / cascade 자동 압축), `stage_config.py` 의 `description_ko` / `behavior` 텍스트는 v1.0 시점 그대로였음. UI 의 "동작 방식" 박스가 옛 동작 (Level 1/2/3 / `DocumentService.search` 등) 을 설명해서 사용자 혼란. 텍스트만 갱신.

#### s04_tool
- `description_ko`: "Strategy 카드는 progressive_3level / eager_load / capability_auto / none" → "selected_tools 명시 도구만 eager / 나머지 deferred / LLM 이 ToolSearch 로 명시 승격 (Claude Code 정합)" + R3 RAG 도구 위임 안내
- `behavior` 7 항목 → 11 항목으로 재작성 (deferred 분리 / ToolSearch / search_tools / discover_tools / fetch_pd / rag_search 빌트인 / R3 / progressive PD)

#### s06_context
- `description_ko`: "RAG 검색 → 컨텍스트로 주입 / 압축" → "참조 리소스 선택만 / 검색은 도구로 위임 (R3) / cascade 자동 압축"
- `behavior` 7 항목 → 10 항목으로 재작성 (Cascade L3/L4/L5 / R3 도구 위임 / progressive PD / 5종 PD 공유 인프라)

엔진 코드 동작은 v1.4.0 그대로 — 행동 변경 없음 (patch). UI 표면 안내가 실 동작 정합되도록만 정정.

## [1.4.0] — 2026-05-08

### 🚨 BREAKING — Claude Code 정합 deferred tools + UI 표면 대청소 + R3 RAG 도구 위임

#### Stage order swap (s04 → s03)
- `s04_tool.order = 3` (was 4), `s03_prompt.order = 4` (was 3). ingress phase 안에서 도구 카탈로그를 먼저 채우고 system_prompt 가 그 결과를 본다. 기존엔 `_build_tool_index_section` 이 항상 빈 `state.tool_index` 를 봐서 `<available_tools>` 섹션이 미렌더되던 회귀 fix.

#### Claude Code 스타일 deferred tools (v1.2.0 통합)
- `s04_tool` 가 `selected_tools` 화이트리스트 기준으로 도구를 **eager** (Anthropic API `tools=` 인자에 박힘) / **deferred** (이름+1줄 desc 만 system_prompt 노출) 로 분리.
- `state.tool_schemas` 에 모든 도구 (eager+deferred) full schema 캐시.
- 신규 빌트인 `ToolSearch(names=[...])` — deferred 도구를 명시 이름으로 schema 합류 → 다음 turn `tools=` 에 자동 누적 (dynamic catalogue). `select:a,b` 문자열 형식도 자동 파싱 (Claude Code 정합).
- `search_tools` (≥12 도구) 의 결과 안내에 ToolSearch 합류 흐름 명시 — keyword 검색 → 합류 자연스럽게 연결.
- `state.tool.deferred` / `state.tool.loaded_names` 필드 신규 + property shim.
- `ToolDeferredEvent` / `ToolLoadedEvent` 신규.

#### 사용자 픽 strategy 카드 4 stage hide
- `s01_input.list_strategies()` → `[]` (분류는 LLM 자율)
- `s03_prompt.list_strategies()` → `[]` (사고 패턴 자율)
- `s04_tool.list_strategies()` → `[]` (progressive_3level + ToolSearch 가 default)
- `s07_act.list_strategies()` → `[]` (sequential default)
- `s06_context.list_strategies()` → `[cascade]` 1개만 (압력별 L3→L4→L5 자동 에스컬레이션)
- 코드 경로 (with_classification / cot_planner / react / eager_load / capability_auto / token_budget / sliding_window / microcompact / context_collapse_overlay / autocompact_llm / parallel_read / strict_no_error) 모두 보존 — 외부 plugin 또는 `active_strategies` 직접 셋으로 강제 가능. UI 표면만 단순화.

#### R3 — s06 RAG 자체 검색 → s04 rag_search 도구 위임
- `rag_tool_mode` default `'both'` → `'tool'`. `s06_context` 가 `rag_collections` 박혀있어도 자체 `doc_service.search` 호출 안 함. `s04_tool` 가 등록한 `rag_search` 빌트인이 LLM 손에 노출 → LLM 이 도구로 직접 호출.
- `RAGSearchTool` 에 progressive PD 신규 — `state_ref` + `progressive=True` + `snippet_size=120`. 검색 결과 본문은 `state.pd_stores["rag"]` 에 보관, LLM 에는 인덱스+snippet 만 반환. LLM 이 `fetch_pd(kind='rag', id=...)` 로 본문 lazy fetch.
- s06 의 책임 재정의: ❌ RAG 검색 (도구로 위임) / ✅ 참조 컬렉션 선택 + history 압축 (cascade).

#### Deprecated 자동 정규화 (백워드 호환 무성의 X — 엄밀 cleanup)
- `DEPRECATED_STRATEGIES_BY_STAGE` / `DEPRECATED_STAGE_PARAM_VALUES` 맵 신규.
- `_normalize_active_strategies()` / `_normalize_stage_params()` 함수.
- **`HarnessConfig.__post_init__` 에서 강제 호출** — 모든 인스턴스화 경로 (`cls(**kwargs)` / `from_dict` / `from_workflow` / 이식측 직접 `cls(**config_kwargs)`) 통과. DB 의 옛 워크플로우 row 가 `token_budget` / `eager_load` / `cot_planner` / `parallel_read` 등 박고 있어도 실행 시점에 자동으로 `cascade` / `''` (default 폴백) 로 정규화.
- `rag_tool_mode: 'both'/'context'` → `'tool'`, `rag_pd_mode: 'eager'` → `'progressive'` 도 동일 정규화.

#### 비고
- s06 의 자체 RAG 검색 코드 (`if rag_collections and state.user_input` 블록) 는 보존 — `rag_tool_mode == 'context'` 또는 `'both'` 명시 시 백워드 호환 동작.
- 도구 5종 PD (tool_result / rag / history / db_schema / gallery) 모두 default 로 적용 — 사용자가 카드/설정 안 박아도 자동.

## [1.0.9] — 2026-05-01

### 🏗 Plugin Registration API 정리 + s06 god-class 분해 + runtime_defaults 인프라

#### Top-level register_* API (외부 plugin 단일 진입)
- `xgen_harness/__init__.py`: 30+ register_* / get_* / list_* / Protocol 함수를
  top-level export. 외부 패키지가 `from xgen_harness.core.phase_registry import ...`
  같이 깊은 모듈 경로를 알 필요 없이 `from xgen_harness import register_phase` 한 줄로 사용.
- 새 export: `register_runtime_default`, `get_runtime_default`, `resolve_with_default`,
  `list_runtime_defaults`, `register_phase`, `register_orchestrator`, `register_service`,
  `register_env_mapping`, `register_strategy`, `register_node_plugin`, `register_provider`,
  `register_node_adapter`, `register_fan_out_strategy`, `register_decide_defaults`,
  `register_model_pricing`, `register_preset`, `register_capability_discovery_defaults`,
  `register_output_formatter`, `register_persist_defaults`, `register_identity`,
  `register_rules`, `register_thinking_mode`, `register_evaluation_criterion`,
  `register_evaluation_prompt_template`, `register_judge_defaults`, `register_term_expander`,
  `register_search_alias`, `TermExpander`, `available_guards`, `describe_guards`,
  `build_guard_chain`. 기존 `register_stage`/`register_tool_source`/`register_guard`/
  `register_xgen_node_resolver`/`register_sandbox_verifier` 와 함께 entry_points 그룹과
  1:1 매핑.

#### runtime_defaults registry (sentinel 폴백 인프라)
- 새 모듈 `core/runtime_defaults.py` (118 LOC, 16 floor 사전 등록).
  엔진은 정책 default 를 박지 않지만(이식측 책임), 정책 sentinel(None) 이 산술/비교
  위치까지 흘러가면 TypeError 크래시 → 안전 바닥(safety floor) 으로 폴백.
- 16 floor: max_iterations / max_retries / max_tool_rounds / validation_threshold /
  synthesis_intro_threshold_chars / max_pending_tool_results / context_window /
  max_tokens / thinking_budget_tokens / temperature / cascade_l3/l4/l5_threshold_pct /
  compaction_threshold_pct / microcompact_threshold_pct / context_collapse_threshold_pct /
  autocompact_threshold_pct.
- `register_runtime_default(key, value)` — 외부 plugin 이 도메인 floor override.
- `core/llm_call.py` / `core/pipeline.py` / `stages/s06_context/stage.py` 의 sentinel
  폴백을 `resolve_with_default` 헬퍼로 통일.

#### s06_context god-class 분해 (mixin 패턴)
- 새 모듈 `stages/s06_context/cascade.py` — `CascadeCompactionMixin` (327 LOC):
  L3 microcompact / L4 collapse_overlay / L5 autocompact_llm + cascade dispatcher.
- 새 모듈 `stages/s06_context/intent.py` — `IntentRoutingMixin` (62 LOC):
  stage_param `intent_rules` → `auto_metadata_filter` 자동 결정.
- `ContextStage(CascadeCompactionMixin, IntentRoutingMixin, Stage)` 다중 상속으로
  본 클래스는 dispatcher + RAG/DB fetch + token budget compaction 만.

#### term_expansion 단일 정의 (god-class 정리)
- `tools/term_expansion.py` 신설 — `TermExpander` Protocol + `register_term_expander` /
  `register_search_alias` / `expand_query_terms` / `_load_entry_points_once` 단일 정의.
- `tools/builtin.py` 는 위 모듈에서 re-export 만 (기존 자체 정의 155 LOC 삭제).
  외부 호환 보장 — `from xgen_harness.tools.builtin import register_term_expander`
  여전히 작동.

## [1.0.8] — 2026-05-01

### 🧹 s02_history.memory_collection UI 필드 제거 (dead UI)

- `core/stage_config.py` s02_history.fields 에서 `memory_collection` 항목 제거.
  v0.29.1 audit 때 "embedding_search 전략 임계 3종" 으로 노출했으나 실제 사용자 시나리오에서
  안 쓰여서 /harness 페이지의 이력 stage 클릭 시 죽은 dropdown 노출만 됨.
- 코드 동작 변경 없음 — `s02_history/stage.py:87` 의
  `get_param("memory_collection", state, "memory")` fallback 으로 default `"memory"`
  collection 이름이 그대로 들어감. embedding_search strategy 가 필요한 사용자는
  xgen-documents 에 `memory` 라는 collection 만 미리 만들어두면 동일하게 동작.
- `memory_top_k` / `memory_score_threshold` 필드는 유지.

## [1.0.7] — 2026-04-30

### 🔍 Stage UI 정합성 일괄 audit + dead/ambiguous 제거

#### Pipeline / Policy Gate
- `core/pipeline.py`: PRE_TOOL **OR** POST_RESPONSE 한쪽만 호출하던 분기를 두 훅 독립 호출로 변경. 도구 호출 + 응답 텍스트 동반 케이스에서 ContentGuard 의 응답 검증(PRE_MAIN/POST_RESPONSE) 누락 결함 수정. POST_RESPONSE 는 항상, PRE_TOOL 은 pending 있을 때만 호출.

#### s08 judge LLM system prompt override (확장성)
- `stages/s08_decide/strategies/judge_then_loop.py`: `_llm_judge_fallback` 의 `aux_call()` 호출에 `system=` 파라미터 전달. `evaluation_system_prompt` stage_param 으로 사용자 도메인별 평가관 톤·출력 규약 박을 수 있음.
- `core/stage_config.py` s08_decide.fields 에 `evaluation_system_prompt` textarea 추가.

#### Advanced 플래그 (확장 포인트)
- `core/stage_config.py` s06_context 의 18 fields 에 `"advanced": True` 박음 (cascade thresholds / microcompact / autocompact / sliding_window / intent_rules / rag_pd_* / chars_per_token / context_window / reranker / rerank_top_k). 외부 stage 도 자기 fields 에 같은 키 박을 수 있는 확장 포인트 — Frontend 가 자동으로 collapsible "Advanced settings" 섹션으로 격리.

#### 메타 / 코멘트 정합
- s04_tool description 에 "Strategy 카드 = 도구 발견 전략, s07_act 의 도구 실행 strategy 와 별개" 명시.
- s03_prompt thinking_mode field description 에 "⚠ Strategy 카드 픽이 우선" 명시.

#### 이식·프론트 동반 변경 (별도 레포)
- xgen-frontend `packages/harness-store`: `StageField` 인터페이스에 `advanced?: boolean` 추가.
- xgen-frontend `features/main-harness-stage-config`: `ConfigFieldsSection` 이 advanced flagged fields 를 collapsible 섹션으로 분리 렌더. RESOURCE_OWNED_FIELDS['s03_prompt'] 의 dead `prompt_id` → `system_prompt` 정정 (직접입력 textarea 와 Configuration 의 system_prompt 중복 자동 제거).
- locale: capability emptyTitle "Could not load" 에러 톤 → "lazy 발행 — 직접 입력 가능" 안내 톤.

## [1.0.6] — 2026-04-30

### 🐛 도구 호출 후 합성 답변 미완 함정 수정

**증상**: LLM 이 "CSV 파일을 읽어보겠습니다" 같은 짧은 인트로 + 도구 호출 → 도구 실행 → **답변 합성 없이 종료**. 사용자에겐 인트로 텍스트만 도달.

**원인**: `ThresholdDecide` 의 종료 판정이 `last_assistant_text` 가 비어있지 않으면 즉시 `LOOP_COMPLETE` 반환. 도구 호출 직전의 짧은 인트로가 `last_assistant_text` 에 박혀있어 다음 iter (도구 결과 합성) 가 발생 안 함. `_needs_synthesis_kick` safeguard 가 한 번 더 LLM 호출하지만 `tool_definitions=[]` 도구 비활성 + 컨텍스트 빈약으로 또 짧게 흘리고 끝남.

**수정**: `stages/strategies/_decide.py` ThresholdDecide.decide() 에 새 분기 추가 — `tools_executed_count > 0 + final_output 빔 + last_assistant_text < 200자` 면 `LOOP_CONTINUE` 로 다음 iter 강제 진입. `_SHORT_INTRO_THRESHOLD` 200 은 pipeline.\_needs_synthesis_kick safeguard 와 같은 임계 (정책 일관).

테스트: 4 케이스 PASS (도구후 짧은인트로 / 정상 긴답변 / 도구無 / pending tool).

## [1.0.5] — 2026-04-30

### 🧹 Dead trigger 코드 일괄 청소

#### `harness_mode='selected'` 제거 (캔버스 회귀 유산)
- `core/config.py`: `is_selected()` property + 코멘트 정리. 이제 `harness_mode` 는 `'autonomous' | 'off'` 2 값.
- `stages/s00_harness/stage.py`: selected 분기 (pinned_chosen/strategies/params 적용 흐름) 30 LOC 제거. PlanningCard 핀 버튼 → harness_mode='selected' 자동 전환 흐름 폐기. 핀 흐름이 다시 필요해지면 별도 stage 로 분리.

#### Tool Synthesis 인프라 제거 (자동 trigger 미연결)
- 자동 도구 합성 trigger 가 어디에도 묶여있지 않은 채로 인프라만 살아있어 dead chain 이었다 — Planner/ToolSelect 어디에도 `synthesize_and_register` 호출처 없음, 외부 caller 도 0건.
- `tools/synthesis.py` 파일 삭제 (334 LOC) — `SynthesizedTool` / `SynthesizedToolSource` / `synthesize_and_register` / NOMGraph 변환 / 갤러리 upsert 등.
- `tools/__init__.py`: `_preload_manifest_once` + `XGEN_HARNESS_PRELOAD_MANIFEST` env + `_MANIFEST_PRELOADED` 플래그 제거 (synthesis 도구 복원 전용).
- `events/types.py` / `core/llm_call.py` / `core/stage_config.py` / `providers/openai.py` / `compile/local_manifest.py`: "synthesized" 라벨 코멘트 5 곳 정리.

#### 이식·프론트 동반 변경 (별도 레포)
- xgen-workflow `controller/workflow/endpoints/harness.py`: `POST /auto-synthesize` endpoint + `HarnessSynthesizedToolDict` / `HarnessAutoSynthesizeRequest` 클래스 (143 LOC) 제거.
- xgen-frontend `packages/harness-store`: `pinned_strategies` 필드 + `setPinnedStrategy` action + 'selected' 자동 전환 로직 제거. 구 레코드의 `harness_mode='selected'` → `'autonomous'` 마이그레이션.
- xgen-frontend `features/main-harness-stage-config`: 'Selected' ModeChip 제거 (3개 → 2개), locale 의 `selectedTitle/Sub/Label` / `pinned*` / `noPins` / `noPinned` 키 제거.
- xgen-frontend `features/main-harness-chat`: locale 의 `pinHint` / `unpinHint` 제거.

진짜 자가증식 도구 루프가 필요해지면 Planner / ToolSelect 에 trigger hook 추가 후 별도 endpoint 로 부활.

## [1.0.4] — 2026-04-30

### 🛡 Policy Gate emit 본체 + decide defaults 레지스트리화

#### Bug fix (BLOCKER)
- `s05_policy/stage.py`: `_emit_policy_blocked` 메서드 본문 추가. 기존엔 호출(line 169 / 212)만 있고 본문 없어 Guard block 발생 시 `AttributeError` 로 파이프라인 크래시.
- 신규 `PolicyBlockedEvent` (events/types.py) 가 4 훅(PRE_MAIN/PRE_TOOL/POST_RESPONSE/LOOP_BOUNDARY) 차단을 SSE 로 발행 — UI 가 어떤 Guard / 어떤 시점 / 어떤 도구 막혔는지 가시화.
- `event_to_dict` 매핑 정합: `event_type=policy_blocked` 으로 6 필드 (guard_name / hook / reason / severity / tool_name / timestamp) 직렬화.

#### 박제 정리 (확장성)
- `stages/strategies/_decide.py`: `max_retries=3` magic number 박제 → `_DECIDE_DEFAULTS` 레지스트리 + `register_decide_defaults(**kwargs)` / `get_decide_default(key)` 외부 override API. v1.0.2 "정책 default 박제 정리" 룰을 strategy 영역에도 적용.
- `validation_threshold=0.7` 도 동일 레지스트리화.

#### 코멘트 / 메타 정합
- `core/stage.py:99`: `"실행 순서 (0~11)"` → `"v1.0 통합 기준 0~9"` (s00 + s01~s09).
- `api/router.py:9, 52`: `"12개 스테이지"` → 동적 표현 (registry 기반 — 외부 stage 자동 합류).
- `compile/npm_spec.py:78`: `harness_version=">=0.28.0,<0.29"` 박제 default → `""` (snapshot.harness_version 가 항상 override 함을 코멘트로 명시. 박제 fallback 회귀 위험 제거).

#### 이식·프론트 동반 변경 (별도 레포)
- xgen-workflow `harness_bridge/xgen_streaming.py`: `PolicyBlockedEvent` SSE 변환 추가 (`event_kind: "policy_blocked"`).
- xgen-frontend `packages/harness-store/src/index.ts`: `policy_blocked` → `type: 'policy.blocked'` 매핑.
- xgen-frontend `features/main-harness-chat/src/components/event-log.tsx`: `EVENT_META['policy.blocked']` 항목 추가 (⛔ 차단 배너).

## [1.0.0] — 2026-04-30

### 🔥 BREAKING — 11→10 Stage 고결화 통합

Spec: `docs/harness/2026-04-29-stage-consolidation-v1.md` (LOCKED v1.1).

#### Stage 변경
- **삭제 (분해 흡수)**:
  - `s05_strategy/` → CoT/ReAct → `s03_prompt`, capability discovery → `s04_tool`, intent routing → `s06_context`
  - `s08_judge/` → `s08_decide` 의 `judge_then_loop` strategy 로 격하
  - `s10_save/` → `s09_finalize` 의 `persist` strategy 로 격하
  - `s12_publish/` → 빈 dead slot 삭제
- **번호 시프트**: `s09_decide → s08_decide`, `s11_finalize → s09_finalize`
- **격상**: `s05_policy` 가 role-based 단독에서 **일반 순번 진입 + 4훅 동시 작동**.
  PRE_TOOL 시점 자연 매핑 (s04 다음, s06 전). PRE_MAIN/POST_RESPONSE/LOOP_BOUNDARY 훅도 그대로.
  Guard 가 block 하면 `PipelineAbortError` 즉시 발생 → "규제 위반 → 실행 차단" 보장.

#### 박제 풀기 (4 신규 레지스트리)
- `xgen_harness.prompt_templates` — Identity / Rules / Thinking mode 템플릿
- `xgen_harness.evaluation_criteria` — Judge 평가 기준
- `xgen_harness.output_formatters` — Finalize 출력 포맷
- `register_persist_defaults / register_capability_discovery_defaults` — 임계값
- `_default_identity / _default_rules` 함수 안 박제 → DEFAULT_IDENTITIES / DEFAULT_RULES dict

#### 코드 변경
- `core/config.py`: ALL_STAGES 11→9 (s00 별도 통제탑), REQUIRED_STAGES = `{s01_input, s08_decide, s09_finalize}`
- `core/presets.py`: 4 preset (minimal/chat/agent/evaluator/rag) 재정의 + `multi_agent` 추가, `register_preset()` 공개
- `core/stage.py`: STAGE_DISPLAY_NAMES (10 entry)
- `core/stage_io.py`: STAGE_PARAM_SCHEMAS 정리
- `core/stage_config.py`: STAGE_CONFIGS (UI fields) 분배 — s03/s04/s06 흡수, s08_decide/s09_finalize 통합
- `core/strategy_resolver.py`: evaluation/decide 슬롯 등록 위치 → `s08_decide`
- `core/registry.py`: `MultiAgentPlannerStage` 등록 슬롯 → `s00_harness` 의 `multi_agent`
- `core/builder.py`: `with_validate()` 가 `active_strategies['s08_decide']='judge_then_loop'` 셋
- `orchestrator/multi_agent_planner.py`: `PLAN_SLOT = "s00_harness"`, `order = 0`
- `errors/hierarchy.py`: ValidationError 의 stage_id → `s08_decide`

#### Stage ID 별칭 (구→신, 자동 정규화)
- `s05_plan / s05_strategy → s03_prompt`
- `s09_validate / s09_judge / s08_judge → s08_decide`
- `s09_decide / s10_decide → s08_decide`
- `s11_save / s10_save → s09_finalize`
- `s12_finalize / s12_complete / s11_finalize → s09_finalize`

#### 테스트
- `tests/test_import_smoke.py`: 13→10 stage / `REQUIRED_STAGES` 갱신 / wheel export 제거
- `tests/test_plan_fingerprint.py`: 10-stage CATALOG / s08_decide judge_then_loop
- 22/22 PASS

#### 마이그레이션 가이드
- `register_strategy("s05_strategy", ...)` 호출 → 삭제. CoT 는 `register_thinking_mode()` 로.
- `register_strategy("s08_judge", "evaluation", ...)` 호출 → `register_strategy("s08_decide", "evaluation", ...)`
- `disabled_stages={"s05_strategy"}` 설정 → 제거 (해당 책임은 s03/s04/s06 의 stage_param 으로)
- `disabled_stages={"s08_judge"}` 설정 → `active_strategies={"s08_decide": "threshold"}` (judge 비활성)
- `disabled_stages={"s10_save"}` 설정 → `active_strategies={"s09_finalize": "default"}` 또는 `"noop"`

## [0.29.0] — 2026-04-29

### 🔥 BREAKING — Python wheel 채널 완전 제거 (npm 단일 채널화)

v0.28.0 의 npm 채널이 안정화되어 wheel/PyPI 기반 컴파일 코드 일괄 제거.

### 삭제
- `xgen_harness/compile/wheel.py` — wheel 빌더 (420 LOC)
- `xgen_harness/compile/mcp_server.py` — wheel 안의 stdio MCP wrapper (120 LOC)
- `xgen_harness/compile/deps.py` — wheel 의존성 resolver (238 LOC)
- `xgen_harness/compile/templates/` — wheel Python 패키지 템플릿
- public API 제거: `build_wheel`, `compile_workflow`, `WheelBuildResult`,
  `serve_mcp`, `run_mcp_blocking`, `MCPNotInstalledError`, `DependencyResolver`,
  `resolve_dependencies`, `register_dependency_rule`, `DependencyRule`

### 마이그레이션
- 사용자가 v0.28 이전 publish 한 wheel session 은 verify-self-heal 시 명시
  에러로 마킹 — 사용자가 재 publish 1회 (자동 npm 변환).
- `compile_workflow` import 하던 외부 코드는 `compile_workflow_to_npm` 으로 이전.
- `compile_nom_graph` 도 npm tarball 반환으로 변경 (NpmPackResult).

### 비파괴
- npm 채널 모든 helper (`compile_workflow_to_npm`, `freeze_*_tool` 등) 그대로.
- `xgen-harness-engine-node` (npmjs) 와 별개 — 호환성 보장.

## [0.28.0] — 2026-04-29

### 🎯 npm 컴파일 채널 1차 — 외부 MCP 생태계 호환

기존 Python wheel + inline pip install 패턴 (mcp-station 의 다른 MCP 서버들과
이질적) 대신 표준 npx 패턴으로 전환. wrapper 는 minio presigned tarball, engine
은 npmjs registry 의 `xgen-harness-engine-node`. fully equivalent — 모든 stage
설정값 spec.json 1:1.

### 신규
- `xgen_harness/compile/npm_spec.py` — `HarnessSpec` (HarnessConfig 1:1) +
  `freeze_http_tool` / `freeze_xgen_node_tool` (alias) / `freeze_mcp_session_tool`
  / `freeze_rag_tool`
- `xgen_harness/compile/npm_pack.py` — `compile_workflow_to_npm()` +
  `build_npm_package()` + `NpmPackResult` (dist_name / wheel_path alias 호환)
- `node-engine/` — TypeScript 신규 패키지 (`xgen-harness-engine-node` npmjs publish)
  - 13 stage 1:1 포팅 (s00 ~ s11)
  - 4 strategy 본문 (cot_planner / react / capability / + none)
  - 6 context strategy (token_budget / sliding_window / microcompact /
    context_collapse_overlay / autocompact_llm / cascade) + RAG dispatch
  - llm_judge 본문 (4 기준 가중평균)
  - 4 빌트인 Guard (cost_cap / max_loop / pii_block / domain_allow) +
    `registerGuard()` 확장점
  - capability binding (spec.config.capabilities → tool_definitions 자동 합류)
  - 4 provider — Anthropic / OpenAI / vLLM (Qwen `<tool_call>` XML parser
    1:1 포팅, 0.27.1 의 알고리즘 그대로)
  - frozen tool dispatch (http / mcp_session / rag / noop)
  - MCP stdio server (`@modelcontextprotocol/sdk`)

### 변경 (deprecated 마킹)
- `compile/wheel.py` — Python wheel 빌더, v0.30 제거 예정
- `compile/mcp_server.py` — wheel 안의 stdio wrapper, v0.30 제거 예정

### 비파괴
- 기존 `compile_workflow` (wheel) 그대로 import 가능 — 마이그레이션 기간 동안
  외부 사용자 깨지지 않음.

## [0.27.1] — 2026-04-29

### 🐛 vLLM/Qwen native `<tool_call>` 텍스트 파서
prod 회귀: `provider=vllm` + `model=Qwen3.5-27b` 사용 시 답변에 `<tool_call><function=foo>...</function></tool_call>` XML 이 그대로 노출되고 도구 호출 발동 안 함 ("하다 마는" 답변).

원인: vLLM 의 OpenAI 호환 endpoint 가 `--enable-auto-tool-choice --tool-call-parser hermes` 옵션 없이 serve 되면, Qwen 이 학습된 native 형식을 text content 에 박아 응답. 엔진 OpenAIProvider 는 `tool_calls` 필드만 보고 text 를 그대로 stream → 사용자 화면 노출 + tool 미발동 + s07_act 미진입.

수정: `xgen_harness/providers/openai.py` 에 `_parse_native_tool_call` 추가. 두 형식 지원:
- Hermes JSON: `<tool_call>{"name":"x","arguments":{...}}</tool_call>`
- XML parameter: `<tool_call><function=name><parameter=k>v</parameter>...</function></tool_call>`

`_stream_request` 에 chunk 경계 buffering 도입 — 마지막 `_TOOL_CALL_OPEN_LEN=11`자만 보류 후 flush, `<tool_call>` 발견 시 본문은 별도 buffer 누적, `</tool_call>` 발견 시 파싱해 `ProviderEvent(TOOL_USE)` emit. JSON 파싱 실패 시 원본 텍스트 그대로 fallback (no breakage). vLLM hermes parser 가 활성된 정상 환경에선 `tool_calls` 필드 경로로 와서 text parser pass-through.

자가감사 4축 PASS — chunk 5종 시뮬 + JSON/XML/string-encoded args/empty/plain text 6 단위 테스트 통과. 인프라 변경 없이 Qwen tool 사용 회복.

## [0.27.0] — 2026-04-29

### 🐛 BUG-A — `harness_config.preset` 키 자동 expand
이전: `preset="minimal"` 만 박으면 엔진이 무처리(`pass`) → 모든 13 stage 그대로 실행 → 사용자 의도와 정반대.
현재: `core/config.py:from_workflow` 에서 `from .presets import PRESETS` 로 동적 lookup → `disabled_stages` / `active_strategies` / `max_iterations` / `temperature` 자동 채움. **사용자 명시 값은 항상 우선**. unknown preset 이름은 `WARNING` 으로 등록된 preset 목록을 동적 출력 (외부 `PRESETS.register` 도 자동 흡수 — 확장성 보존).

### 🐛 BUG-B — `selected_tools` list 형태 글로벌 화이트리스트
이전: dict (`{source_id: [name]}`) 만 처리. list 받으면 silently fallback → 모든 도구 LLM context dump → 비용 폭증 (실측 단순 채팅 1턴에 16K 토큰).
현재: `stages/s04_tool/stage.py` 에서 list 형태도 정규화 → 글로벌 화이트리스트로 적용.

### 🐛 BUG-C — `max_retries` 가 `max_iterations` 와 fallback
이전: max_retries default=3 hardcoded. 사용자가 max_iterations=5 늘려도 retry cap=3 에서 잘림 → UI 속임.
현재: `max_retries` 명시 안되면 `max_iterations` 와 동기화 (둘 다 횟수 cap). 사용자 명시 시 그게 우선. UI 단일 컨트롤(`max_iterations`) 일관성.

### ✨ UX-2 — `s07_act` 에 `strict_no_error` variant + s09 wiring
도구 1개라도 실패 시 즉시 중단. `state.metadata['s07_strict_failed']` 박고 **`s09_decide` 가 즉시 `LOOP_COMPLETE` 로 stop** (wiring 완성).

### ✨ UX-4 — `s08_judge` default 를 `none` 으로
이전: `llm_judge` default → 단순 채팅에도 매 iteration 마다 추가 LLM 호출.
현재: `none` default. preset='evaluator' 가 명시 선택하므로 evaluator 사용자 영향 없음.

### ✨ UX-7 — `s02_history` 에 `none` variant
이력 무시 (매 turn 독립 실행). 변형 description 명확화.

## [0.26.13] — 2026-04-27

### 🐛 OpenAI tool schema 정규화 — Tavily 류 MCP 도구 400 수정

운영 SSE 에서 `mcp_tavily_search_mcp` 호출 시 OpenAI 가:

```
HTTP 400: Invalid schema for function 'mcp_tavily_search_mcp'.
Please ensure it is a valid JSON Schema.
param: tools[1].function.parameters
code: invalid_function_parameters
```

원인: v0.26.2 의 `_convert_tools` 는 `{"type":"object"}` 빈 properties 케이스만
patch 하고, MCP 서버가 보내는 풍부한 schema 는 그대로 OpenAI 로 흘렸다. Anthropic
은 관대하게 수용했지만 OpenAI Function calling 은 다음 패턴들을 거부:

1. `"type": ["string", "null"]` 배열 타입 (Tavily 의 nullable 표현)
2. `"anyOf": [{"type": "string"}, {"type": "null"}]` (1번의 다른 표현)
3. `"$ref": "#/definitions/..."` (외부 ref — OpenAI 가 인라인 풀어주지 않음)

vLLM / Anthropic 검증 환경에서는 통과되어 회귀로 잡히지 않다가, OpenAI provider
사용 워크플로우에서만 첫 호출에서 SSE 끊김으로 노출됨.

### 변경

- `xgen_harness/providers/openai.py` 에 `_normalize_for_openai(schema)` 헬퍼 신설.
  단방향 평탄화 — 의미 손실은 nullable 표현이 "필수 아닌 단일 type" 으로 약화되는
  정도. OpenAI Function calling 은 어차피 nullable 을 수용 안 하므로 정상 통로.
    - type 배열 → null 제거 후 단일 type
    - anyOf / oneOf 안에 null branch 만 빼면 단일이 되는 경우 부모로 평탄화 (enum 등 키 끌어올림)
    - `$ref` 는 drop (빈 dict 자리만 유지)
    - dict / list 모두 재귀 처리
- `_convert_tools` 가 v0.26.2 의 빈 properties 보정 **이전 단계** 에서 호출.

### 적용 범위

- OpenAI provider 만 영향. Anthropic / LangChainAdapter 무변경 (각자 자체 변환기).
- 외부 provider (Bedrock / Gemini 등 entry_points 기여자) 는 base `_sanitize_tool_defs`
  + 자기 변환기를 쓰므로 영향 없음. 이 정규화가 base 로 올라가면 자동 상속 가능
  (별도 PR — 본 릴리즈는 OpenAI 한정 안전망).

### 자가검증

7/7 PASS — Tavily 류 6 패턴 (type 배열 / anyOf-null / $ref / 중첩 type 배열 / 정상
필드 보존 / enum 끌어올림) + v0.26.2 빈 properties 회귀.

```python
from xgen_harness.providers.openai import _convert_tools
out = _convert_tools([{
    'name': 'tavily_search',
    'input_schema': {'type': 'object', 'properties': {
        'time_range': {'type': ['string', 'null']},
        'topic': {'anyOf': [{'type': 'string', 'enum': ['general', 'news']}, {'type': 'null'}]},
        'extra': {'$ref': '#/definitions/Extra'},
    }},
}])
# out[0].function.parameters.properties:
#   time_range: {type: 'string'}                          ← 배열 평탄화
#   topic:      {type: 'string', enum: [general, news]}   ← anyOf-null 평탄화 + enum 끌어올림
#   extra:      {}                                        ← $ref drop
```

## [0.26.12] — 2026-04-27

### 🐛 `HarnessConfig.from_workflow` 가 `aux_max_tokens` 안 받던 누수 (v0.26.11 라이브 적발)

`harness_config.aux_max_tokens` 박아도 `from_workflow` 변환 시 dict 키를 읽지 않아 default 500 으로 덮였음.
1줄 fix: `aux_max_tokens=int(harness_config.get("aux_max_tokens", 500))`.

이제 워크플로우 단에서 `HarnessConfig(aux_max_tokens=300)` 또는 dict `{"aux_max_tokens": 300}` 박으면 즉시 반영.

## [0.26.11] — 2026-04-27

### ✨ 외부 확장 6 결함 일괄 정리 (확장성·연동성·하드코딩 4축 자가감사 후속)

**#1+#5 — `pyproject.toml` 외부 lock-in 빈 본 6 그룹 추가**
- `orchestrators` / `sandbox_verifiers` / `tools` (gallery) / `phases` / `node_plugins` / `model_pricing`
- 외부 작업자가 어떤 그룹 이름이 valid 인지 한눈에 확인. 데이터 추가만 — break change 0.

**#2 — silent contract 결판 (entry_points discovery 추가)**
- `fan_out_strategies` (`orchestrator/multi_agent_planner.py`): pyproject 빈 본은 있는데 discovery 코드 0 → `_discover_fan_out_from_entry_points()` 추가. callable / dict 모두 허용.
- `evaluation_criteria` (`stages/s08_judge/stage.py`): 동일 누수 → `_discover_evaluation_criteria_from_entry_points()` 추가. dict / list 모두 허용.
- `option_sources` 는 이식측 owns (`harness_options_registry.py:683`) — 이미 discovery 있어 silent contract 아님.

**#3 — `register_model_pricing()` API + entry_points 자동 발견**
- `stages/strategies/token_tracker.py`: PRICING dict 가 외부에서 추가 못 하는 closed table 이었음.
- `register_model_pricing(name, input, output, cache_read=None, cache_write=None)` API 신설.
- entry_points 그룹 `xgen_harness.model_pricing` 자동 스캔 — 사내 vLLM / 자체 호스팅 모델 가격을 외부 패키지 한 줄로 등록.

**#4 — auxiliary LLM 호출 통합 헬퍼 `aux_call()`**
- `core/llm_call.py`: 본문 호출(`_single_call`) 과 분리한 보조 호출 표준 헬퍼.
- max_tokens 단일 진실 소스 = `state.config.aux_max_tokens`.
- `state.llm_call_count` 자동 누적 + `state.token_usage` 누적 + `_estimate_cost` 합산 + `StageSubstepEvent` 자동 emit (verbose 가시성 일관).
- 적용 3 곳: `s08_judge._execute_llm_judge` / `strategies/evaluation.LLMJudgeEvaluation` / `s06_context.l5_autocompact`.
- `xgen_harness.aux_call` 로 export — 외부 strategy 도 같은 헬퍼로 보조 호출 통합 가능.

**#6 — `HarnessConfig.aux_max_tokens` 필드화 (매직넘버 500 박힌 4 건 제거)**
- 4 호출부 모두 `aux_call` 경유 → 코드 grep `max_tokens=500` 0 건.
- 사용자 워크플로우 단에서 `HarnessConfig(aux_max_tokens=...)` 로 override 가능.

검증: 자가검증 4축 (확장성 / 연동성 / 하드코딩 / 무침범) 통과. `feedback_no_hardcoding_extensibility` 정합.

## [0.26.10] — 2026-04-27

### 🐛 DAG orchestrator — sub-Pipeline DoneEvent forward 누수

**증상**: DAG 자율 테스트에서 첫 노드만 응답 출력되고 두 번째 노드 이벤트가 클라이언트에 도달 못 함. 외부에서 "DAG 실행 타임아웃" 으로 끊김.

**원인**: `orchestrator/dag.py:_forward_events()` 가 sub Pipeline 의 DoneEvent 도 그대로 외부 emitter 로 forward. DoneEvent 가 external_emitter 로 들어가는 즉시 `EventEmitter.stream()` 의 `if isinstance(event, DoneEvent): break` (events/emitter.py:102) 가 외부 stream 을 종료 — 후속 노드 이벤트 전체 누락.

**fix**: `_forward_events()` 가 sub Pipeline 의 DoneEvent 만 skip. DAG 전체 DoneEvent 는 `run()` 마지막에 별도 emit (line 229) 하므로 정상 종료 신호 유지. 다른 이벤트 (Stage / Metrics / ToolCall / Error / …) 는 그대로 forward.

검증: 두 노드 모두 final_output 정상 도달 + DAG-level DoneEvent 마지막에 한 번만 emit → 외부 stream 깔끔 종료.

## [0.26.9] — 2026-04-26

### 🐛 MCPClient header forward — production tools=0 회귀 수정

라이브 검증 (saleskit JWT 로 production 직접 호출) 결과:
- `/api/mcp/sessions/{id}/tools` 직접 호출 → tools=1 (Time Server) ✅
- `/api/agentflow/harness/tool-sources` 의 mcp-sessions source → tools=0 ❌

원인: `xgen_harness/tools/mcp_client.py` 의 `list_tools` / `call_tool_raw` /
`check_session` 가 `httpx` 호출 시 헤더를 0개 보냈다. self-loopback 호출이라
컨테이너 안에서 user 컨텍스트가 손실되고 station 이 401/빈응답 → silent
`except: return []` → Stage 4 의 MCP Sessions Box 가 영구 비어있음 → LLM 이
MCP 도구를 호출 못 함. CustomAPIToolSource 는 이미 동일 패턴으로 헤더를
forward 하고 있어, 이 회귀는 mcp_client.py 단독 누락이었다.

### 변경
- `xgen_harness/tools/mcp_client.py` 상단에 `_forward_request_headers()` 헬퍼
  추가 — `xgen_harness.tools.use_request_headers` contextvar 에서 헤더를
  끌어와 Authorization / Cookie / x-user-* / x-workspace-id 화이트리스트로
  필터.
- `MCPClient.list_tools(session_id)` — `httpx.get` 에 `headers=` 전달.
- `MCPClient.call_tool_raw(session_id, ...)` — `httpx.post` 에 `headers=`
  전달.
- `MCPClient.check_session(session_id)` — `httpx.get` 에 `headers=` 전달.

### 효과
- 인증된 사용자가 Stage 4 펼치면 자기 MCP 세션의 도구가 실제로 노출.
- LLM 이 tool_use 로 MCP 도구 호출 가능 (이전엔 도구 정의 자체가 없어서
  call 도 못 했음).
- SDK 직접 사용(엔진 API 바깥)은 contextvar 비어있어 빈 dict — 무영향.

## [0.26.8] — 2026-04-26

### 🐛 i18n fix: description_en in EN locale leaked Korean text

production /api/agentflow/harness/stages 응답에서 s04_tool / s05_policy 두
stage 가 영문 locale 에서도 description 이 한국어로 노출되는 회귀 발견.

**원인**: `_resolve_stage_self_describe()` 가 `_compose_from_class_attrs()` 를
먼저 시도. 이 함수는 docstring 첫 단락을 description_ko / description_en 양쪽
에 그대로 박는다 ("i18n 없음. 추후 gettext" TODO). class attr `when_to_use`
가 set 된 stage 는 STAGE_CONFIGS dict 의 명시 영문 description 이 가려졌다.

### 변경
- `xgen_harness/core/stage_config.py::_resolve_stage_self_describe()` 우선순위
  변경:
    1. **explicit `describe_config()` override** (Stage 베이스 기본은 None)
    2. **STAGE_CONFIGS dict 항목** 우선 (명시 ko / en 분리)
    3. auto-compose from class attrs (외부 Stage 폴백 only)
- `xgen_harness/stages/s05_policy/stage.py::PolicyGateStage.describe_config()`
  override 추가 — 명시 ko / en 분리, machine meta 명시.

### 효과
- 13 stage 모두 영문 locale 에서 영문 description 노출.
- s04_tool, s05_policy 회귀 동시 해결 (같은 root cause).

## [0.26.7] — 2026-04-26

### 🟡 UX 함정 방지: tool 호출 후 final answer 보강

비교 테스트 (saleskit JWT, 도구 활성/비활성 vs max_iter 변화) 로 발견:

- `max_iter=1` + 도구 활성 시: LLM 이 첫 iter 에서 도구만 호출 → 도구 결과는
  state 에 들어오지만 LLM 이 final 답변 텍스트 만들 두 번째 iter 가 없음.
- `metrics.output_tokens=73, output_length=0, tools_executed=2` 같은 모순 패턴.
- production default `max_iter=10` 환경에선 자연스럽게 다음 iter 에서 답변되므로
  드러나지 않던 UX 함정. 단 사용자가 max_iter 줄이면 즉시 빈 응답.

### 변경
- `xgen_harness/core/pipeline.py` — Phase B 종료 후 Phase C 직전:
  `last_assistant_text` / `final_output` 모두 빈 채 + `tools_executed_count > 0`
  이면 `state.tool_definitions = []` 로 도구 비활성 + 1회 보강 main_call.
  LLM 이 도구 결과만 보고 답변 텍스트 만들도록 강제.
- 보강 호출 후 `state.tool_definitions` 원복 (다음 iteration / 외부 코드 영향 0).

### 효과
- 라이브 비교: A (도구 0) ✅, B (도구+max=1) ❌→ ✅, C (도구+max=3) ✅
- 모든 max_iter 값 + 도구 활성 조합에서 사용자가 빈 응답 받지 않게 보장.

## [0.26.6] — 2026-04-26

### 🚨 DAG orchestrator — PipelineState init TypeError fix

UI 헤더 버튼 라이브 검증 (Save/Deploy/Compile/Galleries/Config + DAG/Publish/MCP)
중 발견:
- DAG 멀티 하네스 실행 (`POST /dag/execute/stream`) 시 노드 결과:
  `error: "PipelineState.__init__() got an unexpected keyword argument 'tool_definitions'"`
- 모든 DAG 노드 실행이 100% 실패 (success=false, output="")

원인: PipelineState 가 v0.11.22+ 에서 `tool` 을 `ToolGroup` 으로 도메인 그룹화
하면서 `tool_definitions` 는 property shim 으로만 노출. dataclass `__init__`
kwarg 로는 못 받음.

orchestrator/dag.py:255 가 `PipelineState(tool_definitions=...)` 로 호출 →
TypeError. v0.11.22 도메인 그룹화 시 dag.py 동기화 누락된 것으로 추정.

### 변경
- `xgen_harness/orchestrator/dag.py` — `tool_definitions` 를 init kwarg 에서
  제거하고 instance 생성 후 setter 로 박음 (`state.tool_definitions = ...`).

### 검증
라이브 saleskit 계정 + DAG 1 node 그래프:
- 이전: `node_results.n1.error = "PipelineState.__init__() got an unexpected..."`
- v0.26.6: 노드 정상 실행 + 응답 생성 기대 (컨테이너 재시작 후 재검증)

## [0.26.5] — 2026-04-25

### 🚨 Anthropic thinking — max_tokens 자동 보정

PARTIAL/N/A 재검증 (88 항목 검증의 후속) 으로 발견:

- 사용자가 `thinking_enabled=true` + `thinking_budget=5000`, `max_tokens=200` 으로
  설정하면 Anthropic API 가 무조건 HTTP 400 거부:
  > `max_tokens` must be greater than `thinking.budget_tokens`.
- engine stage_config 의 default 값도 동일 함정: `max_tokens=8192` < `thinking_budget=10000`.
  사용자가 thinking 토글만 켜고 default 그대로 두면 즉시 400.
- 결과: thinking 기능 자체가 production 0 fire 였던 가능성 높음.

### 변경
- `xgen_harness/providers/anthropic.py:chat()` — thinking enabled 시 자동 보정:
  `max_tokens <= budget_tokens` 이면 `max_tokens = budget_tokens + 1024` (buffer).
  사용자 설정 무시 아니라 안전 보장 — 답변용 토큰 0개로 떨어지지 않게.

### 검증
- 라이브 saleskit 계정 + claude-haiku-4-5-20251001 + thinking_budget=5000:
  이전: HTTP 400 즉시 실패
  v0.26.5: 자동 max_tokens=6024 로 보정되어 정상 응답

## [0.26.4] — 2026-04-25

### 🚨 batch transport (stream=False) 응답 누락 fix

per-feature 라이브 검증 (saleskit JWT, 49 항목 중 1 FAIL) 으로 발견:
`s00_harness.strategy=batch` 선택 시 LLM 응답 받았으나 (metrics output_tokens=9)
`output_length=0` — state.final_output 빈 채.

원인: `OpenAIProvider._batch_request` 는 응답 전체 text 를 STOP 이벤트의 .text
필드에 담아 단일 yield. 그러나 `core/llm_call.py:_single_call` 의 STOP 핸들러
(line 271-274) 는 `output_tokens` 만 처리하고 `event.text` 를 무시. 결과:
- streaming: TEXT_DELTA 들이 누적되어 result_text 정상
- batch: text_parts 빈 채로 result_text="" → state.last_assistant_text=""

이로 인해 batch 모드는 메타데이터 (토큰/비용) 만 정상이고 사용자 응답이 사라짐.

### 변경
- `xgen_harness/core/llm_call.py` STOP 핸들러에 `event.text` 처리 추가:
  TEXT_DELTA 가 한 번도 안 왔는데 STOP 이 text 가지고 오면 그것을 result 로.
  MessageEvent 도 함께 emit (UI/SSE 호환).

## [0.26.3] — 2026-04-25

### 🚨 s10_save record 컬럼명 — 실 DB schema 정합

라이브 검증 (saleskit 계정 + OpenAI gpt-4o-mini, s04_tool 비활성으로
SynthesizedToolSource 우회) 중 발견:

- 실행 자체는 13 Stage 완주, judge 1.00 pass 정상.
- BUT s10_save 에서 PostgreSQL 에러:
  `column "input_data" of relation "harness_execution_log" does not exist`
- 실 DB schema 컬럼: `input_text`, `output_text` (text 타입).
- 엔진 record 는 `input_data` (dict) / `output_data` (dict) 로 보냄 → 미스매치.
- 결과: 매 실행 graceful 하게 inserted_id=None 으로 끝나서 사용자에겐 안 보임.
  `/executions` 리스트에 row 0개. 멀티턴 chat 도 thread 빈 채로.

이는 **B2 (insert vs insert_record 시그니처)** 와는 별개의 결함. 시그니처 fix
한 직후에도 컬럼명 미스매치로 여전히 실패. 라이브 검증으로만 발견 가능했음.

### 변경
- `xgen_harness/stages/s10_save/stage.py` — record 의 `input_data` (dict) →
  `input_text` (str, 5000자 truncate), `output_data` (dict) → `output_text`
  (str, 50000자 truncate). DB 컬럼이 text 타입이라 평문 직렬화.

## [0.26.2] — 2026-04-25

### 🚨 OpenAI tool schema 호환성 — 400 거부 수정

라이브 검증 (saleskit 계정으로 실 워크플로우 실행) 중 발견:
- SynthesizedToolSource 가 자동 등록한 `naver_tool` 의 input_schema 가
  `{"type": "object"}` (properties 누락) — OpenAI 호환 안 됨.
- OpenAI 응답: `"Invalid schema for function 'naver_tool': In context=(),
  object schema missing properties."` HTTP 400.
- 이로 인해 OpenAI provider 사용하는 모든 실행이 SSE error event 후 종료.
  (vllm 은 schema 검증 느슨해서 통과되어 이 결함 가시화 안 됐음.)

### 변경
- `xgen_harness/providers/openai.py:_convert_tools` — `type=object` 인데
  `properties` 키 없으면 자동으로 `{}` 추가. OpenAI strict schema 검증 통과.

### Breaking 없음

## [0.26.1] — 2026-04-25

### 🔧 s06_context.files 필드 부활 — 실 wiring 추가

v0.26.0 에서 D3 (`s06_context.files`) 를 dead UI 로 판정하고 제거했으나, frontend
ResourceSelector 가 여전히 files multi_select UI 를 갖고 있어 사용자 클릭이
무효화되는 문제. 엔진 측에서 진짜로 작동하도록 wiring 추가하고 필드 부활.

### 변경
- `xgen_harness/stages/s06_context/stage.py` — execute() 가 `files` stage_param
  을 read 해서 `metadata_filter` 의 `file_name` 키로 자동 라우팅 (union with
  사용자 textarea 입력). xgen-documents `DocumentSearchRequest.filter` 가 그대로
  처리하므로 검색 범위가 실제로 좁혀짐.
- `xgen_harness/core/stage_config.py` — `files` 필드 다시 등록 (description 에
  "metadata_filter.file_name 자동 라우팅" 명시).

### Breaking 없음
v0.26.0 의 다른 변경 (D1/D2/D4 + D5/D6/D7 + B7) 은 그대로.

## [0.26.0] — 2026-04-25

### 🧹 Dead UI 정리 + Label-only strategy 보강 + EventQueue 백프레셔

전수 기능 검증 보고서 (`docs/confluence/2026-04-25-harness-functional-verification.md`)
가 발견한 14건 결함 중 엔진 권한 안의 7건 (D1~D7) + 1건 (B7) 일괄 fix.

### Dead UI 4건 — UI 노출되나 코드가 한 번도 read 안 하던 stage_param 제거

- **D1 `s01_input.provider` 필드 제거** (`core/stage_config.py`)
  stage 자기 docstring 에 "s01 은 읽지도 쓰지도 않는다" 명시. provider 결정은
  HarnessConfig top-level (ConfigPanel) 단일 진실 소스. stage_param 으로 중복
  노출이 사용자 거짓말이었음 (UI 클릭 → 환경 무반영).
- **D2 `s02_history.memory_source` 필드 제거**. grep 0 hit. 실 동작은 strategy
  분기 + ServiceProvider.documents 주입 여부로만 결정.
- **D3 `s06_context.files` 필드 제거**. grep 0 hit. 파일 단위 검색은
  `metadata_filter` (file_name) 또는 `folders` 자동 확장 사용.
- **D4 `s09_decide.max_iterations` stage_param 제거**. Pipeline 이 top-level
  `state.config.max_iterations` 만 read. ConfigPanel 의 글로벌과 이중 노출되어
  사용자가 어느 값이 박히는지 헷갈림. `max_retries` 만 보존.

### Label-only strategy 3건 정리

- **D5 `s03_prompt.simple` strategy 제거** (`stages/s03_prompt/stage.py:294`).
  StrategyInfo 선언만 있고 execute() 분기 코드 없음. section_priority 와 동일
  동작이라 라벨 거짓말. list_strategies() 에서 라벨 자체 제거.
- **D6 `s04_tool.none` strategy 분기 신규 구현** (`stages/s04_tool/stage.py:55-61`).
  이전엔 분기 grep 0 hit. 사용자가 도구 인덱싱을 명시적 비활성화 원할 수 있어
  (디버깅 / 도구 무관 단발) execute() 진입 직후 short-circuit. should_bypass
  자동 감지와 달리 도구·RAG·capability 가 *있어도* 강제 skip.
- **D7 `s10_save.noop` strategy 분기 신규 구현** (`stages/s10_save/stage.py:29-35`).
  동일 패턴. save_enabled=false 만이 실 wiring 이었으나 strategy=="noop" 도
  동등하게 skip 하도록 분기 추가.

### B7 — EventEmitter queue 백프레셔

- **`events/emitter.py:31`** queue_size **1000 → 8000**.
  production 라이브에 `Event queue full, dropping event: MessageEvent` 분당
  ~75건 발생 (긴 응답 시 SSE 컨슈머가 못 따라가 message.delta drop).
- `_drop_count` 누적 카운터 + warning 폭주 방지 (1회 + 100회마다 1회만 노출).

### Breaking — 사용자 영향 (마이너)

- **stage_params 에서 위 4개 키 제거됨** (`s01_input.provider`,
  `s02_history.memory_source`, `s06_context.files`, `s09_decide.max_iterations`).
  기존 저장된 워크플로우가 이 키를 가지고 있어도 무시됨 (이전에도 read 0회라
  기능적 변화 없음).
- `s03_prompt` 의 strategy 옵션에서 `simple` 사라짐. 기존 active_strategies 가
  `simple` 이면 실질적으로 default (section_priority) 와 동일 동작이던 것이
  유지되므로 사용자 영향 없음.

### 검증

- `pytest test_serialization.py test_compile.py` 31/32 PASS (1 fail = dummy
  API key, 코드 무관).
- 엔진 직접 검증:
  ```python
  from xgen_harness.core.stage_config import get_all_stage_configs
  configs = get_all_stage_configs()
  assert configs['s01_input']['fields'] == []   # D1
  assert 'files' not in [f['id'] for f in configs['s06_context']['fields']]  # D3
  assert configs['s09_decide']['fields'] == [{'id': 'max_retries', ...}]     # D4
  from xgen_harness.events.emitter import EventEmitter
  assert EventEmitter()._queue.maxsize == 8000  # B7
  ```

## [0.25.3] — 2026-04-24

### ♻️ harness_mode 도메인 언어 캡슐화 (자가 감사 후속)

사용자 지적 "엔진 하드코딩 / 확장성 해치는 짓 없었지" 감사 결과 1 지점 개선.
`s06_context/stage.py:49` 의 `harness_mode == "autonomous"` 리터럴 비교를 헬퍼
메서드 호출로 전환. 새 모드 (예: `"safe_mode"`) 도입 시 엔진·이식에 흩어진 리터럴
비교를 추적할 필요 없이 `HarnessConfig.is_autonomous()` 한 지점만 조정.

### 변경
- `xgen_harness/core/config.py` — `HarnessConfig.is_autonomous()` / `is_selected()` /
  `is_off()` 3 헬퍼 박제. `harness_mode` 값 소문자 정규화 포함.
- `xgen_harness/stages/s06_context/stage.py` — 리터럴 비교 → `config.is_autonomous()`
  호출. `getattr(..., lambda: True)` 폴백으로 구형 config 인스턴스도 graceful.

### 자가 감사 결과
- ✅ 엔진 내 xgen / 이식측 특화 리터럴 없음 (v0.22 독립성 유지)
- ✅ ToolSource Protocol + entry_points 로 확장성 강화
- ✅ STAGE_DISPLAY_NAMES 에 s05_policy 보강 — 내장 Stage 매핑 완성 (외부 Stage 는 `display_name` property override 로 합류 가능)
- ✅ `harness_mode` 리터럴 비교 제거 (본 릴리즈)

### Breaking 없음

---

## [0.25.2] — 2026-04-24

### 🩹 s06_context RAG 폴백이 사용자 의도 무시하던 버그 수정

사용자가 `/harness` UI 에서 RAG 컬렉션 체크박스를 **비운 상태로 저장** 했음에도 응답이 특정 문서 기반으로 나가는 현상. 원인은 `s06_context/stage.py:47-48` 의 `config.rag_collections` 자동 폴백. 이식측이 HarnessConfig 에 레거시 컬렉션을 채우면 `stage_params.s06_context.rag_collections` 가 비어있어도 그 값으로 전체 검색이 돌아갔다.

### 변경
- `xgen_harness/stages/s06_context/stage.py` — `config.rag_collections` 폴백을 **`harness_mode == "autonomous"` 일 때만** 허용. `selected` / `off` 모드는 사용자가 UI 에서 명시 선택한 `stage_params` 만 존중. 사용자가 빈 컬렉션으로 저장하면 RAG 검색 자체가 skip.
- `config.harness_mode` 기본값은 `"autonomous"` — SDK 로 직접 `HarnessConfig(rag_collections=[...])` 주입하는 레거시 경로는 그대로 작동.

### 관련 UX 원칙 박제
- `selected` / `off` 모드 = "사용자 직접 지시" → stage_params 가 비어있으면 Stage 비활성 (레거시 config 폴백 금지)
- `autonomous` 모드 = "LLM 알아서" → config 폴백 + Planner 가 params override 로 채움 허용

### Breaking 없음
- 기본 모드가 `autonomous` 라 기존 SDK 사용자 영향 없음.

---

## [0.25.1] — 2026-04-24

### 🩹 s05_policy 표시 이름 누락 수정 + docstring 사용자 친화화

v0.17.0 에 Policy Gate Stage 가 도입됐지만 `STAGE_DISPLAY_NAMES` / `STAGE_DISPLAY_NAMES_KO` 딕셔너리에 등록이 빠져있어서 프론트 UI 가 원시 식별자 `s05_policy` 를 그대로 카드에 노출했음. 사용자가 "이게 뭔지 모르겠다" 피드백 후 긴급 패치.

### 변경
- `xgen_harness/core/stage.py` — `STAGE_DISPLAY_NAMES["s05_policy"] = "Policy Gate"`, `STAGE_DISPLAY_NAMES_KO["s05_policy"] = "정책 게이트"` 추가
- `xgen_harness/stages/s05_policy/stage.py` — PolicyGateStage docstring 을 사용자 친화 설명으로 재작성. `_compose_from_class_attrs` 가 이 docstring 첫 문단을 `description_ko/en` 으로 자동 노출 → UI 상세 패널에서 "이 Stage 가 무엇을 하는지" 읽을 수 있음. "Skipped (condition unmet)" 이 오류가 아닌 정상 bypass 라는 것도 본문에 명시.

### Breaking 없음
- API / 스키마 / stage_params 계약 모두 v0.25.0 그대로.

---

## [0.25.0] — 2026-04-24

### ⚠️ Breaking: ToolSource 를 **유일한 도구 공급 채널** 로 승격

v0.24 까지 s04_tool 이 도구를 긁어오는 길은 네 갈래였다 — `mcp_sessions`,
`custom_tools`, `node_tags`, `cli_skills`. 네 개 각각에 대해 stage 내부에 특수
분기가 있었고, 엔진이 xgen-mcp-station / xgen-core function_storage 를 직접
HTTP 호출하는 독립성 위반이 누적. v0.25 는 이 전체를 `ToolSource` Protocol
하나로 수렴한다.

### 변경

- `xgen_harness/tools/__init__.py`
  - `ToolSource` Protocol 에 `list_tools(filters=None)` 시그니처 추가 (기존
    구현도 backwards compat — TypeError 흡수해서 인자 없이 재호출).
  - `describe_tool_source(source) -> dict` — source_id / display_name /
    display_name_ko / description / icon / category / filter_schema 를 UI
    메타로 노출. 속성 누락 시 안전 폴백.
  - `describe_all_sources()` + `list_all_tools(filters_by_source)` — 엔진
    `/tool-sources` 엔드포인트가 쓰는 고수준 헬퍼.
  - `source_of(tool_name)` — 도구 이름 → 소스 id 역매핑 (s07_act 가 dispatch
    직전 "누가 실행할지" 확인용).
  - `use_request_headers(headers)` contextmanager + `get_request_headers()` —
    `/tool-sources` 핸들러가 요청 헤더를 contextvar 에 실어주면 downstream
    ToolSource 가 self-loopback 호출 시 Authorization / x-user-* 전파.
  - `register_tool_source()` 이 동일 `source_id` 재등록 시 기존 슬롯을 **교체**
    (hot-reload 시나리오).

- `xgen_harness/stages/s04_tool/stage.py` — 전면 재작성
  - `mcp_sessions` / `custom_tools` / `node_tags` / `cli_skills` stage_param
    읽기 경로 전부 제거.
  - `selected_tools: dict[str, list[str]]` 단일 파라미터 — 키 없음=소스 전체
    포함, 빈 리스트=소스 비활성, 이름 리스트=화이트리스트.
  - `tool_source_filters: dict[str, dict]` 단일 파라미터 — 각 소스의
    `list_tools` filter 파라미터 맵.
  - `_discover_selected_mcp_tools()` / `_collect_mcp_sessions_from_workflow()`
    삭제 (MCP 디스커버리는 호스트 측 `MCPStationToolSource` 가 담당).
  - `tool_source_of: dict[tool_name -> source_id]` 를 state.metadata 에 기록
    (s07_act dispatch 디버깅 / audit 용).
  - `sources_discover_start` / `sources_discover_complete` StageSubstepEvent
    추가.

- `xgen_harness/core/stage_config.py` — s04_tool 필드 재정의
  - `mcp_sessions` / `custom_tools` / `node_tags` / `cli_skills` 필드 삭제.
  - `selected_tools` + `tool_source_filters` object 필드 신설.

- `xgen_harness/api/router.py`
  - `GET /api/harness/tool-sources?include_tools=true&filters=<json>` 신규
    엔드포인트. 등록된 모든 `ToolSource` 메타 + 각 소스의 `list_tools()` 결과를
    통째로 반환. 요청 헤더를 `use_request_headers()` 로 전파.

- `xgen_harness/core/planner.py` — submit_plan 도구 설명 업데이트 (s04_tool
  파라미터 명칭 `selected_tools` / `tool_source_filters` 반영).

### 마이그레이션 (이식 / 외부 플러그인)

**이식**: xgen 특화 소스 3 개를 `ToolSource` 로 구현해서 등록:
  - `MCPStationToolSource` (source_id=`mcp-sessions`) — 기존 Station `/sessions` + 각 세션 `/tools` 를 `list_tools` / `call_tool` 로 감쌈. 기존 `_discover_selected_mcp_tools` 대체.
  - `CustomAPIToolSource` (source_id=`custom-api`) — xgen-core `tools` 저장소 self-loopback 조회, 요청 헤더 전파로 user 컨텍스트 유지.
  - `XgenNodeToolSource` (source_id=`xgen-nodes`) — `constants/node_registry.json` 파싱 + `ExternalNodeRef` 로 ResourceRegistry 실행 경로 재사용.

`harness_bridge/__init__.py` 가 최초 import 시점에 `register_all_tool_sources()` 호출 → 엔진 레지스트리에 자동 주입.

**외부 기여자**: 자기 wheel 의 `pyproject.toml` 에
```toml
[project.entry-points."xgen_harness.tool_sources"]
my_source = "my_pkg:MySource"
```
선언만 하면 엔진 / 이식 / 프론트 코드 수정 0 으로 s04 UI 에 "My Source" Box 가 자동 등장.

### 프론트 (xgen-frontend @xgen/api-client)

- 신규 `listHarnessToolSources(filters?)` + 타입 `HarnessToolSource` /
  `HarnessToolSourceItem` / `HarnessToolSourceFilterField` /
  `HarnessToolSourcesResponse`.
- `features/main-harness/src/components/resource-selector.tsx` 의 `ToolSelector`
  전면 재작성 — 하드코딩 4 Box (MCP / Custom / CLI / nodeTags) 구조 삭제,
  `/tool-sources` 응답 기반 동적 N Box 렌더. 각 Box 상단에 source.description
  안내 카드 + filter_schema 기반 sub-UI (`ToolSourceFilterWidget` — multi_select
  / text / toggle 세 위젯) + "전체 포함 / 비활성" 빠른 액션 + 도구 체크박스.
- MCP Market 설치 UI 는 유지 — 설치는 `mcp-sessions` 소스의 공급원 확장 경로.

### 하위호환

- Breaking. 저장된 하네스 워크플로우에 `stage_params.s04_tool.mcp_sessions` 등
  구형 키가 있으면 v0.25.0 엔진은 조용히 무시. 프론트 리로드 시 자동으로
  `selected_tools` / `tool_source_filters` 로 재선택 필요 (사용자 수동).
- 엔진 `describe_tool_source()` 는 Protocol 속성 누락 시 폴백 제공 — 구형
  ToolSource 구현 (`source_id` 없음) 도 `type(source).__name__` 이름으로 UI 에
  등장.

### 관련 메모 / 설계 문서

- `docs/worklog/2026-04-24-toolsource-unification.md` (예정)
- 메모리: `feedback_harness_scope_only.md` (레거시 workflow 인프라 무침범 원칙 준수)

---

## [0.24.5] — 2026-04-24

### 🧰 공용 tool sanitize — provider 하드코딩 분기 일반화

v0.24.3 의 Anthropic-only 화이트리스트는 "웹 UI 는 모든 LLM provider 를 다 지원하는데
왜 Anthropic 만 특수 처리하나" 라는 확장성 지적에 따라 **base 메서드로 일반화**.

### 변경

- `providers.base.LLMProvider._sanitize_tool_defs(tools)` 기본 구현 추가
  - 클래스 상수 `ALLOWED_TOOL_KEYS = {"name", "description", "input_schema", "type"}`
    (LLM 표준 공통 4 키) 기본 화이트리스트
  - 얕은 dict comprehension 으로 정제
- `AnthropicProvider` 가 `ALLOWED_TOOL_KEYS = LLMProvider.ALLOWED_TOOL_KEYS | {"cache_control"}`
  로 확장 (prompt caching 용 Anthropic 전용 키)
- `AnthropicProvider.chat` 의 인라인 화이트리스트 → `self._sanitize_tool_defs(tools)` 호출로 교체
- `OpenAIProvider` 는 `_convert_tools` 가 `name/description/parameters` 만 뽑는
  자체 변환기라 무변경. 공용 sanitize 거치지 않아도 이미 안전.
- `LangChainAdapter` 도 `_to_langchain_tools` 자체 변환으로 안전.

### 외부 provider 기여자 가이드

- pip 으로 Bedrock / Gemini / vLLM 등 커스텀 provider 를 추가하면 **base 의
  기본 sanitize 를 자동 상속**. annotations 같은 비표준 키로 400 나지 않음.
- 자기 provider 에 확장 키가 필요하면 클래스 상수로 `ALLOWED_TOOL_KEYS = LLMProvider.ALLOWED_TOOL_KEYS | {"my_ext"}` 한 줄.

### 하위호환

- v0.24.4 근본 수정 (Tool.to_api_format 에서 annotations 제거, state.tool.annotations 맵 분리) 유지
- v0.24.3 Anthropic 인라인 화이트리스트는 제거됐으나 base sanitize 가 같은 역할 수행

---

## [0.24.4] — 2026-04-24

### 🧬 annotations 를 tool payload 에서 근본 분리 (provider-agnostic)

v0.24.3 은 Anthropic 한정 화이트리스트 안전망이었음. 외부 provider (Bedrock /
Google / 커스텀 entry_points) 도 같은 문제를 겪을 수 있어 **payload 구조 자체에서**
annotations 를 뺌.

### 변경

- `Tool.to_api_format()` 반환값에서 `annotations` 키 제거 → LLM provider 표준 포맷
  (name/description/input_schema) 만 포함. **모든 provider 안전**.
- `ToolGroup` 에 `annotations: dict[str, dict[str, Any]]` 신규 필드 — tool_name 별
  MCP annotations 블록 보관 (payload 와 분리).
- `s04_tool/stage.py` 가 외부 ToolSource 의 annotations 를 `state.tool.annotations[name]`
  맵으로 분리 저장. `state.tool_definitions` 에는 들어가지 않음.
- `s07_act._resolve_read_only_hint` 3단 → 6단 확장: state.tool.annotations 1차 →
  Tool 인스턴스 속성 → legacy tool_definitions annotations → legacy metadata.is_read_only
  → legacy is_read_only → False fallback.
- `HITLGuard._resolve_annotations` 도 동일 3단 우선순위 (map → legacy definitions → instance).
- `to_index_entry()` 는 annotations 유지 — UI/Progressive Disclosure 표시용, API payload 아님.

### 안전망 유지

- v0.24.3 의 `anthropic.py` 화이트리스트 정제는 **보조 방어막** 으로 그대로 유지
  (외부 ToolSource 가 실수로 legacy 포맷 넣어도 payload 오염 차단).

### 하위호환

- 구 버전 외부 MCP 가 tool_def 에 annotations 를 넣어 보내면 s04 가 분리 저장.
- s07_act / HITL 이 legacy 경로 (tool_definitions.annotations) 도 여전히 읽음.

---

## [0.24.3] — 2026-04-24

### 🔥 Anthropic tools annotations 400 핫픽스

프로덕션 Auto 모드 s04_tool → s06_context 진행 중 Anthropic API 에서:
```
HTTP 400: tools.0.custom.annotations: Extra inputs are not permitted
```

### 원인

- v0.23.0 에서 `Tool.to_api_format()` 이 MCP 표준 `annotations` 블록을 추가
- 엔진 내부 `state.tool_definitions` 는 정상이지만 Anthropic provider 가 **그대로 API 로 전송**
- Anthropic tools 스펙은 `{name, description, input_schema, type, cache_control}` 만 허용 → `annotations` unknown field 400

### 수정

- `providers/anthropic.py` 가 tools 전송 직전 **화이트리스트 정제**. `_ANTHROPIC_TOOL_KEYS = {"name", "description", "input_schema", "type", "cache_control"}` 만 통과
- 엔진 내부 annotations 유지 (s07_act 의 readOnlyHint 우선순위 조회 경로는 그대로)
- OpenAI provider 는 `_convert_tools` 가 name/description/parameters 만 뽑아서 무영향

### 검증

- Anthropic API tool 정의 스펙 일치
- 내부 `Tool.annotations()` 호출 경로 (s07_act destructive 판별 등) 무변경

---

## [0.24.2] — 2026-04-24

### 🔥 Auto 모드 s04_tool AttributeError 핫픽스

프로덕션 Auto 모드 실행에서 `'list' object has no attribute 'items'` 로 s04_tool 직후 중단.

### 원인

- `ProgressiveDiscovery.discover` 반환형은 `tuple[list[dict], list[dict]]` — `tool_index` 는 **list**
- `s04_tool/stage.py:153` 은 초기 dict 구조 시절 잔재로 `tool_index = {k: v for k, v in tool_index.items() ...}` dict comprehension 호출
- 평소엔 `selected_builtins` 기본값 `["discover_tools"]` 덕분에 L151 분기 (`if "discover_tools" not in selected_builtins`) 가 False 라 숨어있던 버그
- Auto 모드에서 Planner LLM 이 `builtin_tools=[]` 또는 discover_tools 뺀 list 로 제안 → 분기 True → `list.items()` 호출 → AttributeError

### 수정

- `s04_tool/stage.py:153` dict comprehension → list comprehension (`[ti for ti in tool_index if ti.get("name") != "discover_tools"]`)
- 다른 5 소비처 (s03_prompt / s07_act / state.py 등) 는 전부 이미 list 로 일관 처리 중 — 본 수정은 s04_tool 1곳만 영향

### 검증

- `grep -rn tool_index.items() xgen_harness/` → 결과 0 (수정 후)
- `grep -rn state.tool_index xgen_harness/` → 전부 list 순회/append

---

## [0.24.1] — 2026-04-24

### 🔧 v0.24.0 후속 — `__version__` drift 핫픽스

PyPI 0.24.0 publish 검증 중 `xgen_harness.__version__ == "0.22.1"` 발견.
pyproject.toml 은 0.24.0 이지만 `xgen_harness/__init__.py` 의 하드코딩
문자열이 동기화되지 않아 런타임이 틀린 버전을 보고.

### 수정

- `xgen_harness/__init__.py` : `__version__` 를 하드코딩 문자열 → 런타임
  `importlib.metadata.version("xgen-harness")` 조회로 전환. 설치된 wheel
  의 METADATA 에서 정확한 값을 읽으므로 pyproject.toml 만 bump 하면
  자동 반영. PackageNotFoundError 시 `"0.0.0+uninstalled"` 폴백 (소스
  로만 실행하는 dev/CI 환경 호환).

### 검증

- PyPI 0.24.1 publish 후 `xgen_harness.__version__ == "0.24.1"` 확인.
- 기존 22 pytest 회귀 없음.

## [0.24.0] — 2026-04-24

### 🛡 HITL Guard + 🗜 Agent-controlled Compact Tool + approval.required SSE

v0.23.0 의 Tool annotations (destructive/open_world/...) 를 **실제 안전 게이트**
와 **컨텍스트 절감 게이트** 로 확장. production 에이전트 표준 패턴
(Claude Code / Cursor / Aider 류) 과 동일 수준.

### 추가

- **HITLGuard** (`stages/strategies/guard.py`)
  - Human-In-The-Loop — destructive/open_world 도구 호출 전 사용자 승인 대기
  - `hook_points = {PRE_TOOL}` — 도구별 개별 승인
  - trigger 파라미터: `trigger_destructive` (기본 True) / `trigger_open_world` /
    `trigger_non_readonly`
  - `timeout_sec=300` 기본, 0 이면 무한 대기
  - `auto_approve_for_dev=True` 로 개발 환경 우회
  - 거부 시 가짜 `tool_result(is_error=True)` 를 LLM 에 전달 → 재계획 루프
  - 승인자가 `edited_input` 제공하면 args 자동 교체 (오입력 교정)
  - `xgen_harness.guards` entry_points 에 "hitl" 이름으로 등록

- **Guard.check_async + GuardChain.invoke_async** (`stages/strategies/guard.py`)
  - 비동기 경로 신설. 기존 sync Guard 는 `check_async` 기본 구현이
    `check()` 래핑이라 **완전 backward-compat**.
  - HITLGuard 만 `check_async` override 해서 `state.request_approval` 을 await.
  - `s05_policy/stage.py` PRE_TOOL 경로가 `invoke_async` 로 전환.

- **PipelineState.request_approval / resolve_approval / pending_approval_ids**
  (`core/state.py`)
  - 내부 `_approval_futures` 딕셔너리. `request_approval` 이
    `ApprovalRequiredEvent` 방출 + Future 생성 + await.
  - 이식측이 `resolve_approval(id, decision, reason, edited_input)` 호출
    → Future 풀려 실행 재개.
  - `state.emit_event()` — verbose 여부 무관 필수 이벤트 발행 경로 신설.

- **ApprovalRequiredEvent / ApprovalDecidedEvent** (`events/types.py`)
  - SSE 이벤트 2종. `event_to_dict` 에 type_map 등록.
  - 최상위 `xgen_harness` 에서 export.
  - 프론트는 기존 EventLog 구독으로 approval_required 수신 → 모달 렌더.

- **CompactTool** (`tools/builtin.py`)
  - LLM 이 자율 호출하는 컨텍스트 압축 도구. 자동 50KB threshold 가 못 잡는
    **누적 부풀이** 해소.
  - scope: `tool_results_before:N` / `history_before:N` / `pd_store:<kind>`
  - `summary_hint` 파라미터로 "뭘 남길지" LLM 이 선언
  - `destructive_hint=True` — HITLGuard 가 자동 트리거해 사용자 확인 가능
  - summarizer 미주입 시 길이 기반 truncate 폴백 (의미론 손실 but 동작 유지)

- **tests/test_hitl_and_compact.py** (11 케이스)
  - approval 이벤트 직렬화
  - request_approval + resolve_approval 라운드트립
  - approval_timeout
  - HITL 비-destructive 스킵 / 승인·거부·auto-approve
  - GuardChain.invoke_async 혼합 (sync+async Guard)
  - CompactTool history_before / tool_results_before / annotations

### 변경

- s05_policy 의 PRE_TOOL 경로가 sync `invoke` → async `invoke_async` 로 전환.
  기존 5 내장 Guard 는 check_async 기본 구현 타고 그대로 동작.

### 검증

- pytest 22/22 PASS (import_smoke 5 + plan_fingerprint 6 + hitl_and_compact 11)
- 기존 v0.23 fingerprint baseline 그대로 통과 — Planner 행동 변화 0

### 이식측 요구

- pin 상향 필요: `xgen-harness>=0.23,<0.24` → `>=0.24,<0.25`
- `POST /api/agentflow/harness/approvals/{approval_id}` 엔드포인트 추가
  (body: `{decision, reason?, edited_input?}`)
- SSE 중계 시 `approval_required` 이벤트 타입 프론트 전달

## [0.23.0] — 2026-04-24

### 🧰 MCP Tool annotations 1급화 + 행동 지문 회귀 테스트

도구 분류가 이름 휴리스틱에 의존하던 취약점 해소 + 회귀 방어선 구축.

### 추가

- **Tool ABC 에 힌트 4종 1급 속성** (`tools/base.py`)
  - `read_only_hint` — 외부 상태 미변경 → s07_act 가 asyncio.gather 병렬 실행
  - `destructive_hint` — 되돌릴 수 없음 → HITL / Policy Gate 트리거용
  - `idempotent_hint` — 같은 입력 → 같은 결과 (재시도 안전)
  - `open_world_hint` — 외부 시스템 영향 (네트워크·파일시스템)
  - `annotations()` 메서드로 MCP 표준 블록 캡슐화
  - `to_api_format()` / `to_index_entry()` 모두 annotations 포함

- **빌트인 도구 힌트 정확화** (`tools/builtin.py`)
  discover_tools / search_tools / fetch_pd — 전부 readOnly + idempotent + !openWorld
  (프로세스 내부 state 만 읽음)

- **MCP 서버 annotations 우선 수용** (`tools/mcp_client.py`)
  MCP 서버가 2025-06-18+ 표준 annotations 를 보내면 그대로 사용. 없을 때만
  legacy 이름 휴리스틱 폴백 (deprecated 경로, 다음 메이저에서 제거).

- **GalleryTool annotations 파싱** (`tools/gallery.py`)
  `tool_def["annotations"]` 우선 + legacy `is_read_only` 폴백. 4 필드 전부 캐싱.

- **RAG Tool 힌트 선언** (`tools/rag_tool.py`)
  readOnly + idempotent + openWorld.

- **s04_tool 이 ToolSource annotations 전파** (`stages/s04_tool/stage.py`)
  `list_tools()` 응답의 annotations 를 `state.tool_definitions` 에 그대로 실음.

- **tests/test_plan_fingerprint.py** — Planner 정규화 행동 지문 5 케이스
  `HarnessPlanner._build_plan_from_tool_input` (순수 함수) 의 결정적 출력을
  `tests/fingerprints/plan_*.json` 에 baseline 저장 → 이후 실행마다 비교.
  단위 테스트로는 못 잡는 "에이전트 행동 변화" 를 지문으로 감지.
  시나리오: simple_qa / rag_with_judge / filters_unknown_stage /
  injects_required / malformed_types_defended.

### 변경

- **s07_act 이름 휴리스틱 완전 폐기** (`stages/s07_act/stage.py`)
  이전: `{"create","update","delete",...}` 키워드 매칭 → "search_and_create_plan"
  같은 이름이 write 로 오분류 → 병렬 실행 손실.
  현재: `_resolve_read_only_hint()` 헬퍼가 5 단 우선순위로 결정.
    1) tool_definitions[*].annotations.readOnlyHint (MCP 표준)
    2) Tool 인스턴스.read_only_hint
    3) legacy tool_definitions metadata.is_read_only
    4) legacy instance.is_read_only
    5) False (안전 쪽 — 명시 선언 없으면 순차 실행)

- **`Tool.is_read_only` 는 별칭으로 유지**
  v0.24 에서 제거 예정. 외부 구현체 backward-compat.

### 검증

- pytest 11/11 PASS (import_smoke 5 + plan_fingerprint 6)
- fingerprint baseline 5 개 커밋됨 — 향후 정규화 변화 즉시 감지
- backward-compat 확인: 기존 `is_read_only` 쓰는 외부 Tool 구현체 영향 없음

### 관찰

- `plan_injects_required.json` baseline 이 REQUIRED_STAGES (s01_input /
  s09_decide / s11_finalize) 자동 주입이 Planner 에서 일어나지 **않음**을
  기록. 문서와 실제 코드 갭 드러남 — 향후 `_build_plan_from_tool_input`
  수정 시 리뷰 대상.

## [0.22.1] — 2026-04-24

### 🔧 v0.22.0 후속 — 재검수 결과 2건 해소

엄밀 재검수에서 잠재 사일런트 버그 + 죽은 property 검출. 기능 변경 없고 견고성만 향상.

### 수정

- **`register_orchestrator` 케이스 민감 → 정규화**
  외부 기여자가 `register_orchestrator("MyPattern", ...)` 대소문자 섞어 등록하면,
  `pipeline.py:148` 의 `orch_hint.strip().lower()` 조회와 불일치해서 영원히 miss →
  iterative fallback 되는 사일런트 버그. register/get/unregister 모두 `name.strip().lower()`
  정규화로 통일. 기존 기본 5개(모두 소문자 등록)는 무영향.
- **`state.is_over_iterations` property 제거**
  v0.22.0 에서 pipeline 이 `loop_iteration < effective_max_iter` 직접 계산으로 전환해
  이 property 의 호출 횟수 0. 삭제.

### 검증 실측

- `get_orchestrator("MyCustom")` / `get_orchestrator("mycustom")` / `get_orchestrator("  MYCUSTOM  ")` 모두 동일 spec 반환
- `grep -rn is_over_iterations` 결과 0 (주석 제외)
- 기존 기본 5 orchestrator(linear/iterative/react/plan_execute/dag) 행동 동일

---

## [0.22.0] — 2026-04-24

### 🧹 엔진 독립성 + 레지스트리 완성

내부 코드 감사 결과 치명 등급 지적 3건 정리. 엔진은 이제 xgen 을 전혀 모른다.

### Breaking (이식측 행동 필요)

- **`xgen_harness.integrations/` 디렉터리 삭제**. xgen 특화 코드(XgenAdapter / XgenServiceProvider / xgen NodeAdapter / SSE 변환 / workflow_bridge) 는 호스트(xgen-workflow) 측 `harness_bridge/` 로 이전. 엔진이 `editor.node_composer` 같은 호스트 모듈을 보던 잔재 제거.
- **`xgen_harness.adapters.xgen.XgenAdapter` 삭제**. 같은 경로로 이전.
- 호스트는 `adapters.resource_registry.ExternalNodeRef` Protocol 을 만족하는 dataclass(node_id / category / spec_id / params 4 필드) 를 tool_executor 로 등록하면 엔진이 자동 감지 (duck-typing).

### 추가

- `adapters.resource_registry.ExternalNodeRef` — runtime_checkable Protocol. `_XgenNodeRef` isinstance 체크가 이 Protocol 로 일반화.
- `core.config.mark_stage_required(stage_id)` / `unmark_stage_required(...)` / `get_required_stages()`. `REQUIRED_STAGES` 는 live set 으로 외부 등록 즉시 반영.
- `OrchestratorSpec.replan_per_iter` / `max_iterations_override`. pipeline 이 이름 if-else 대신 spec 을 조회하여 행동 분기. 외부 orchestrator 도 선언만 하면 엔진이 동일하게 존중.
- `providers.get_default_model(provider)` — env override (`XGEN_HARNESS_{PROVIDER}_DEFAULT_MODEL`) 우선. 새 모델 출시 시 코드 수정 없이 런타임 반영.
- `core.pipeline.ROLE_ORCHESTRATOR_PLANNER / ROLE_POLICY_GATE / ROLE_MAIN_ACTOR / ROLE_SCORER` 상수 박제. 리터럴 중복 제거.

### 변경

- `core.pipeline.py` 의 `"linear"/"plan_execute"` 이름 분기 → `get_orchestrator(name).replan_per_iter / max_iterations_override` 조회.
- 전 코드에서 `PROVIDER_DEFAULT_MODEL.get(...)` 직접 호출 → `get_default_model(...)` 로 교체 (builder/session/provider_bootstrap/multi_agent/api.router).
- `pyproject.toml` `package-data` 의 `integrations/*.json` 항목 제거.

### 제거되지 않은 것 (v0.23+ 예정)

- `core.stage_config` 의 UI 리터럴(`icon`/`fields.label` 등). 프론트가 이 필드를 렌더에 사용하는지 전수 확인 후 별도 PR 에서 이전 예정.

### 감사 후속 — 이전 감사의 오진 수정

- `events.types` 의 `MissingParamEvent / StageSubstepEvent / RetryEvent / PlanningEvent` 는 **죽은 코드 아님**. `state.emit_verbose(...)` wrapper 경유 emit 총 14 건 확인. 삭제 계획 철회.
- `core.planner.py:419` 의 `chosen.sort(...)` 는 실행 순서가 아닌 표시용 정렬 (_planner_skips 는 set membership). 자율주행 훼손 아님. 유지.

---

## [0.21.0] — 2026-04-24

### 🌀 NOM IR 허브 — Phase C 완결

v0.16.0 에서 선언된 NOM (Node Object Model) 의 주석 약속 — "`to_mcp() / to_wheel() / to_sandbox_payload()` 한 곳에서 확정" — 이 실체화됨. Stage / Strategy / Tool / MCP server / 외부 플러그인 노드가 **하나의 IR** 을 통해 wheel / MCP / sandbox 3 경로로 분기.

### 추가
- `NOMGraph.to_mcp_schema(include_kinds=..., name_strategy=...)` — NOM → MCP `tools/list` 응답 스키마 (Claude Desktop/Cursor 호환)
- `NOMGraph.to_sandbox_payload(node_id, input)` — NOM → `Sandbox.run_nom_tool` payload (격리 실행 브리지)
- `NOMGraph.to_wheel_snapshot(gallery_name, ...)` — NOM → `WorkflowSnapshot` (기존 `build_wheel` 경로 재사용, 비파괴)
- `xgen_harness.compile.compile_nom_graph(graph, ...)` — NOM 전용 one-shot 진입점 (`compile_workflow` 와 병렬)
- `xgen_harness/compile/nom_compile.py` 신설
- `tools/synthesis.py` `synthesized_tools_as_nom_graph(tools)` — 여러 Tool Synthesis 결과 → NOMGraph → 한 번에 wheel
- top-level export: `NOMKind / NOMParam / NOMOutput / NOMNode / NOMGraph / snapshot_current_registry_as_nom / compile_nom_graph`

### 설계
- 기존 `compile_workflow()` / `WorkflowSnapshot` 은 **그대로 유지** (워크플로우 중심 경로). NOM 은 추가 진입점 (노드 그래프 중심).
- 두 경로는 `WorkflowSnapshot` + `build_wheel()` 에서 수렴 — 중복 빌드 로직 없음.
- 외부 기여자는 `NOMGraph` 만 만들면 wheel/MCP/sandbox 파이프라인 자동 사용.

### 실측 스모크
- 수동 NOM (2 노드 TOOL) → to_mcp_schema → [search, fetch] 정확, required 필드 표시
- to_sandbox_payload(node_id) → entry/input/metadata 정상, 예외 경로 (KeyError / ValueError) 확인
- to_wheel_snapshot → workflow_type="nom", from_nom=True 플래그 확인
- compile_nom_graph → `xgen_gallery_nom_ping-0.1.0-py3-none-any.whl` 6.1KB 생성
- synthesized_tools_as_nom_graph([t1, t2]) → NOMGraph → compile_nom_graph → wheel. **Tool Synthesis → 배포** 파이프라인 최초 E2E.

## [0.20.0] — 2026-04-24

### 🛡️ Sandbox Verifier — MCP stdio 서버 publish-time 게이트

v0.18 Phase A 에서 "하네스 워크플로우 → wheel → MCP stdio 서버" 자동 말아올리기가 완성됐지만, 외부 호스트(xgen-mcp-station) 에 등록되기 **전** 에 건전성·스키마·리소스를 검증할 관문이 없었다. v0.20.0 은 이 문지기(Phase B Gate) 를 엔진 기본 기능으로 제공한다.

### 추가
- `xgen_harness/core/sandbox_verifiers.py` 신설
  - `SandboxVerifier` Protocol — Registry + `entry_points("xgen_harness.sandbox_verifiers")` 로 확장
  - `MCPStdioVerifier` (기본) — JSON-RPC over stdio 로 `initialize` → `notifications/initialized` → `tools/list` 왕복, 스키마 유효성 검증, 정규화된 tools 배열의 SHA-256 해시 반환 (재현성 지표)
  - POSIX rlimit (`SandboxLimits`) + timeout + stderr tail cap — `core/sandbox.py` 와 동일 정책으로 통일
  - 편의 함수 `verify_mcp_stdio(command, ...)`
- `VerifyResult` dataclass — `ok` / `tools` / `tool_count` / `handshake_ms` / `tools_ms` / `payload_hash` / `stderr_tail` / `error` / `timed_out` / `applied_limits`
- `__init__.py` 에서 전부 top-level export (Sandbox 관련도 함께)

### 설계 원칙 (유지)
- 엔진은 generic primitive — 호출자가 `command: list[str]` 을 준비, verifier 는 격리 subprocess 기동만 담당. xgen-mcp-station / Claude API 등 특정 서비스를 모른다.
- if/elif 분기 없음 — 새 verifier (mcp-http, docker-wrapped, wasm) 는 `register_sandbox_verifier()` 한 줄 또는 entry_points 로 추가.

### 실측 스모크 (로컬)
- `MCPStdioVerifier.verify(command=[python, "-u", "-m", "xgen_gallery_verif_smoke.cli", "serve-mcp"])` 
- 결과: `ok=True`, `tool_count=1`, `tools=['run_workflow']`, handshake 370ms, tools/list 1ms, stderr empty, rlimit `{cpu_seconds=30, address_space_mb=2048, max_open_files=256}` 적용 확인

### 이식측 wiring
- 이식측 `xgen-workflow/controller/workflow/endpoints/harness_publish.py` 의 `MCPStationPublisher.publish()` 가 Station 등록 **전** 에 `MCPStdioVerifier` 로 검증 → 통과 시 `payload_hash` 를 session 메타에 첨부. `HARNESS_SANDBOX_POLICY` env(`strict`/`advisory`/`off`)로 정책 조절. *(이식측 커밋은 PyPI 배포 후 별도 진행 예정)*

## [0.16.6] — 2026-04-22

### 🎯 Pipeline Role 체계 — Stage 이름 리터럴 12 → 0

사용자 감사에서 지적된 잔재: `pipeline.py` 가 `s00_harness`/`s07_act`/`s08_judge` 를 이름으로 직접 알고 있었음 (12 hit). "s07_act → s07_execute 리네임 시 Pipeline 도 고쳐야" 하는 **확장성 위반**.

### 수정
- `Stage.role` 속성 도입 (`core/stage.py`). 기본 빈 문자열
- 3 개 Role 정의:
  - **`orchestrator_planner`** — ingress 최상단 prepend + bypass 금지 + Phase B replan 대상 (s00_harness)
  - **`main_actor`** — Planner 의 main_call 을 이 Stage **직전** 에 주입 (s07_act)
  - **`scorer`** — `StageExitEvent.score` 에 `validation_score` 노출 (s08_judge)
- `pipeline.py` 모든 특수 분기를 role 기반 검색으로 전환
  - `reg.get("s00_harness", ...)` → `_find_role_in_registry(reg, cfg, "orchestrator_planner")`
  - `if stage.stage_id == "s07_act"` → `if stage.role == "main_actor"`
  - `if stage.stage_id == "s08_judge"` → `if stage.role == "scorer"`
  - `_planner_skips` / `_find_loop_s00` / `_invoke_main_call` 전부 role 기반
- `planner.py` 의 `stage_id="s00_harness"` 리터럴도 role 조회로 제거

### 자가검증 (grep)
- `pipeline.py` / `planner.py` 실 로직 Stage 이름 리터럴: **12 → 0** ✅
- 3 Role 선언 확인: `orchestrator_planner` 1, `main_actor` 1, `scorer` 1 ✅
- `_find_role_in_registry(reg, cfg, "orchestrator_planner")` = `s00_harness` 인스턴스 반환 ✅
- `_find_loop_s00()` = role 기반으로 `s00_harness` 인스턴스 반환 ✅

### bench 동반 (외부 어댑터)
- `bench/prod_schema.py` 신설 — 운영 `functionId` 상수 (`api_loader` / `mcp` / `tools` 등) 단일 파일로 분리
- `bench/collect_prod_catalog.py` 가 `classify` / `is_extractable` / `extractor_for` 헬퍼만 import
- 운영 스키마 변경 시 `bench/prod_schema.py` 한 곳만 수정

### 영향
- 외부 기여자가 `role="orchestrator_planner"` 선언한 자기 Planner Stage 로 바꿔 끼우면 Pipeline 수정 0
- `main_actor` / `scorer` 도 같은 방식으로 교체 가능
- v0.16.5 에서 발견된 hot-fix (tool_result content 정규화) 와 독립적 개선

## [0.16.5] — 2026-04-22 (hot-fix)

### 🔥 Tool result content string 정규화 — Anthropic 400 + slice TypeError 동시 수정

v0.16.4 실측에서 5 도구 실호출 시도 시 두 에러 발생:
1. `Tool 'cj_tool' failed: slice(None, 500, None)` — `result_text[:500]` 이 dict 에 대해 불가
2. `HTTP 400: tool_result.content must be string or content block list` — Anthropic API 가 dict 거부

원인:
- `SynthesizedToolSource.call_tool` 가 dict payload 를 그대로 content 로 반환
- `s07_act._dispatch_tool` 의 `result.get("content", str(result))` 가 dict 면 dict 그대로 리턴

수정 (두 지점 동시):
- `tools/synthesis.py::SynthesizedToolSource.call_tool` — content 가 str 이 아니면 JSON 직렬화
- `stages/s07_act/stage.py::_dispatch_tool` ToolSource 경로 — 안전 가드. 둘 다 막히는 이중 방어

## [0.16.4] — 2026-04-22

### 🎯 s04_tool 브릿지 — 전역 tool_sources 가 LLM tool_definitions 로 자동 전파

v0.16.3 에서 `get_tool_sources()` 로 등록된 도구가 **s07_act 실행 경로엔 합류** 하지만 **LLM prompt 의 `tools` 배열에 안 실리는** 문제 발견. `s04_tool` Stage 가 MCP 세션 / custom_tools / RAG 경로만 `state.tool_definitions` 로 변환하고 있었음.

- `s04_tool/stage.py` 에 "1.7 전역 tool_sources 편입" 블록 추가
- 모든 `ToolSource.list_tools()` 결과를 `state.tool_definitions` 에 자동 주입 (중복 이름 skip)
- 하드코딩 0, 레지스트리 기반. `register_tool_source` / `XGEN_HARNESS_PRELOAD_MANIFEST` 양쪽 경로 모두 반영

### 검증 시나리오
- 운영 xgen.x2bee.com 전수 스캔 매니페스트 (21 NOMNode: api_loader 5 / mcp 7 / builtin 9) 를 env 로 주입
- 하네스 Auto 가 홈쇼핑 쿼리 실행 → LLM 에게 도구 스키마 전달 → 실 tool_call 발생 → 5 API 호출 결과 종합

## [0.16.3] — 2026-04-22

### 🎯 Tool Synthesis 번들 자동 주입 훅

**확장성·연동성 — 하드코딩 금지 준수**: `XGEN_HARNESS_PRELOAD_MANIFEST` 환경 변수만 지정하면 프로세스 시작 시 LocalManifest 파일을 읽어 SynthesizedTool 을 자동 등록.

- `tools/__init__.py` 에 `ENV_PRELOAD_MANIFEST = "XGEN_HARNESS_PRELOAD_MANIFEST"` 단일 상수
- `get_tool_sources()` 첫 조회 시 idempotent 로드
- 다중 파일은 OS path separator (`os.pathsep`) 로 구분
- `compile.local_manifest` 스키마 재사용 — synthesis 가 만든 파일을 운영·이식 프로세스에 **그대로** 주입
- 활용: 홈쇼핑 5 개 HTTP API (cj/gs/hs/lotte/naver keywords) 를 SynthesizedTool 로 wrap → manifest 저장 → env 주입 → 하네스 프로세스가 자동 인식

### 자가검증
- `XGEN_HARNESS_PRELOAD_MANIFEST=/path/to.json` 설정 후 `get_tool_sources()` → 5 도구 자동 등록 PASS
- 프로세스 간 tool 전파 — synthesis 프로세스가 만든 manifest 를 다른 프로세스(하네스 서버) 가 재사용 PASS

## [0.16.2] — 2026-04-22 (hot-fix)

### 🔥 streaming transport UnboundLocalError 수정

실 LLM 스모크 테스트에서 발견된 치명 버그:
```
cannot access local variable 'call_count' where it is not associated with a value
```

- `core/llm_call.py:74` — 초기화되지 않은 `call_count += 1` 제거
- `state.llm_call_count` 가 실제 누적 카운터, 반환값의 `call_count=1` 은 단일 호출 고정
- 영향: Auto 모드에서 응답은 나왔지만 마무리 단계에서 에러 이벤트 2 건 발생 (사용자 경험 저하)
- 실측 검증: `한국의 수도?` 스모크 PASS (Planner 3 Stage 선택 + 답변 "서울")

## [0.16.1] — 2026-04-22

### 🎯 하드코딩 제거 감사 + Sandbox 리소스 하드닝 + 통합 매니페스트

사용자 지시 박제: **"확장성 연동성 하드코딩식 해결 금지"** (`feedback_no_hardcoding_extensibility`).
이 릴리즈는 v0.16.0 에서 발견한 박제 5 지점 (synthesis prefix/tags/manifest 스키마) 제거 + xgen-sandbox 아이디어 차용한 rlimit 기반 격리 + 대규모 벤치 자동화.

**🟢 1. Sandbox 하드닝 (`core/sandbox.py`)**:
- `SandboxLimits` dataclass 신설 — `cpu_seconds / address_space_mb / max_open_files / max_file_size_mb / no_core_dump` 5 필드
- POSIX `preexec_fn` 에서 `resource.setrlimit` 강제 — child 전용, 부모 영향 0
- 실증: 300MB 할당 → MemoryError 차단, 무한 루프 → SIGKILL(-9) 2초 만료
- xgen-sandbox 아이디어 차용: per-call 리소스 필드 통합 스펙. 코드 카피 없음

**🟢 2. LocalManifest 통합 스키마 (`compile/local_manifest.py` 신설)**:
- 과거 `tools/synthesis.py` 가 `{"version":"0.1","nodes":[]}` 스키마를 즉석 박제, `core/node_plugin.py` 는 `NodePluginManifest` 로 따로 → drift 위험 발생
- `LocalManifest` 단일 dataclass + `SCHEMA_NAME="xgen_harness.local_manifest"` / `SCHEMA_VERSION=1` 단일 상수
- `load_manifest / save_manifest / upsert_node_in_file` 통합 API
- synthesis 와 node_plugin 모두 이 모듈 호출만 — 같은 JSON 파일을 양쪽에서 주고받기 가능. drift 원천 차단

**🟢 3. Tool Synthesis 하드코딩 제거 (`tools/synthesis.py`)**:
- prefix/tags/package/entry 5 군데 박제 → 모듈 상수 (`_NAMESPACE / _ENTRY_PREFIX / _TAGS / _PLUGIN_PACKAGE / _SOURCE_LABEL`) 로 추출
- `set_synthesis_namespace() / set_synthesis_tags() / get_synthesis_config()` 런타임 교체 API
- `to_nom_node()` 가 통일 NOMNode 인스턴스 반환 (과거 dict → 타입 안전성)
- `upload_synthesized_to_gallery()` 는 `compile.local_manifest.upsert_node_in_file` 호출만. 자기 스키마 박제 0
- `load_synthesized_from_gallery()` 도 동일 모듈 경유
- 실증: 동일 파일을 synthesis 가 쓰고 NodePlugin 이 읽어 NOMNode 복원 PASS

**🟢 4. NodePlugin ↔ LocalManifest 호환 (`core/node_plugin.py`)**:
- `load_manifest_file()` 이 `.json` 파일의 `schema=xgen_harness.local_manifest` 를 우선 감지 → LocalManifest 경로로 로드
- 아니면 기존 YAML/JSON → NodePluginManifest fallback. 완전 호환

**🟢 5. n=30 벤치마크 자동화 (`bench/run_harness_vs_workflow_n30.py`)**:
- 3 카테고리 × 10 샘플 = 30 케이스 (`bench/cases/n30_cases.jsonl` 외부 파일)
- 지표 4 종: 정답률 / 평균 지연(ms) / 평균 토큰 / 평균 Stage·도구 수
- 결정적 모의 executor — 실 LLM 배선 전 **구조/집계 파이프라인** 검증 (다음 릴리즈에서 `--real-llm` 배선)
- 리포트 표준 포맷 `bench/reports/YYYY-MM-DD-harness-vs-workflow-n30.md` 확정
- 초회 결과 (stub): simple 에서 토큰 -20%, Stage 수 -50%, 복잡 케이스는 자율 도구 호출 2 회

### 자가검증 (feedback 체크리스트 5 항목)
1. **레지스트리 기반?** LocalManifest = 통합 모듈 ✅
2. **entry_points 자동 발견?** 기존 8 축 + tool_sources 유지 ✅
3. **단일 파일 기본값?** synthesis 상수 1 파일 / local_manifest 상수 1 파일 ✅
4. **catalog 노출?** stages/orchestrators/providers/phases 변화 없음 (유지) ✅
5. **스키마 중복 없음?** synthesis/node_plugin/gallery 3 곳이 LocalManifest 단일 포맷 공유 ✅ (이 릴리즈 핵심)

### 문서
- `docs/harness/NODE_PLUGIN_SPEC.md` — local_manifest 스키마 편입 (다음 패치)
- `bench/reports/2026-04-22-harness-vs-workflow-n30.md` — 자동 생성 초회

## [0.16.0] — 2026-04-22

### 🚀 비전 6축 Phase 2~5 전면 실증 + 자가증식 도구 루프

사용자 지시: **"쭉 ㄱㄱㄱㄱㄱ 쉬지말고 모든 페이즈 ㄱㄱㄱㄱㄱㄱㄱ 다 해"**. Phase 2~5 를 코드·테스트·리포트까지 완료.

**🟢 1. Phase 2 — Stage Gallery 분리 (`xgen-harness-stage-sample`)**:
- 샘플 패키지 stage_id 현행화 (`s04_tool_index` → `s04_tool`) + 버전 0.2.0
- 의존성 pin `>=0.15.0`
- 실증: `ArtifactRegistry.default()` 에 외부 Stage 가 `(stage_id=s04_tool, artifact=lotte)` 로 자동 합류 PASS
- "Stage 디렉토리 하나만 빼서 pip 패키지로 배포 → UI 에서 default↔artifact 1 클릭 swap" 최초 완성

**🟢 2. Phase 3 — Sandbox + NOM IR (`core/sandbox.py` · `core/nom.py`)**:
- `Sandbox`: subprocess 기반 격리 실행기. timeout / stdout cap / `-I` isolated mode / stdin JSON / return_value 자동 파싱. 정상·타임아웃·예외 3 케이스 전부 확인
- `NOMNode` / `NOMGraph`: Stage / Strategy / Tool / MCP server / legacy Node 를 단일 IR 로 통일. id / kind / source_file / entry / inputs / outputs / tags / version / plugin_package 9 필드
- `snapshot_current_registry_as_nom()` — 현 엔진 상태 (Stage 12 + Strategy + Orchestrator + Provider) 를 54 노드 NOMGraph 로 한 번에 덤프. 갤러리·샌드박스·컴파일러 공통 입력

**🟢 3. Phase 4 — Node Plugin 매니페스트 (`core/node_plugin.py` · `docs/harness/NODE_PLUGIN_SPEC.md`)**:
- `NodePluginManifest` / `register_node_plugin` / `load_manifest_file` 공개 API
- entry_points 그룹 `xgen_harness.node_plugins` 자동 발견 (idempotent)
- 매니페스트 3 경로: Python dict / YAML·JSON 파일 / entry_points
- xgen-workflow 레거시 노드(Input String / LLM / Retriever / ...) 를 외부 pip 패키지로 떼어낼 규약 확정. 엔진 무침범

**🟢 4. Phase 5 — Tool Synthesis Loop (`tools/synthesis.py`)**:
- `SynthesizedTool` / `ToolTestCase` / `SynthesizedToolSource` 데이터 모델
- `test_synthesized_tool()` — Sandbox 로 test_cases 전수 검증. 하나라도 실패하면 등록 차단
- `synthesize_and_register()` — 검증 통과 시 자동 `register_tool_source` → 카탈로그 자동 합류 → 다음 Plan 이 재사용
- **실증**: "slugify" 가상 LLM 생성 도구가 2 test case 통과 → 레지스트리 합류 → 후속 호출 `{"slug":"harness-auto-weave"}` 반환 PASS
- 비전 5번 축 "자가 증식 도구 에이전트" 첫 실전

**🟢 5. Phase 6 — 벤치마크 자동화 (`bench/run_extension_phase_audit.py`)**:
- Phase 2~5 실증을 단일 스크립트로 PASS/FAIL 표 + 증거 JSON 출력
- 리포트: `bench/reports/2026-04-22-extension-phase-audit.md`
- 결과: **4/4 PASS**

### 자가검증 grep (전 축 총점검)
- 9 축 × 5 항목 모두 유지 (v0.15.3 기준)
- 외부 Stage artifact 합류, 외부 NodePlugin 매니페스트 합류, LLM 생성 도구 합류 전부 entry_points / register API 한 줄로 완료
- 하드코딩 이름 리터럴: 각 레지스트리 기본값 1 파일에만 (planner/catalog/pipeline 본체 = 0)

### 문서
- `docs/harness/NODE_PLUGIN_SPEC.md` — 매니페스트 규약 신설
- `docs/worklog/2026-04-22-v0150-extension-audit.md` — 오늘 전체 흐름 박제
- `bench/reports/2026-04-22-extension-phase-audit.md` — 자동 생성 리포트

### 남은 Phase (Phase 3.5 / 5.1+)
- Sandbox 리소스 한도 (rlimit / cgroups) 강제
- Tool Synthesis 의 갤러리 업로드 연동
- Node Plugin 매니페스트 → catalog 최상위 `nodes` 키 승격

## [0.15.3] — 2026-04-22

### 🎯 Phase 2 마무리 — orchestrator_hint 실 분기 + Stage-local Strategy 자동 스캔

사용자 지시: **"다 해 임마"** — 남은 Phase 2 전부 밀어붙이기.

**🟢 1. Pipeline Phase B 에 orchestrator_hint 실 분기 (`core/pipeline.py`)**:
- `linear`: 첫 iter 1회 실행 후 `loop_decision="complete"` 강제 → 단발 Q&A 에 LLM 이 자율 선택
- `iterative`: 기본. 매 iter replan 유지
- `plan_execute`: iter 가 돌아도 replan 생략 — 첫 Plan 고수
- `react` / `dag`: 엔진 no-op (이식측 dispatcher 에 위임)
- 로그에 `orchestrator_hint=<value>` 표기로 디버깅 근거 노출

**🟢 2. Stage-local Strategy 자동 스캔 (`core/fs_scanner.py` 확장)**:
- `scan_stage_strategies()` 신설. 2 convention 지원:
  - `stages/sNN_xxx/strategies/<slot>__<impl>.py` (평면 밑줄 2 개 구분)
  - `stages/sNN_xxx/strategies/<slot>/<impl>.py` (슬롯 서브디렉토리)
- Strategy 서브클래스 export 시 `register_strategy(stage_id, slot, impl, cls)` 자동 호출.
- `strategy_resolver._ensure_defaults_registered()` 가 3 경로 idempotent 합산 (내장 기본 + fs_scan + entry_points)
- 외부 기여자는 Stage 디렉토리 안에 파일 드롭만으로 Strategy 확장. 엔진 코드 수정 0.

### 자가검증 (통합 테스트)
1. Pipeline 소스에 `orchestrator_hint` / `orch_hint` / `linear` / `plan_execute` 문자열 전부 박제 PASS
2. `scan_stage_strategies()` 동적 테스트 — 임시 파일 드롭 후 `_REGISTRY` 에 `(s05_strategy, dummyslot, dummyimpl)` 키 즉시 생성 PASS
3. 테스트 완료 후 cleanup — 실 레포에는 영향 없음

### 프론트 동반 변경 (xgen-frontend feature/harness-v2)
- `StageInfo.source_file?` + `StageStrategy.source_file?` / `slot?` 타입 추가
- stage-list AUTO 블록 헤더에 소스 파일 약식 경로(마지막 2 세그먼트) 표시 + 풀 경로 툴팁

## [0.15.2] — 2026-04-22

### 🎯 파일 구조 자동 연동 — fs_scanner + Tool entry_points + catalog source_file

사용자 지시: **"모든것이 다 엔트리포인트를 알아서 읽고 가져오게 해야 돼. 파일구조나 그런 것도 애가 참고해서 이해됨?"**. 기본 Stage 등록까지 파일시스템 스캔으로 전환 + Tool entry_points 완성 + catalog 에 실제 소스 파일 경로 노출.

**🟢 1. Filesystem Scanner 신설 (`core/fs_scanner.py`)**:
- `scan_default_stages(registry)` — `xgen_harness/stages/` 아래 `sNN_xxx/` 패턴 디렉토리 전수 훑어 Stage 서브클래스 자동 import + register.
- `scan_stage_artifacts(registry)` — `stages/sNN_xxx/artifacts/<name>.py` 가 있으면 대안 artifact 로 자동 등록 (swap-in 변형 디렉토리 convention).
- `get_stage_source_file(cls)` — 클래스의 소스 파일 경로(`xgen_harness/...` 이하 상대)를 반환.
- 외부 기여자는 `stages/s04_tool_lotte/` 디렉토리만 만들고 Stage 서브클래스 export → 엔진 코드 수정 0.

**🟢 2. registry.py 리터럴 Import 제거**:
- 과거 `from ..stages.s01_input import InputStage` 식 12 건 수동 import + register 제거.
- `_register_default_stages()` 가 `fs_scanner.scan_default_stages(registry)` 한 줄 위임.
- cross-directory artifact 1 건만 유지 (`orchestrator/multi_agent_planner.py` 의 `s05_strategy/multi_agent`) — stages/ 바깥이라 스캔 대상 외.

**🟢 3. Tool entry_points 완성 (`tools/__init__.py`)**:
- `_discover_from_entry_points_once()` 추가. 그룹 `xgen_harness.tool_sources`.
- `get_tool_sources()` 첫 호출 시 idempotent 스캔.
- entry_point 반환값 3 형태 수용: `ToolSource` 인스턴스 / factory callable / iterable.
- pip install xgen-tools-xxx 한 패키지가 자동 합류.

**🟢 4. catalog `source_file` 노출 — "LLM 이 파일 구조 보고 판단" (`core/registry.py` · `core/catalog.py`)**:
- `stages[].source_file` = `xgen_harness/stages/sNN_xxx/stage.py`
- `stages[].strategies[].source_file` + `stages[].strategies[].slot` = StrategyResolver `_REGISTRY` 직접 조회로 실제 구현 파일 노출.
- 예시: s00_harness 의 transport 슬롯은 `xgen_harness/stages/strategies/transport.py` 가 streaming/batch 2개 impl 보유 ← LLM 이 이걸 보고 "이 슬롯은 streaming/batch 공용 파일에 정의되어 있고, 새 impl 을 얹으려면 거기에 클래스 추가" 판단 가능.

### 자동 연동 커버리지 (v0.15.2 기준 9 축 완료)

| 축 | 외부 entry_points | 내부 파일 스캔 | catalog source_file |
|---|---|---|---|
| Stage | ✅ xgen_harness.stages | ✅ fs_scanner | ✅ |
| Strategy | ✅ xgen_harness.strategies | ⚠ (Phase 2 convention) | ✅ |
| Capability | ✅ xgen_harness.capabilities (v0.15.1) | N/A | — |
| Tool | ✅ **xgen_harness.tool_sources (v0.15.2)** | N/A | — |
| Orchestrator | ✅ xgen_harness.orchestrators (v0.15.0) | N/A | — |
| Provider | ✅ xgen_harness.providers (v0.15.1) | N/A | — |
| Phase | ✅ xgen_harness.phases (v0.15.1) | N/A | — |
| NodeAdapter | ✅ xgen_harness.node_adapters | N/A | — |
| Transport | ✅ via strategies | options_source 동적 | ✅ |

### 자가검증 grep (8 / 8 PASS)

1. `fs_scanner.scan_default_stages()` 이 12 Stage (s00~s11) 모두 자동 발견 ✅
2. `catalog.stages[].source_file` 전수 존재 ✅
3. `catalog.stages[].strategies[].slot` + `.source_file` 노출 (s00_harness transport 슬롯 실측) ✅
4. `tools.get_tool_sources()` 첫 호출 시 entry_points 스캔 idempotent ✅
5. `tools.register_tool_source()` 직접 등록도 정상 합류 ✅
6. `registry.py` 안 Stage 이름 리터럴 = 1 건 (multi_agent artifact 한 줄) ✅
7. `fs_scanner.py` 안 Stage 이름 리터럴 = 0 ✅
8. 외부 `stages/s04_tool_lotte/` 디렉토리만 드롭해도 `_STAGE_DIR_RE` 매칭 → 자동 합류 ✅

### 다음 턴 (Phase 2 남은 부분)

- Strategy 디렉토리 convention (`stages/sNN/strategies/<slot>/<impl>.py`) + 파일 스캔 자동 등록
- 프론트 PlanningCard / StageList 가 `source_file` 읽어 "이 Stage 는 어디에 있는지" 힌트 표시
- Pipeline Phase B 의 `orchestrator_hint` 실 분기
- 프론트 서브에이전트 산출물 리뷰 + push (AUTO 배지 포함)

## [0.15.1] — 2026-04-22

### 🎯 모든 확장 지점 자동 연동성 감사 — 3개 갭 제거

사용자 지시: **"모든 측면에서 그런 자동연동성을 확인해야 해"**. v0.15.0 의 Orchestrator 전환 모델을 **9 축 전수 감사** 하고 발견된 3개 하드코딩/누락을 즉시 수정.

**🔴 감사 결과 9 / 9** (파일:라인 기반 실측):

| 축 | 판정 |
|---|---|
| Stage / Strategy / Orchestrator / NodeAdapter | ✅ OK (리터럴 0, register_* + entry_points + catalog 3종 완비) |
| Phase | ❌ GAP → **이번 수정** |
| Transport (stage_config options) | ❌ GAP → **이번 수정** |
| Capability / Provider entry_points | ❌ GAP → **이번 수정** |
| Tool entry_points | ⚠ 부분 GAP (다음 릴리즈) |

**🟢 1. Phase 하드코딩 제거 (`core/phase_registry.py` 신설)**:
- 과거 `core/stage.py` 의 `if self.order <= 4: return "ingress"` 매직 넘버 박제 제거.
- `register_phase(name, upper_order, description)` 공개 API + `_REGISTRY` dict + `entry_points("xgen_harness.phases")` 자동 발견.
- `Stage.phase` property 가 `resolve_phase(self.order)` 한 줄로 위임. Stage 서브클래스가 override 하면 그 값 우선.
- 기본 3개 (ingress ≤4 / loop ≤9 / egress ≤9999) idempotent 등록. 외부에서 `register_phase("post_egress", upper_order=9999)` 한 줄로 확장.

**🟢 2. Transport options 동적화 (`core/stage_config.py`)**:
- s00_harness.fields.strategy 의 `"options": ["streaming", "batch"]` 리터럴 제거.
- `"options_source": "s00_harness_transport_strategies"` 로 전환 — UI 드롭다운이 StrategyResolver `_REGISTRY` 에서 실측한 list_strategies 결과를 렌더. 외부 Transport 플러그인이 `register_strategy("s00_harness","transport","websocket",...)` 한 줄로 즉시 UI 합류.
- `stage_config` 와 `StrategyResolver` 간 이중 선언이 제거되어 drift 불가.

**🟢 3. Capability entry_points 자동 발견 (`capabilities/registry.py`)**:
- `_discover_from_entry_points_once()` 추가 — 그룹 `xgen_harness.capabilities` 스캔, CapabilitySpec / Iterable[CapabilitySpec] 모두 수용.
- `get_default_registry()` 최초 호출 시 자동 실행 + idempotent. 반복 호출 부작용 0.
- pip install xgen-capability-xxx 한 것이 레지스트리에 즉시 합류 → Planner catalog 에도 자동.

**🟢 4. Provider entry_points 자동 발견 (`providers/__init__.py`)**:
- `_discover_from_entry_points_once()` + `_register_from_entry_point()` 헬퍼 추가.
- 그룹 `xgen_harness.providers`. entry_point 반환값 3 형태 지원: LLMProvider 서브클래스 / dict(name, cls, default_model, models, api_key_env, context_limit) / list[dict].
- `_register_defaults()` / `list_providers()` / `get_default_provider()` 진입 시 idempotent 스캔.
- 외부 `xgen-bedrock-provider` 패키지가 setup.cfg 에 `[project.entry-points."xgen_harness.providers"]` 선언만 해도 엔진 무수정.

**🟢 5. catalog 최상위 축 추가 (`core/catalog.py`)**:
- `catalog["providers"]` — 레지스트리 전수 + context_limit + default_model. Planner / 프론트가 한 번에 수집.
- `catalog["phases"]` — PhaseRegistry 전수. 외부 phase 도 자동.
- `catalog["orchestrators"]` 는 v0.15.0 때 이미 추가. 이로써 자율주행 필요한 4 축(stages/orchestrators/providers/phases) 이 단일 카탈로그에서 조회 가능.

### 자가검증 grep (8 / 8 PASS)
1. `register_phase("post_egress", upper_order=9999)` → `list_phases()` / `catalog.phases` 즉시 합류 ✅
2. Stage.phase property 가 레지스트리 해석 (ingress/loop/egress 실Stage 에 정상 반영) ✅
3. `stage_config.s00_harness.fields.strategy.options` 키 **부재** (options_source 만 존재) ✅
4. `HarnessStage.list_strategies()` 가 StrategyResolver 에서 동적으로 streaming/batch 반환 ✅
5. `get_default_registry()` 호출 후 `_ENTRY_POINTS_DISCOVERED=True` idempotent ✅
6. `list_providers()` 호출 시 entry_points 스캔 실행 (기본 5개 + 외부 0 일 때 동일 결과) ✅
7. `catalog` 최상위 키 `{stages, required_stages, orchestrators, providers, phases, ...}` ✅
8. `core/stage.py` 의 phase 리터럴 1건 (Type hint 주석 1줄만, 로직 0) ✅

### 남은 Phase 2
- Tool entry_points 자동 발견 (`tools/__init__.py`) — 현재 `tools/gallery.py` 에는 있지만 핵심에 승격 필요
- 프론트 `PlanningCard` 에 max_iterations/orchestrator_hint 배지 + `catalog.providers/phases` 활용
- Pipeline Phase B 의 `orchestrator_hint` 실제 분기 실행 (react/plan_execute)

## [0.15.0] — 2026-04-22

### 🎯 재귀적 자율주행 완성 — LLM 이 반복 수·오케스트레이터까지 자율 결정 + display_name=Auto

사용자 확정 기조: **"LLM 은 골조만 파악. 파일 디렉토리 바라보고 Stage 선택 → 선택된 Stage 안의 구조 뒤져 도구·설정 자율 → 오케스트레이터·반복 횟수까지 자율 주행. 하드코딩 절대 안 된다. 자동 연동 자동 확장성이 중요"**.

**🔴 1. HarnessPlan 스키마 확장 (`core/planner.py`)**:
- `HarnessPlan.max_iterations: Optional[int]` — LLM 이 이번 요청 적정 반복 수 (1~50) 를 직접 판단. 과거 `HarnessConfig.max_iterations = 10` 상수를 Plan 이 override.
- `HarnessPlan.orchestrator_hint: str` — 실행 패턴 힌트 (`linear / iterative / react / plan_execute / dag` + 외부 플러그인 등록 이름). 이식측 dispatcher 가 해석.
- `PLAN_TOOL_INPUT_SCHEMA` 에 두 필드 + 설명 / 범위(1~50) / enum 동적 주입 추가.
- `_build_plan_from_tool_input` 이 범위 밖 값(999 / garbage) 자동 거부.

**🟢 2. OrchestratorRegistry — 자동 연동 자동 확장성 (`core/orchestrator_registry.py` 신설)**:
- `register_orchestrator(name, description=..., dispatch_key=...)` 공개 API.
- 엔진 기본 5개 (`linear / iterative / react / plan_execute / dag`) 는 `_ensure_defaults_registered` 가 idempotent 등록. 외부에서 `unregister_orchestrator` 로 덜어낼 수 있음.
- `entry_points` 그룹 `xgen_harness.orchestrators` 자동 발견 — pip install 로 새 패턴이 즉시 합류.
- `build_plan_tool()` 이 매 호출마다 `list_orchestrators()` 로 enum 동적 주입 + `get_orchestrator_specs()` 설명을 enum description 에 합성. LLM 이 "무슨 의미인지" 바로 이해.
- `_build_plan_from_tool_input` 검증도 레지스트리 기반 — 리터럴 목록 0.
- `planner.py` / `catalog.py` 안에 orchestrator 이름 리터럴 0 개 (자가검증 grep 통과). 기본 5개 정의는 `orchestrator_registry.py` 한 곳에만.

**🟢 3. s00_harness Plan 병합 확장 (`stages/s00_harness/stage.py`)**:
- `_merge_plan_into_config` 가 `plan.max_iterations > 0` 일 때 `state.config.max_iterations` 즉시 override + 로그.
- `orchestrator_hint` 는 `state.metadata["orchestrator_hint"]` 기록 — 이식측 dispatcher / 프론트 PlanningCard 가 해석.
- `PlanningEvent` 방출 시 두 필드 추가 전달.

**🟢 4. display_name = "Auto" 통일 (`core/stage.py` + 프론트 stage-list)**:
- `STAGE_DISPLAY_NAMES["s00_harness"] = "Auto"` / `_KO` 동일. 내부 ID `s00_harness` 유지 → 엔진/SSE/이식 계약 무손상.
- catalog `_collect_stages` 에 `display_name` 필드 노출.
- 프론트 `stage-list.tsx` 배지 "HARNESS" → "AUTO".

**🟢 5. catalog 심화 (`core/catalog.py`)**:
- `catalog["orchestrators"]` 최상위 노출 — 레지스트리 전수, 프론트/이식이 "어떤 패턴들이 가능한지" 한 번에 읽음.
- `stages[].tool_slots` 키 노출 — Stage 저자가 선언한 도구 슬롯 설명을 LLM 에게 전달 (Phase 2 에서 s04_tool 등에 채움).
- `stages[].strategies` 는 이미 `{name, description, is_default}` 심화 — Planner 가 "어떤 impl 들이 있고 각각 뭘 하는지" 보고 고른다.

**🟢 6. PlanningEvent 확장 (`events/types.py`)**:
- `max_iterations: int` + `orchestrator_hint: str` 필드 추가 → SSE 로 프론트 전달 → PlanningCard 배지 렌더용.

### 자가검증 grep (8 / 8 PASS)

1. `HarnessPlan.max_iterations` / `.orchestrator_hint` 존재 ✅
2. Schema `max_iterations` 범위 1~50 ✅
3. PlanningEvent 확장 필드 수용 ✅
4. SYSTEM_PROMPT 228 chars ≤ 300 ✅
5. Plan 파싱 — 정상값 복원 + 잘못된 값 거부 ✅
6. s00 display_name = "Auto" (en+ko) ✅
7. 플러그인 `register_orchestrator` 등록 → 즉시 enum 합류 + Plan 파싱 수용 ✅
8. `planner.py` / `catalog.py` 안 orchestrator 이름 리터럴 0 개 ✅

### 설계 문서
- `docs/harness/2026-04-22-autonomous-driving.md` — 4 Layer 재설계 (Directory-as-Catalog / Deep PD / Plan 스키마 확장 / Plan 우선 규칙) + 자가검증 축 박제.

### 다음 턴 (Phase 2)
- 이식측 `endpoints/harness.py` 가 `state.metadata["orchestrator_hint"]` 로 dispatcher 전환.
- 프론트 `PlanningCard` 에 `max_iterations` / `orchestrator_hint` 배지 + `catalog.orchestrators` 설명 툴팁.
- `core/registry.py` 서브디렉토리 스캔 (`stages/*/strategies/*.py` 자동 등록).
- `core/pipeline.py` Phase B 에 `orchestrator_hint` 분기 (react/plan_execute 본격 구현).

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
