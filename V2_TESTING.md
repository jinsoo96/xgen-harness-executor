# xgen-harness v2 브랜치 — 로컬 테스트 가이드

> 이 브랜치(`feature/harness-v2`)는 PyPI에 올리지 않고 **editable install로 컨테이너에 즉시 반영**해 실험하는 용도이다. main 은 안정판, v2 는 실험판.

## 무엇이 바뀌었나 — 한눈에

| 영역 | 파일 | 변경 |
|---|---|---|
| Public API | `__init__.py` | `StageInput/StageOutput/STAGE_IO_SPECS/get_stage_io` + 4개 verbose 이벤트 export |
| 이벤트 | `events/types.py` | `ServiceLookupEvent`, `CapabilityBindEvent`, `StageSubstepEvent`, `RetryEvent` 추가 |
| Config | `core/config.py` | `verbose_events: bool = False` 플래그 |
| ConfigService | `core/services.py` | `get_setting(key, default)` Protocol 메서드 추가 |
| XgenConfigService | `integrations/xgen_services.py` | `event_emitter` 주입 + `_emit_lookup` + `get_api_key/get_setting` 에서 `ServiceLookupEvent` 발행 |
| Registry | `core/registry.py` | artifact 중복 등록 경고 + `describe_all` 결과에 `current_artifact` 노출 |
| Strategy | `core/strategy_resolver.py` | `entry_points(group="xgen_harness.strategies")` 자동 발견 |

**불가침 유지**: 9 엔드포인트 시그니처 0 변화, 12 Stage ID 0 변화, `Stage` 추상 메서드 시그니처 0 변화, 기존 `__all__` 심볼 0 제거.

---

## 책임 분리 매트릭스 — 라이브러리 vs 이식 측 vs 프론트엔드

| 항목 | 라이브러리 (xgen-harness-executor) | 이식 측 (xgen-workflow) | 프론트엔드 (xgen-frontend) |
|---|---|---|---|
| Stage 정의·실행 | ✅ 유일 | ❌ | ❌ |
| Strategy 레지스트리 | ✅ 유일 | ❌ | ❌ |
| Artifact I/O 계약 | ✅ 유일 | ❌ | ❌ |
| Provider 레지스트리 (단일 진실 소스) | ✅ `providers/__init__.py` | 호출만 | 호출만 |
| Provider 기본 모델 이름 | ✅ `PROVIDER_DEFAULT_MODEL` | 호출만 | 호출만 |
| SSE 이벤트 타입 | ✅ 유일 | SSE 변환 담당 | 수신/렌더만 |
| `/harness/*` 라우트 | ❌ | ✅ `harness.py` | 호출만 |
| `harness_execution_log` DDL | ❌ | ✅ `_ensure_exec_table` | ❌ |
| 서비스 URL (xgen-core 등) | ❌ 환경변수로 받음 | ✅ Docker Compose env | ❌ |
| 사용자 인증 (JWT→x-user-*) | ❌ | ✅ gateway + extract_user_session | ❌ |
| UI Provider display name 매핑 | ❌ | ✅ `_DISPLAY_NAMES` (표기용) | 아이콘/번역 |
| stage_params / artifacts 스키마 렌더 | ✅ `get_stage_config` | 그대로 proxy | ✅ UI 렌더 |

**원칙**: 동작은 **라이브러리**, 배치는 **이식 측**, 표시는 **프론트엔드**. 각 레이어 책임 경계 겹치면 하드코딩 누수.

---

## v2 연속 배포 요약 (v0.8.17 → v0.8.23)

| 버전 | 내용 |
|---|---|
| v0.8.17 | v2 정식 릴리스 (Stage 계약 + 4 verbose 타입 + PROVIDER_MODELS + Redis polling + model 하드코딩 9→2) |
| v0.8.18 | OpenAI base_url endpoint 자동 조립 fix |
| v0.8.19 | XgenConfigService emitter 주입 경로 |
| v0.8.20 | emitter 주입 시점 앞당김 |
| v0.8.21 | SSE 변환에 verbose 4종 추가 |
| v0.8.22 | Pipeline/s04/s05/adapter/s07 에 verbose 실제 발행 |
| v0.8.23 | `config_kwargs` 에 verbose_events 전달 fix |

## 최종 검증 결과 (v0.8.23, 2026-04-19)

### Redis 우선 조회 — 런타임 증명 ✅

