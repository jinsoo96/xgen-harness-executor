# Changelog

All notable changes to `xgen-harness` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
