# Changelog

All notable changes to `xgen-harness` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
