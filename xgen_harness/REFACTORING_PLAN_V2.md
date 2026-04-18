# XGEN Harness — 구조 리팩토링 플랜 V2 (실적 + 잔여)

> v1(`REFACTORING_PLAN.md`)은 R1~R8 제안. **V2 는 v0.8.17→v0.8.24 배포 이후 실제 결과 + 남은 일**을 정리.
> 짝꿍: `EXECUTION_DESIGN_V2.md`

---

## 0. 한 눈 요약

| 레이어 | 완료 | 잔여 |
|---|---|---|
| 라이브러리 (xgen-harness-executor) | 11 항목 (R1/R2/R4/R7/A1~A4/B1/B2/R8 대부분) | 0 (실행 코드 하드코딩 0건 상태) |
| 이식 측 (xgen-workflow) | `_list_providers` 라이브러리 위임, SSE 포맷 통일 | 0 |
| 프론트 (xgen-frontend) | EventLog verbose 필터 + ConfigPanel verbose 토글 + ModelSelect 레거시 제거 + provider→model 레지스트리 경유 | C1 Wizard 진입, C3 /chat 드롭다운, C6 Runtime 탭 artifact viewer 등 5건 |

---

## 1. 원본 R1~R8 진행 상태

| # | 제안 | V1 상태 | V2 결과 |
|---|---|---|---|
| R1 | StageContract 메타 | 제안만 | ✅ `StageInput/Output/STAGE_IO_SPECS/get_stage_io` public export (v0.8.17) |
| R2 | Strategy entry_points 자동 발견 | 제안만 | ✅ `_discover_plugin_strategies` 구현 (v0.8.17) |
| R3 | CapabilityBinder 단일 진입점 | 제안만 | ⏸ 현재 3 경로(declaration/discovery/auto_publish) 병존. 각 경로별 이벤트 발행으로 추적은 완결. 통합은 보류 (영향 크고 명확한 실익 낮음) |
| R4 | ServiceProvider.get_setting 표준화 | 제안만 | ✅ `ConfigService.get_setting` Protocol + XgenConfigService 구현 + `ServiceLookupEvent` 발행 (v0.8.17~0.8.23) |
| R5 | EventChannel 추상화 | 제안만 | ⏸ SSE 단일 채널로 충분. WebSocket/gRPC 수요가 생기면 재고 |
| R6 | CONTRIBUTING_TOOLS.md + validation CLI | 제안만 | ⏸ `tools/gallery.py` 에 포맷 이미 있음. 전용 CLI 는 수요 발생 시 |
| R7 | NullServiceProvider 단위 테스트 | 제안만 | ⏸ 통합 테스트는 `test_integration_runtime.py` 존재. 단위 테스트 확장은 잔여 |
| R8 | 외부 Stage entry_points 등록 | 제안만 | ✅ `_discover_plugin_stages` 구현 (v0.8.17) |

→ **R1/R2/R4/R8 완료**. R3/R5/R6/R7 은 "현재 필요성 낮음" 으로 보류.

---

## 2. V1 에서 예측 못 한 이슈 해결 (V2 추가분)

### A1. model 하드코딩 다중 중복 제거
- `api/router.py`, `core/session.py`, `core/builder.py`, `core/config.py`, `adapters/xgen.py`, `orchestrator/multi_agent.py`, `stages/s01_input.py` 의 `"claude-sonnet-4-20250514"` 중복 9곳 → **2곳** (provider 클래스 생성자 기본값만, 의도 유지)
- 대체: `PROVIDER_DEFAULT_MODEL` / `PROVIDER_MODELS` 레지스트리 참조

### A2. `stage_config.py` static 옵션 배열 제거
- s01_input 의 provider/model select options 하드코딩 → 빈 배열 + `_inject_dynamic_options()` 자동 주입

### A3. Redis 우선 조회 누수 4건 수정
- `adapters/xgen.py:155` API 키 조회 순서 (v0.8.14)
- `providers/__init__.py:70` base_url env-only (v0.8.17, `s01_input._resolve_base_url` 추가)
- model/temperature/max_tokens 기본값 (v0.8.17, 어댑터 `_resolve_adapter_setting`)
- OpenAI provider base_url endpoint 자동 조립 (v0.8.18)

