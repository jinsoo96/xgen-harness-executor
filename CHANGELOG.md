# Changelog

All notable changes to `xgen-harness` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
