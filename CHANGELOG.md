# Changelog

All notable changes to `xgen-harness` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