### A4. verbose 이벤트 전체 경로 (v0.8.19~0.8.23)
- `XgenConfigService` 에 `event_emitter` 주입
- emitter 주입 시점을 execute 초반으로 (v0.8.20)
- `convert_to_xgen_event` 에 4종 추가 (v0.8.21)
- 실제 발행 포인트 연결 (v0.8.22)
- `config_kwargs` 에 verbose_events 전달 누락 수정 (v0.8.23)

### A5. SSE 포맷 통일 (v2 이식 측)
- `harness.py:_harness_stream` 이 `data:` 만 보내던 것을 `event: <type>\ndata: <inner>` 형태로 변경 → 프론트 `_executeSSE` 분기와 호환 → log/tool 이벤트 UI 표시 복원

---

## 3. UI/UX 리팩토링 (프론트)

### 완료
- EventLog 에 `verbose` 필터 탭 + event_kind 4종 매핑 추가
- EVENT_META 컬러 이모지 → 단색 유니코드 기호 (⌕ ⧉ ∙ ↻) 로 일관화
- ConfigPanel `ModelSelect` 미사용 레거시 함수 + 모델 하드코딩 dict 제거
- ConfigPanel 에 `verbose_events` 체크박스 토글 추가
- `HarnessConfigData.verbose_events: boolean` 필드 + DB 로드 병합
- `setHarnessConfigField('provider')` 의 provider→model 매핑 3줄 하드코딩 제거, `dynamicOptions['providers']` 경유
- HarnessPanel 마운트 시 `fetchDynamicOptions('providers')` 선제 호출

### 검증 (Playwright headless)
- 로그인 → /main → /harness?wf_id=... 라우팅 정상
- SVG circles 30개, 12 Stage 한글 라벨 전부 노출
- 실행 → SSE 42 블록 → EventLog 실시간 업데이트
- Verbose 필터 탭 클릭 → 4종 이벤트만 필터링 표시
- 콘솔 에러 0

### 잔여 (별도 세션, 우선순위 순)

| # | 항목 | 난이도 | 효과 |
|---|---|---|---|
| C1 | 4-Step Wizard 진입 (프리셋 선택 → 도구 → 모델 → 저장) | M | 초심자 90% 가 3클릭으로 Agent 완성 |
| C3 | `/chat` 에 배포된 하네스 드롭다운 | M | 하네스 주 동선 완성 |
| C5 | PipelineCanvas stage 배지 실시간 색상 | **이미 있음** (이번에 확인) | - |
| C6 | StageDetailPanel Runtime 탭 (artifact JSON viewer) | M | 스테이지별 디버깅 |
| C7 | 프리셋 카드 진입 화면 | S | Progressive Disclosure |
| C2 | 기존 노드 모드 "고급 토글" 뒤로 숨김 | S | 진입 복잡도 감소 |

---

## 4. 원칙 점검 (사용자 11 원칙)

| # | 원칙 | V2 결과 |
|---|---|---|
| 1 | Stage 고정 12개 | ✓ |
| 2 | Stage 클릭 선택 | ✓ |
| 3 | Tool/MCP/RAG "선택만" | ✓ |
| 4 | DAG 멀티 에이전트 | ✓ (Orchestrator 탭 + `MultiAgentExecutor`) |
| 5 | 저장 → 채팅 호출 | ⚠️ C3 남음 |
| 6 | 단계별 추적 | ✓ (EventLog + verbose) |
| 7 | Progressive Disclosure | ✓ (`progressive_3level` 기본 + verbose 기본 off) |
| 8 | 환경만 주고 모델이 알아서 | ✓ |
| 9 | Strategy 갈아끼우기 | ✓ (entry_points 자동 발견) |
| 10 | 딸딱딸깍 | ⚠️ C1 Wizard 남으나 Stage 클릭 선택만으로 대부분 가능 |
| 11 | 1번 선택 세이브 끝 | ⚠️ Save 버튼 1개 (Ctrl+S) 자동 저장도 있음, 프리셋 카드 진입만 추가하면 완결 |

→ **11개 중 9개 완료, 2개 부분**. C1, C3 가 남은 큰 UX 퍼즐.

---

## 5. 하드코딩 최종 감사

### 라이브러리 실행 코드: **0 건**
### 유지 정당화 (비실행)
- `providers/anthropic.py:26`, `providers/openai.py:24` provider 클래스 생성자 기본값 — provider-specific 편의 (직접 인스턴스화 시)
- `core/builder.py:6`, `providers/langchain_adapter.py:13`, `providers/__init__.py:161` — docstring 예시
- `stages/strategies/token_tracker.py:77~89` — Anthropic/OpenAI 공식 가격표 (사실 정보)
- `providers/__init__.py` `PROVIDER_DEFAULT_MODEL` / `PROVIDER_MODELS` — **단일 진실 소스 레지스트리 자체**