```
OPENAI_TEMPERATURE_DEFAULT  source=redis  hit=True
OPENAI_MAX_TOKENS_DEFAULT   source=redis  hit=True
OPENAI_API_KEY              source=redis  hit=True  provider=openai
OPENAI_API_BASE_URL         source=redis  hit=True
```

### Verbose 4종 발행 ✅
- `service_lookup`: 4건 (Redis 경로 전부 추적)
- `stage_substep`: 2건 (s07 llm_request_start + llm_response_complete)
- `capability_bind`: 0건 (capabilities 미선언 시 정상)
- `retry`: 0건 (재시도 미발생 시 정상)

### 12 스테이지 E2E ✅
- OpenAI gpt-4o-mini full 12 스테이지 통과
- metrics 이벤트 duration_ms/total_tokens/cost_usd 정상

---

## 원복 (v2 별로면)

`xgen-easy-dev/docker/rollback-v1.sh` 한 줄 실행. 30초~2분 소요. `ROLLBACK.md` 참조.

---

## v2 핵심 개념 요약

### 1. Stage 계약 — "뭘 받고 뭘 내보내나" 선언
```python
from xgen_harness import Stage, StageInput, StageOutput

class MyStage(Stage):
    input_spec = StageInput(requires=["user_input"], optional=["attached_files"])
    output_spec = StageOutput(produces=["my_result"], modifies=["messages"])

    async def execute(self, state):
        ...
```
→ Pipeline 이 실행 전 `missing` 누락 필드 자동 검출 (pipeline.py:158).

### 2. Stage 교체 — "디폴트 복사 → v2 → Config 에서 선택"
```python
from xgen_harness.core.registry import register_stage

class MyValidateV2(Stage):
    @property
    def stage_id(self): return "s09_validate"
    @property
    def order(self): return 9
    async def execute(self, state): ...

register_stage("s09_validate", "v2", MyValidateV2)

# Config 에서 선택
config.artifacts["s09_validate"] = "v2"   # 기본 "default" 대신 "v2" 사용
```

### 3. Strategy 교체 — 같은 Stage 안 알고리즘 갈아끼움
```python
config.active_strategies["s04_tool_index"] = "eager_load"
# progressive_3level(기본) → eager_load 로 실행 시 교체
```

### 4. Verbose 이벤트 — 블랙박스 해소
```python
config = HarnessConfig(verbose_events=True)
# 실행 시 추가 이벤트 발행:
#   - ServiceLookupEvent: Redis vs .env 어디서 키 가져왔는지
#   - CapabilityBindEvent: 선언/발견/자동발행 3경로 중 어느 것
#   - StageSubstepEvent: 스테이지 내부 단계 (rag_fetch / llm_request 등)
#   - RetryEvent: 재시도 발생 시
```

### 5. 외부 Stage/Strategy 플러그인
외부 패키지 `setup.cfg`:
```ini
[options.entry_points]
xgen_harness.stages =
    s99_custom = my_pkg.stages:CustomStage

xgen_harness.strategies =
    s04_tool_index:discovery:my_algo = my_pkg.strategies:MyDiscovery
```
→ `pip install my_pkg` 만으로 자동 등록.

---

## 이식 실구동 테스트 — 3 시나리오

각 시나리오는 **xgen-workflow 컨테이너가 v2 라이브러리를 editable 로 물고** 있는 상태에서 수행.

### 시나리오 A — Anthropic full 12 스테이지 + Redis 우선 조회 검증

1. `persistent_configs.ANTHROPIC_API_KEY` 에 유효 키 설정 (xgen-core UI 또는 SQL UPDATE)
2. Redis 캐시 refresh: `docker exec redis-feature-store redis-cli -a <pass> DEL config:ANTHROPIC_API_KEY`
3. 요청:
```json
{
  "workflow_name": "v2 anthropic full",
  "input_data": "1+1은?",
  "interaction_id": "v2_anthropic_001",
  "harness_config": {
    "preset": "standard",
    "provider": "anthropic",
    "verbose_events": true
  }
}
```
4. 기대: 12 스테이지 enter/exit + `service_lookup` 이벤트 (source=redis, hit=true) + `data` 토큰 스트림 + metrics

### 시나리오 B — OpenAI + builtin tool loop (v0.8.16 fix)

