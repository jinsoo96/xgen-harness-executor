# XGEN Harness Execution — 설계 문서 V2

> v1(`EXECUTION_DESIGN.md`)은 v0.8.13 릴리스 시점 기준. **V2 는 v0.8.24 기준** — 7 연속 릴리스(v0.8.17→v0.8.24), 이식 측 `feature/harness-v2` 브랜치, 프론트 UI 실구동 검증, 하드코딩 재감축 결과를 모두 반영.
> 짝꿍: `REFACTORING_PLAN_V2.md` (리팩토링 실적/잔여)
> 원복: `xgen-easy-dev/docker/rollback-v1.sh` 한 줄로 30초~2분.

---

## 0. 한 줄 정의 (변경 없음)

> **xgen-harness execution**은 *"에이전트가 무엇을 할지를 노드로 그리는 것"*이 아니라, *"에이전트가 어떤 환경에서 어떤 자원을 들고 어떻게 점진적으로 정보를 탐색해 답을 만들지"*를 **12개의 정형화된 스테이지**로 표현하는 실행 엔진이다.

V2 에서 이 원칙은 그대로. 달라진 건 **레지스트리가 단일 진실 소스로 강제**되고 **verbose 이벤트 4종으로 블랙박스가 완전히 해소** 된 것.

---

## 1. V1 대비 구조적 변화 (한눈에)

| 영역 | V1 (v0.8.13) | V2 (v0.8.24) |
|---|---|---|
| Public Stage 계약 | `input_spec/output_spec` 있으나 private | `StageInput/StageOutput/STAGE_IO_SPECS/get_stage_io` public export |
| Verbose 이벤트 | 없음 | 4 타입 (`ServiceLookupEvent`, `CapabilityBindEvent`, `StageSubstepEvent`, `RetryEvent`) + SSE 변환 + 실발행 경로 |
| ConfigService | `get_value/get_api_key` | `+ get_setting(key, default)` Protocol (Redis→env→default 강제) |
| Provider 레지스트리 | `PROVIDER_DEFAULT_MODEL` (provider 당 1개) | `+ PROVIDER_MODELS` (provider 당 여러 모델) + `get_provider_models()` 헬퍼 |
| Strategy 확장 | 수동 `register_strategy` | `+ entry_points(group="xgen_harness.strategies")` 자동 발견 |
| Stage 교체 | `register_stage()` 있음 | `+ describe_all().current_artifact` 노출 + 중복 등록 경고 |
| model 하드코딩 | 9곳 중복 | **2곳** (의도된 provider 생성자 기본값만) |
| Redis 우선 조회 | `adapters/xgen.py:155` 에서 .env 우선 (버그) | **완전히 수정** + SSE `service_lookup` 으로 런타임 증명 |
| OpenAI base_url | full endpoint URL 만 수용 | **base URL 만 와도 endpoint 자동 조립** |
| 이식 측 브랜치 | `feature/harness-executor` 단일 | `feature/harness-executor` (v1 안정) + `feature/harness-v2` (실험) |
| 원복 | 수동 | `rollback-v1.sh` 한 줄 |

---

## 2. 핵심 추상화 (유지)

| 추상화 | 책임 | V2 변경 |
|---|---|---|
| Stage | state 를 변환하는 단위 | + `contract`(StageInput/Output) public, `state.emit_verbose()` 헬퍼 |
| Strategy | Stage 내부 알고리즘 교체 | + entry_points 자동 발견 |
| Artifact | 스테이지 간 데이터 규격 | + `STAGE_IO_SPECS` public export |
| Capability | "할 줄 아는 일" 선언 | 변화 없음 (3 경로 바인딩 그대로) |
| ServiceProvider | 외부 자원 주입 | + `get_setting` 표준, XgenConfigService 에 EventEmitter 주입 |

---

## 3. 12 스테이지 (위치·책임 유지)

| # | Stage | 책임 | V2 의 새 기능 |
|---|---|---|---|
| 1 | s01_input | 입력 정규화 · 메시지 조립 | `_resolve_base_url`, model 미지정 시 `PROVIDER_DEFAULT_MODEL` 조회 |
| 2 | s02_memory | 이전 대화 · 메모리 | - |
| 3 | s03_system_prompt | 시스템 프롬프트 + RAG 합성 | - |
| 4 | s04_tool_index | 도구·MCP·RAG·Capability 바인딩 | `CapabilityBindEvent(source="declaration")` 발행 |
| 5 | s05_plan | 계획 수립 + capability 자동 발견 | `CapabilityBindEvent(source="discovery")` 발행 |
| 6 | s06_context | RAG 검색, 컨텍스트 압축 | `services.database.get_schema_summary` 위임 (DB 자동 인식) |
| 7 | s07_llm | Provider 호출 | `StageSubstepEvent(llm_request_start / llm_response_complete)` |
| 8 | s08_execute | Tool call 실행 | - |
| 9 | s09_validate | 출력 검증 | - |
| 10 | s10_decide | 다음 행동 결정 | `loop retry` 시 `RetryEvent` 발행 |
| 11 | s11_save | 결과 영속화 | `services.database.insert_record` 위임 |
| 12 | s12_complete | 메트릭 + Done | - |