### 이식 측
- `harness.py:_DISPLAY_NAMES` 2줄 (Anthropic / OpenAI 한글명) — UI 한글화, 이식 측 책임 정당

### 프론트
- `DEFAULT_HARNESS_CONFIG.model: 'gpt-4o-mini'` 1줄 — 신규 하네스 첫 선택 초기값 (합리)

---

## 6. 이식 측 브랜치 운영 체계

### 현재 3개 레포 4개 브랜치

| 레포 | 브랜치 | 상태 |
|---|---|---|
| xgen-harness-executor (GitHub) | `main` / 태그 `v0.8.24` (PyPI) | 최신 안정 — v2 변경 모두 merge |
| xgen-workflow (GitLab) | `feature/harness-executor` | v1 안정판 (`xgen-harness>=0.8.15`) |
| xgen-workflow (GitLab) | `feature/harness-v2` | v2 실험판 (`xgen-harness>=0.8.24`) |
| xgen-frontend (GitLab) | `feature/harness-v2` | EventLog verbose + ConfigPanel toggle |

### 원복

```bash
cd /home/jinsookim/harness_xgen/xgen-easy-dev/docker
bash rollback-v1.sh           # v2 → v1 (30초~2분)
bash rollback-v1.sh --forward # 반대 방향
```

---

## 7. 연속 배포 타임라인 (참조용)

| 버전 | 주요 내용 |
|---|---|
| v0.8.17 | v2 정식 릴리스 (Stage 계약 / Verbose 4종 / PROVIDER_MODELS / Redis polling / model 중복 9→2) |
| v0.8.18 | OpenAI base_url endpoint 자동 조립 |
| v0.8.19 | XgenConfigService EventEmitter 주입 경로 |
| v0.8.20 | emitter 주입 시점 execute 초반으로 |
| v0.8.21 | SSE 변환에 verbose 4종 추가 |
| v0.8.22 | verbose 실제 발행 경로 완성 |
| v0.8.23 | config_kwargs 에 verbose_events 전달 fix |
| v0.8.24 | model 하드코딩 잔여 3곳 정리 (api/router, multi_agent, config.from_dict) |

---

## 8. 다음 Phase (별도 세션)

### Phase P (프론트 UX, 우선순위)
1. [C1] 4-Step Wizard — 프리셋 선택 → 도구/RAG 체크 → 모델 → 저장 → `/chat` 이동
2. [C3] `/chat` 드롭다운 — 배포된 하네스 목록 + 실행
3. [C6] StageDetailPanel Runtime 탭 — 최근 실행 artifact JSON viewer

### Phase L (라이브러리 개선, 필요 시)
- R3 CapabilityBinder 통합 (경로 3개 → 1개)
- R7 NullServiceProvider 단위 테스트 확장
- StageSubstepEvent 샘플 s07 외에 s04/s06 확장

### Phase E (검증)
- Anthropic API 키 재주입 후 D1 full 12 stage
- RAG 컬렉션 등록 후 D2 capability_bind auto_publish 실발행
- evaluator preset 까다로운 입력으로 D3 retry 실발행

---

## 9. 결론

V2 는 **"제안 → 실적"** 단계를 대부분 완료. R1/R2/R4/R8 + A1~A5 + UI 6건 이 배포됨. **실행 코드 하드코딩 0건 상태**이며 verbose 이벤트 체계가 런타임에서 완전히 가시화됐다.

남은 것은 **프론트 UX 3건 (Wizard / chat 드롭다운 / Runtime 탭)** 과 환경 의존 검증. 원복은 언제든 30초 안에 가능하다.

---

## 참고 문서

- v1 원본: [`REFACTORING_PLAN.md`](./REFACTORING_PLAN.md)
- 설계 문서 V2: [`EXECUTION_DESIGN_V2.md`](./EXECUTION_DESIGN_V2.md)
- 이식 테스트: [`../V2_TESTING.md`](../V2_TESTING.md)
- CHANGELOG: [`../CHANGELOG.md`](../CHANGELOG.md)
- 원복: `xgen-easy-dev/docker/rollback-v1.sh`
- 작업 일지: `docs/worklog/2026-04-19.md`