1. 요청:
```json
{
  "harness_config": {
    "provider": "openai",
    "stage_params": {"s04_tool_index": {"builtin_tools": ["discover_tools"]}},
    "verbose_events": true
  }
}
```
2. 기대: s04_tool_index **bypass 안 됨** (`tools_bound>=1`), `tool_call` / `tool_result` 발생

### 시나리오 C — Stage 교체 (v2 artifact)

1. 외부 모듈에서 `register_stage("s09_validate", "v2", MyValidateV2)` 등록
2. Config: `"artifacts": {"s09_validate": "v2"}`
3. 기대: `/harness/stages` 응답의 `current_artifact: "v2"`, 실행 시 MyValidateV2 가 호출

---

## 로컬 실험 절차 (컨테이너 editable install)

### 1. v2 브랜치 체크아웃
```bash
cd /home/jinsookim/harness_xgen/xgen-harness-executor
git checkout feature/harness-v2
```

### 2. 호스트 디렉토리를 컨테이너에 마운트 (docker-compose.yaml)
```yaml
services:
  xgen-workflow:
    volumes:
      - ../xgen-harness-executor:/mnt/xgen-harness-v2:ro
```
→ compose 재시작: `docker compose up -d xgen-workflow`

### 3. 컨테이너 안에서 editable install
```bash
docker exec xgen-workflow bash -c '
  cd /app && \
  uv pip install -e /mnt/xgen-harness-v2 --force-reinstall && \
  uv run python -c "import xgen_harness; print(xgen_harness.__version__)"
'
```
→ 호스트에서 코드 수정하면 컨테이너 즉시 반영 (process 재시작만 필요).

### 4. 코드 수정 후 반영
```bash
# 호스트에서 파일 편집 후
docker compose restart xgen-workflow
```

### 5. v2 동작 확인 — 3가지 스모크 테스트

**(a) verbose 이벤트 활성화**
```bash
docker exec xgen-workflow timeout 60 curl -sN -X POST \
  -H "Content-Type: application/json" \
  -H "x-user-id: 1" -H "x-user-admin: true" \
  -d '{
    "workflow_name": "v2 verbose test",
    "input_data": "hi",
    "interaction_id": "v2_verbose_001",
    "harness_config": {
      "preset": "standard",
      "provider": "openai",
      "model": "gpt-4o-mini",
      "verbose_events": true
    }
  }' \
  http://localhost:8000/api/agentflow/harness/execute/stream | \
  grep -E "service_lookup|capability_bind|stage_substep"
```

**(b) Stage 교체 (Artifact 선택)**
```bash
# harness_config 에 artifacts 지정
"harness_config": {
  ...
  "artifacts": {"s09_validate": "v2"}
}
```

**(c) 외부 Strategy 플러그인**
테스트 패키지를 만들어 `pip install -e` 한 뒤 `uv pip show` 로 확인.

---

## main 과 v2 전환

```bash
# v2 실험 완료, main 으로 돌리기
docker exec xgen-workflow bash -c 'cd /app && uv pip install --force-reinstall xgen-harness'
# 또는
git checkout main  # 호스트에서, 다음 컨테이너 재시작 시 PyPI 버전으로 복귀
```

## 주의

- 이 브랜치는 **PyPI 미배포**. 배포는 `main` 에서만 `v*` 태그 푸시.
- `xgen-workflow` 의 `pyproject.toml` `xgen-harness>=0.8.16` 제약은 그대로. v2 는 `__version__="0.8.16"` 유지 (배포 버전 충돌 방지).
- 실험이 안정되면 **cherry-pick 또는 merge** 로 main 에 병합, `v0.9.0` 으로 태그.

---

## Q&A

**Q. StageContract 라는 새 클래스가 필요한가?**  
A. 이미 `StageInput/StageOutput` 이 역할 동일. v2 에서는 그것을 **public export** 로 승격한 게 핵심. 새 클래스 만들면 중복.

**Q. verbose_events 를 켜면 어디서 이벤트가 나오나?**  
A. 현재는 `XgenConfigService.get_api_key/get_setting` 에서 `ServiceLookupEvent` 발행. 다른 경로(capability bind, stage substep) 는 각 Stage/경로가 `emit_lookup` 패턴을 따라 추가 필요. v2 의 확장 포인트.

**Q. 외부 Stage 가 기존 12 Stage ID 를 덮으면?**  
A. 덮어쓰기 감지 warning 이 `logger.warning("Artifact 덮어쓰기: ...")` 으로 출력. 의도된 교체면 무시, 실수면 잡힘.
