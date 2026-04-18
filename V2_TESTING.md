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