---

## 4. Verbose 이벤트 시스템 (V2 신규)

```
HarnessConfig(verbose_events=True) → PipelineState.emit_verbose(event)
    ├── ServiceLookupEvent   : Redis vs env 조회 경로
    ├── CapabilityBindEvent  : declaration / discovery / auto_publish
    ├── StageSubstepEvent    : Stage 내부 주요 단계
    └── RetryEvent           : Stage on_error 복구 / 에이전틱 루프 retry
```

### 발행 경로

| 타입 | 발행 지점 |
|---|---|
| ServiceLookupEvent | `XgenConfigService.get_api_key/get_setting` (hit/miss + source) |
| CapabilityBindEvent | s04(선언) · s05(자연어 발견) · XgenAdapter(auto_publish 요약) |
| StageSubstepEvent | s07 request/response 샘플 (확장 지점) |
| RetryEvent | `Pipeline._execute_stage on_error 복구` + `loop_decision=retry` |

### SSE 포맷 (이식 측 `harness.py`)

```python
if evt_type in ("log", "tool"):
    yield f"event: {evt_type}\ndata: {json.dumps(inner)}\n\n"
else:
    yield f"data: {json.dumps(event)}\n\n"
```

→ 프론트 `_executeSSE` 가 `event: log` 헤더로 분기 → `mapLogToPipelineEvent` 가 `event_kind=verbose.*` 로 매핑 → EventLog 에 단색 기호(⌕ ⧉ ∙ ↻)로 렌더.

### 실런타임 증명 (v0.8.24)

```
service_lookup × 4
  OPENAI_API_KEY            ← redis [hit]
  OPENAI_API_BASE_URL       ← redis [hit]
  OPENAI_TEMPERATURE_DEFAULT ← redis [hit]
  OPENAI_MAX_TOKENS_DEFAULT ← redis [hit]
stage_substep × 2
  s07_llm / llm_request_start
  s07_llm / llm_response_complete
```

---

## 5. 단일 진실 소스 (하드코딩 감축)

### Provider/Model
```python
# xgen_harness/providers/__init__.py
PROVIDER_DEFAULT_MODEL = {"anthropic": "...", "openai": "...", "google": "..."}
PROVIDER_MODELS        = {"anthropic": [...], "openai": [...], "google": [...]}

def get_provider_models(provider) -> list[str]:
    # 기본 모델 + 추가 목록, 중복 제거
    ...
```

### 소비자
- `stage_config.py._inject_dynamic_options` → s01_input UI options 자동 주입
- 이식 측 `harness.py:_list_providers` → `/api/agentflow/harness/options/providers` 응답
- 프론트 `StageDetailPanel` → dropdown 렌더
- 프론트 `useHarnessStore.setHarnessConfigField('provider')` → 자동 모델 전환도 레지스트리 경유

→ **새 provider 를 라이브러리에 register 하면 프론트 UI 까지 자동 반영**. 이식 측 · 프론트 수정 불필요.

---

## 6. 이식 흐름 (V2 확정판)

```
브라우저 /harness
    ↓
Next.js proxy → xgen-workflow:8000
    ↓
controller/workflow/endpoints/harness.py
    ├── /harness/presets                → list_presets()
    ├── /harness/stages                 → ArtifactRegistry.describe_all()
    ├── /harness/options/providers      → get_provider_models() × 5
    ├── /harness/options/capabilities   → CapabilityRegistry.list_all()
    ├── /harness/workflows              → DB workflow_meta
    ├── /harness/config/{id} GET/PUT    → harness_config 부분 CRUD
    └── /harness/execute/stream
            ↓
         XgenAdapter.execute
            ↓ (verbose_events=true 면 services.config 에 emitter 주입)
            ↓ HarnessConfig 생성 (Redis 우선 기본값 polling)
            ↓ Pipeline.from_config + Pipeline.run(state)
              Phase A  s01 → s02 → s03 → s04 (capability_bind 발행)
              Phase B  s05 (discovery 발행) → s06 → s07 (substep 발행)
                    → s08 → s09 → s10 (retry 발행 시점)
              Phase C  s11 → s12 (metrics / done)
            ↓ EventEmitter.stream()
            ↓ convert_to_xgen_event() → xgen SSE 포맷
         yield `event: log\ndata: {...}` / `data: {...}`
    ↓
프론트 _executeSSE → mapLogToPipelineEvent → EventLog 실시간 렌더
    (verbose.* 이벤트는 필터 탭 `Verbose` 에서만 표시)
```

---

## 7. 원복 전략

- 라이브러리 v2 변경은 이미 main 에 merge 됨 (v0.8.24) → 원복 대상이 아님
- 이식 측만 `feature/harness-executor` ↔ `feature/harness-v2` 로 분리 운영
- 한 줄 전환: `bash xgen-easy-dev/docker/rollback-v1.sh [--forward]`
- 전환 메커니즘: `workflow/.env` 의 `GIT_BRANCH` 변경 + `docker compose up -d xgen-workflow`
- 전환 시 DB / Redis / 영속 데이터는 모두 호환

---

## 8. UI/UX (실구동 검증 완료)

### 화면 구조
- 사이드바 (Workflows / Agentflows / Conversations)
- 툴바 (Preset · Save · Deploy · Config · 실행)
- Pipeline Canvas (12 stage 원형 배지 + Phase A/B/C + 드래그 가능)
- Stage Detail Panel (stage 클릭 시 열림 — 동적 필드)
- Config Panel (Max Tokens · MCP · RAG · Max Iterations · **Verbose Events 토글** · Capabilities)
- Event Log (`All / Stages / Tools / Verbose / Errors` 필터 + `Show LLM`)

### 상태 배지 색상
- `running` = 파란색 pulse (`#2563eb`)
- `done`    = 녹색 (`#16a34a`)
- `error`   = 빨간색 (`#dc2626`)
- `bypass`  = 회색 투명

### 레거시 일관성
- EventLog 기호 13종 모두 **단색 유니코드** (컬러 이모지 0)
- CSS 변수 `--h-bg / --h-fg / --h-accent` 방식 — `canvas-execution`, `canvas-core` 와 동일

### Playwright 실검증 결과
- 로그인(admin@test.com/1234) → /main → /harness?wf_id=...
- SVG circles 30개 (12 stage + Phase + 기타)
- 12 stage 한글 라벨 전부 노출
- 실행 버튼 → SSE 42 블록 수신 → EventLog 라이브 업데이트
- Verbose 필터 탭 클릭 → verbose 이벤트 4건만 필터링
- 콘솔 에러 0건

---

## 9. 효과 측정

### 사용자 측면
| 항목 | 캔버스 | 하네스 V2 |
|---|---|---|
| 간단 Agent 생성 | 노드 4개 + 엣지 + 파라미터 편집 | provider/model 드롭다운 2번 + 실행 |
| RAG 통합 | DocumentLoader + VectorDB + Agent + 연결 | `rag` preset 선택 + RAG Collections 체크박스 |
| 디버깅 | 노드별 로그 추적 | Stage 기반 EventLog + Verbose 필터 |
| 새 provider 추가 | 노드 타입 추가 + UI 컴포넌트 | 라이브러리 레지스트리 1줄 추가 (UI 자동) |

### 운영자 측면
- 이벤트 표준화 (stage_enter/exit + metrics + verbose 4종)
- 에러 발생 위치 = stage_id + event_kind
- Redis 변경 즉시 반영 (+ `service_lookup` 이벤트로 검증 가능)
- 버전 롤백 = `rollback-v1.sh`

---

## 10. 결론 — V1 에 이은 V2 의 세 가지 진전

1. **단일 진실 소스 강화**: provider/model/URL/이벤트 타입 모두 레지스트리 기반. 프론트·이식 측·라이브러리 각 레이어는 호출/표시만
2. **Verbose 가시화**: Redis 경로·Capability 경로·Stage 내부·재시도 모두 SSE 이벤트로 외부에 노출. 블랙박스 해소
3. **운영 안전성**: 이식 측 브랜치 분리 + 한 줄 원복. 실험판을 부담 없이 돌림

> **결론**: 하네스는 이제 "Stage 몇 개를 어떻게 묶을지" 가 아니라 "어떤 환경·도구·capability 를 어떤 레지스트리에서 선택할지" 로 의사결정이 이동했다. 코드는 같은 방향으로 수렴 — 레지스트리 확장만으로 플랫폼 전체가 확장.

---

## 참고 문서

- v1 원본: [`EXECUTION_DESIGN.md`](./EXECUTION_DESIGN.md)
- 짝꿍: [`REFACTORING_PLAN_V2.md`](./REFACTORING_PLAN_V2.md)
- 이식 테스트 가이드: [`../V2_TESTING.md`](../V2_TESTING.md)
- 원복 스크립트: `xgen-easy-dev/docker/rollback-v1.sh`
- CHANGELOG: [`../CHANGELOG.md`](../CHANGELOG.md) (v0.8.17 ~ v0.8.24 섹션)
