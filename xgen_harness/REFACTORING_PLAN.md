# XGEN Harness — 구조 리팩토링 안

> 짝꿍 문서: `EXECUTION_DESIGN.md` (왜 만들었는지)
> 이 문서: **현재 구조를 더 다듬는 법** — 단, 엔드포인트도 스테이지도 다른 기능도 해치지 않으면서.
>
> 대원칙: **"기존을 부수지 말고, 옆에 새로 두어라. 검증 끝나면 그때 갈아끼워라."**
> 버전 기준: v0.8.13 (2026-04-18).

---

## 0. 이 문서가 답하려는 질문

| 질문 | 한 줄 답 |
|---|---|
| 무엇을 절대 건드리지 않는가? | `/harness/execute/stream` 엔드포인트 시그니처, 12 스테이지 ID, Stage 인터페이스 메서드 시그니처 |
| 무엇을 더 다듬는가? | 인터페이스 정형화, 등록 자동화, 서비스 조회 통일, 이벤트 채널 표준화 |
| 어떻게 안 해치고 다듬는가? | **Side-by-side 패턴** — 새 모듈을 옆에 만들고 기본값으로 swap, 호환 레이어 유지 |
| 끝나면 어떤 모습이 되는가? | 외부 기여자가 *"파일 하나 추가 + entry_point 등록"*만으로 새 Stage / 새 Strategy / 새 Tool을 살 수 있다 |

---

## 1. 현재 상태 진단 (직접 코드 검증 결과)

### 1.1 잘 된 부분 (건드리지 않는다)

| 영역 | 상태 | 근거 |
|---|---|---|
| 레거시 무침범 | A | `editor/`, `service/`, `execution.py` 에 harness 침투 0건 |
| 분리 엔드포인트 | A | `/harness/execute/stream` 완전 구현 (`harness.py:673`) |
| 12 스테이지 정형화 | A | `Stage` 추상 클래스, 모든 s01~s12 동일 인터페이스 |
| Strategy 패턴 | A | `StrategyResolver`, 40+ 구현체 |
| Progressive Disclosure | A | `s04_tool_index` default `progressive_3level` |
| Capability 시스템 | A | 3가지 바인딩 경로 (선언/발견/발행) 모두 작동 |
| DAG Orchestrator | A | `api/router.py:218`에서 실제 호출 |
| Gallery 표준 | A | `ToolPackageSpec` + `entry_points` 자동 발견 |

### 1.2 다듬을 부분 (이 문서의 대상)

| # | 영역 | 현 상태 | 목표 |
|---|---|---|---|
| R1 | Stage 인터페이스 정형화 | `Stage` 추상이 있으나 입력/출력 아티팩트 명세가 코드 주석 수준 | Artifact 스키마를 코드로 강제 |
| R2 | Strategy 등록 자동화 | 수동 import 후 `StrategyResolver`에 등록 | `entry_points`로 자동 발견 |
| R3 | Capability 발행 일원화 | s04 (선언) / s05 (발견) / Adapter (자동) 3개 경로가 각자 코드 | 단일 `CapabilityBinder`로 통합 |
| R4 | Service 조회 순서 통일 | 정책은 "Redis → .env" 이나 호출 체인이 코드마다 다름 | `ServiceProvider` 1개 진입점 강제 |
| R5 | 이벤트 채널 표준화 | SSE 변환이 `harness.py`에서 inline 구현 | `EventChannel` 추상화로 분리 |
| R6 | Gallery 노티스 | 형식은 있으나 외부 기여자용 가이드 페이지 없음 | `CONTRIBUTING.md` + 자동 검증 CLI |
| R7 | 테스트 격리 | 통합 테스트가 실제 xgen 서비스에 의존 | `NullServiceProvider` 기반 단위 테스트 |
| R8 | "v2 복사" 가능성 | 새 Stage 만들면 코어에 손대야 함 | `register_stage()`로 외부 등록 가능 (이미 부분 지원) |

---

## 2. 절대 건드리지 않는 것 (불가침 영역)

다음 항목들은 변경 시 외부 호환성을 깬다. **리팩토링 대상이 아니다.**

### 2.1 외부 API 시그니처

```
POST /harness/execute/stream
GET  /harness/presets
GET  /harness/stages
GET  /harness/options/{source}
GET  /harness/workflows
GET  /harness/config/{workflow_id}
PUT  /harness/config/{workflow_id}
GET  /harness/executions
GET  /harness/executions/{execution_id}
```

→ 이 9개 엔드포인트의 요청/응답 스키마는 **얼리지 않는다**. 내부 구현은 자유롭게.

### 2.2 12 스테이지 ID

`s01_input` ~ `s12_complete`. ID는 캐시 키, 이벤트 라벨, 프리셋 매핑 등에 박혀 있다. **새 스테이지를 만들면 s13_*** 부터.

### 2.3 Stage 추상 메서드 시그니처

```python
class Stage(ABC):
    @property
    def stage_id(self) -> str: ...
    @property
    def order(self) -> int: ...
    async def execute(self, state: PipelineState) -> dict: ...
    def should_bypass(self, state: PipelineState) -> bool: ...
    def list_strategies(self) -> list[StrategyInfo]: ...
```

→ **추가는 OK, 변경은 NO.** 외부 Stage 구현체가 이 시그니처에 의존한다.

### 2.4 Public Export

`xgen_harness/__init__.py`의 `__all__` 70+ 심볼. 추가는 OK, 제거/변경은 NO.

---

## 3. 영역별 리팩토링 안

### R1. Stage 인터페이스 정형화 — 입력/출력 아티팩트 명세

#### 현 상태
```python
class ToolIndexStage(Stage):
    async def execute(self, state: PipelineState) -> dict:
        # 무엇을 받아서 무엇을 만드는지는 코드 주석으로만 표현
        ...
```

→ 외부 기여자가 새 Stage를 만들 때 *"내가 무엇을 받고 무엇을 만들어야 하는가"*를 추측해야 한다.

#### 리팩토링 안 — `StageContract` 추가 (옵션 메타데이터)

```python
# core/stage.py 에 추가 (기존 Stage는 그대로)
@dataclass(frozen=True)
class StageContract:
    """스테이지의 입출력 명세 — 선언적, 검증용"""
    consumes: list[str]   # state.<key> 중 읽는 것 (예: ["user_input", "tool_definitions"])
    produces: list[str]   # state.<key> 중 쓰는 것 (예: ["tool_index", "metadata.rag_collections"])
    optional_consumes: list[str] = field(default_factory=list)

class Stage(ABC):
    # ... 기존 메서드 그대로 ...

    @property
    def contract(self) -> Optional[StageContract]:
        """선언적 입출력 — 없으면 None (하위 호환)"""
        return None
```

#### 왜 이게 안 해치는가
- 기존 Stage는 `contract` 메서드를 구현 안 해도 됨 (default None)
- 새 Stage만 작성 시 권장
- 런타임에 contract 검증은 *옵션* (개발 모드에서만 활성화)

#### 효과
- 외부 기여자: "이 스테이지가 무엇을 읽고 쓰는지" 한눈에 보임
- 도구: contract 정보로 자동 의존 그래프 그리기 가능
- 테스트: contract 위반 자동 검출

#### 영향
- 기존 코드 0줄 변경
- 새 추가 코드: `StageContract` 클래스 + 12개 Stage에 contract 선언 (선택)

---

### R2. Strategy 등록 자동화 — entry_points 발견

#### 현 상태
```python
# strategies/discovery.py 안에 클래스 정의
class ProgressiveDiscovery: ...
class EagerLoad: ...

# StrategyResolver에 수동 등록 (init 시점)
resolver.register("s04_tool_index", "progressive_3level", ProgressiveDiscovery)
```

→ 외부 패키지가 새 Strategy를 추가하려면 `StrategyResolver`에 직접 등록 코드를 박아야 한다.

#### 리팩토링 안 — entry_points 자동 등록

```python
# core/strategy_resolver.py 에 추가
def discover_strategies_from_entry_points():
    """xgen_harness.strategies entry_point에서 자동 발견"""
    import importlib.metadata as md
    for ep in md.entry_points(group="xgen_harness.strategies"):
        spec = ep.load()  # {"stage_id": "...", "name": "...", "factory": Class}
        get_default_resolver().register(spec["stage_id"], spec["name"], spec["factory"])
```

```python
# 외부 패키지의 setup.cfg
[options.entry_points]
xgen_harness.strategies =
    my_discovery = my_pkg.strategies:DISCOVERY_SPEC
```

#### 왜 이게 안 해치는가
- 기존 builtin Strategy는 코드에서 직접 등록 (변경 없음)
- entry_points는 **추가** 발견 메커니즘 — 빈 entry_points면 no-op
- `Pipeline` 초기화 시점 1회 호출

#### 효과
- 외부 기여자: PyPI 패키지 + entry_point만으로 Strategy 추가
- 코어 PR 불필요

#### 영향
- 기존 코드: `Pipeline.__init__` 또는 `Pipeline.from_config`에 1줄 추가 (`discover_strategies_from_entry_points()`)
- 신규 코드: `core/strategy_resolver.py` 에 함수 1개

---

### R3. Capability 발행 일원화 — `CapabilityBinder`

#### 현 상태

3가지 경로가 각자 다른 위치에서 capability를 바인딩:

| 경로 | 위치 | 트리거 |
|---|---|---|
| 선언 | `s04_tool_index._bind_capabilities()` | `config.capabilities` |
| 발견 | `s05_plan._discover_and_bind_capabilities()` | `mode=capability` |
| 자동 발행 | `adapters/resource_registry.py` | XgenAdapter 시작 시 |

→ "capability가 왜 이 시점에 들어왔는가"를 추적할 때 3곳을 다 봐야 한다.

#### 리팩토링 안 — `CapabilityBinder` 단일 진입점

```python
# capabilities/binder.py (신규)
class CapabilityBinder:
    """모든 capability 바인딩의 단일 진입점.

    s04, s05, Adapter 모두 이걸 통해 호출.
    바인딩 시점/이유를 metadata에 기록 → 추적 가능.
    """
    def bind(
        self,
        names: list[str],
        state: PipelineState,
        *,
        source: Literal["declaration", "discovery", "auto_publish"],
        params: Optional[dict] = None,
    ) -> BindResult:
        """capability 이름들을 바인딩하고 metadata['bindings_log']에 기록"""
        ...
```

#### 왜 이게 안 해치는가
- 기존 `materialize_capabilities()` + `merge_into_state()` 함수는 그대로 둠 (binder가 내부적으로 호출)
- s04 / s05 / Adapter의 호출부만 binder 사용으로 교체
- 외부 API 변화 0

#### 효과
- 모든 바인딩 이력이 `state.metadata["bindings_log"]`에 기록됨 → 디버깅 즉시
- 향후 새 바인딩 경로 (예: 사용자가 채팅 중 추가) 추가 시 binder 한 곳만 호출
- 단위 테스트: binder만 모킹하면 3가지 경로 모두 테스트 가능

#### 영향
- 신규: `capabilities/binder.py`
- 변경: `s04_tool_index.py`, `s05_plan.py`, `adapters/resource_registry.py` 의 호출부 (각 5~10줄)

---

### R4. Service 조회 순서 통일 — `ServiceProvider` 강제

#### 현 상태

설정 조회 정책: **Redis → .env** (사용자 명시).
실제 코드: 일부는 `os.environ` 직접, 일부는 `ServiceRegistry`, 일부는 `ExecutionContext`.

→ 정책과 실제가 어긋날 위험.

#### 리팩토링 안 — `ServiceProvider.get_setting()` 표준화

```python
# core/services.py
class ServiceProvider(Protocol):
    async def get_setting(
        self,
        key: str,
        *,
        default: Optional[str] = None,
    ) -> Optional[str]:
        """설정 조회. Redis → .env 순서 강제."""
        ...

# adapters/xgen.py — XgenServiceProvider 구현
class XgenServiceProvider(ServiceProvider):
    async def get_setting(self, key, *, default=None):
        # 1. Redis
        if val := await self._redis.get(f"setting:{key}"):
            return val
        # 2. xgen-core /api/data/config (DB)
        if val := await self._fetch_from_core(key):
            return val
        # 3. .env
        return os.environ.get(key, default)
```

#### 왜 이게 안 해치는가
- 기존 `os.environ` 호출은 그대로 둠 (점진 교체)
- 새 코드는 무조건 `provider.get_setting()` 사용
- lint 룰로 `os.environ` 신규 사용 금지 (점진 적용)

#### 효과
- Redis 우선 정책이 코드로 강제됨 (정책 vs 실제 일치)
- 런타임 설정 변경이 즉시 반영됨 (Redis TTL 갱신)
- 테스트: Mock provider 주입으로 환경 격리

#### 영향
- 신규: `XgenServiceProvider.get_setting()` 메서드 보강
- 변경: `os.environ.get()` 직접 호출하는 곳을 점진 교체 (한 번에 다 안 해도 됨)

---

### R5. 이벤트 채널 표준화 — `EventChannel`

#### 현 상태

`harness.py:_harness_stream()`이 SSE 변환을 inline 구현:
```python
async for event in adapter.execute(...):
    yield f"data: {json.dumps(event)}\n\n"
```

→ 새 채널(WebSocket, gRPC, Kafka)을 추가하려면 어댑터 코드 또 작성.

#### 리팩토링 안 — `EventChannel` 추상화

```python
# events/channel.py (신규)
class EventChannel(Protocol):
    async def send(self, event: HarnessEvent) -> None: ...
    async def close(self) -> None: ...

class SSEChannel(EventChannel):
    """기존 SSE 변환 로직을 그대로 옮김"""
    ...

class WebSocketChannel(EventChannel):
    """미래용 — 양방향"""
    ...
```

#### 왜 이게 안 해치는가
- 기존 `harness.py`의 SSE 로직은 `SSEChannel` 안으로 그대로 이동 (동작 동일)
- 호출부만 `channel = SSEChannel(); async for e in adapter.execute(): await channel.send(e)` 로 변경
- 외부 SSE 응답 포맷 0 변화

#### 효과
- 채널 추가 = `EventChannel` 새 구현 1개
- 테스트: `MemoryChannel` 로 이벤트 수집 후 검증 용이

#### 영향
- 신규: `events/channel.py`
- 변경: `harness.py` 의 `_harness_stream()` (10줄 정도)

---

### R6. Gallery 노티스 — 외부 기여자 가이드

#### 현 상태

`tools/gallery.py`에 표준 형식이 코드로는 있으나, *"외부 기여자가 이 도구를 쓰려면 이 페이지를 보세요"* 라는 진입점이 없다.

#### 리팩토링 안 — `CONTRIBUTING_TOOLS.md` + CLI 검증

```
xgen-harness-executor/
├── CONTRIBUTING_TOOLS.md         (신규) 외부 기여자 가이드
├── xgen_harness/
│   ├── tools/
│   │   └── gallery.py             (기존)
│   └── cli/
│       └── validate_tool.py       (신규) — 패키지 검증 CLI
```

`CONTRIBUTING_TOOLS.md` 내용:
1. 패키지 구조 (TOOL_DEFINITIONS, call_tool)
2. entry_points 등록 방법
3. 검증 CLI 사용법: `xgen-harness validate-tool <package>`
4. 갤러리 PR 절차

CLI 동작:
```bash
$ xgen-harness validate-tool my_tools
✓ TOOL_DEFINITIONS found (5 tools)
✓ call_tool signature OK
✓ entry_point registered: xgen_harness.tools = my_tools
✓ Description length OK (Progressive Disclosure compliant)
```

#### 왜 이게 안 해치는가
- 기존 `tools/gallery.py` 0 변경
- 신규 CLI는 별도 entry_point (`xgen-harness validate-tool`)

#### 효과
- 외부 기여자: 가이드 따라 PR 가능
- 자동 검증으로 PR 리뷰 부담 감소

#### 영향
- 신규: `CONTRIBUTING_TOOLS.md`, `cli/validate_tool.py`

---

### R7. 테스트 격리 — `NullServiceProvider` 기반 단위 테스트

#### 현 상태

통합 테스트 `test_integration_runtime.py`가 실제 xgen 서비스(API 키, RAG)에 의존.
→ CI에서 외부 의존 없이 돌릴 수 없음.

#### 리팩토링 안 — Mock Provider 확장

```python
# core/services.py — NullServiceProvider 보강 (이미 있음, 기능 추가)
class NullServiceProvider(ServiceProvider):
    """테스트용 — 모든 호출이 sentinel 반환"""

    def __init__(self, *, settings: dict = None, llm_response: str = "mock"):
        self._settings = settings or {}
        self._llm_response = llm_response

    async def get_setting(self, key, *, default=None):
        return self._settings.get(key, default)

    async def call_llm(self, **kwargs):
        return {"content": self._llm_response, "usage": {...}}

    async def search_rag(self, query, **kwargs):
        return [{"content": "mock chunk", "score": 0.9}]
```

#### 왜 이게 안 해치는가
- 기존 `NullServiceProvider` 시그니처 유지 (확장만)
- 통합 테스트는 그대로 두고, 단위 테스트만 신규 추가

#### 효과
- 단위 테스트가 외부 서비스 없이 실행
- CI 에서 PR 마다 빠른 피드백

#### 영향
- 신규: `tests/unit/test_pipeline_with_mock.py`
- 변경: `NullServiceProvider` 확장 (하위 호환)

---

### R8. "v2 복사" 가능성 — 외부 Stage 등록

#### 현 상태

`register_stage()` 함수는 이미 있음 (`core/registry.py`). 하지만 사용 예시가 라이브러리 외부에 없음.
→ "내가 새 Stage 만들고 싶은데 어떻게 끼우나요?" 가이드 부재.

#### 리팩토링 안 — 외부 Stage 패턴 문서화 + entry_points

```python
# 외부 패키지: my_stages/__init__.py
from xgen_harness import Stage, register_stage

class MyCustomValidationStage(Stage):
    @property
    def stage_id(self): return "s09_validate_v2"  # 기존 v1 옆에
    @property
    def order(self): return 9.5  # s09와 s10 사이

    async def execute(self, state):
        ...

# entry_point 등록
[options.entry_points]
xgen_harness.stages =
    my_validation = my_stages:MyCustomValidationStage
```

`Pipeline.from_config()`가 entry_points를 발견 → 자동 등록 → `config.active_strategies`에서 선택.

#### 왜 이게 안 해치는가
- 기존 12 스테이지는 그대로 (s09는 s09)
- 외부 Stage는 별도 ID (s09_validate_v2) → 충돌 없음
- 사용자가 명시적으로 선택해야 활성화

#### 효과
- "**복사 → v2 → 등록**" 패턴이 실제로 가능 (사용자 노트의 요구 사항)
- 코어 무수정 외부 확장

#### 영향
- 신규: 문서 (`CONTRIBUTING_STAGES.md`)
- 변경: `Pipeline.from_config()`에 entry_points 발견 1줄 추가

---

## 4. 어떻게 영향 없이 해내는가 — Side-by-side 패턴

### 4.1 패턴 정의

> **새 모듈을 옆에 만들고, 기본값을 새것으로 swap. 기존은 한동안 유지하다가 deprecated 표시 후 제거.**

```
[Phase 1] 신규 코드 추가 (기존 코드 0 변경)
   │
   ▼
[Phase 2] 기본값 교체 (호환 레이어로 기존 호출 유지)
   │
   ▼
[Phase 3] 기존 코드 deprecated 표시 (warning 출력)
   │
   ▼
[Phase 4] (메이저 버전) 기존 코드 제거
```

### 4.2 각 리팩토링별 적용

| 리팩토링 | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|---|---|---|---|---|
| R1 contract | StageContract 클래스 추가 | 12 스테이지에 선언 | 미선언 시 warning | 강제 |
| R2 entry_points | discover 함수 추가 | Pipeline.init에서 호출 | 수동 등록 deprecated | 자동만 허용 |
| R3 binder | CapabilityBinder 추가 | s04/s05/Adapter 교체 | 직접 호출 deprecated | 제거 |
| R4 services | get_setting 표준화 | 신규 코드 강제 | os.environ 직접 호출 lint 경고 | 차단 |
| R5 channel | EventChannel 추가 | harness.py 교체 | inline 변환 deprecated | 제거 |
| R6 gallery | CONTRIBUTING + CLI | 갤러리 PR 절차 안내 | 검증 강제 | (제거 대상 아님) |
| R7 testing | Mock 확장 | 단위 테스트 추가 | (적용 대상 아님) | (적용 대상 아님) |
| R8 stages | entry_points 발견 | 문서화 | 외부 등록 권장 | (제거 대상 아님) |

### 4.3 왜 이 패턴이 안전한가

- **각 Phase가 독립 배포 가능** → 실패 시 롤백 단위가 작다
- **호환 레이어 유지** → 외부 호출자에게 즉각 영향 없음
- **deprecated 명시** → 마이그레이션 시간 충분히 줌
- **메이저 버전에서만 제거** → semver 약속

---

## 5. 단계별 실행 계획

### Phase 1 (1주) — 인터페이스 보강 (코드 0 영향)
- [ ] R1: `StageContract` 추가 + 12 스테이지에 선언
- [ ] R7: `NullServiceProvider` 확장 + 단위 테스트 골격
- [ ] R6: `CONTRIBUTING_TOOLS.md` 작성

### Phase 2 (2주) — 등록 자동화 (호환 레이어)
- [ ] R2: `entry_points` 자동 발견 함수 + Pipeline 초기화 1줄 추가
- [ ] R8: 외부 Stage 등록 문서화

### Phase 3 (2주) — 일원화 (점진 교체)
- [ ] R3: `CapabilityBinder` 추가, s04/s05/Adapter 호출부 교체
- [ ] R4: `XgenServiceProvider.get_setting()` 강화

### Phase 4 (2주) — 채널 분리
- [ ] R5: `EventChannel` 추상화, `SSEChannel` 분리
- [ ] R6: validation CLI 추가

### Phase 5 (지속) — Deprecation
- [ ] 각 리팩토링별 deprecated warning 출력
- [ ] 마이그레이션 가이드 작성

→ 모든 Phase에서 **외부 API 시그니처 0 변화** 보장.

---

## 6. 검증 방법 — "안 깨졌다"를 어떻게 증명하는가

### 6.1 자동 검증

| 항목 | 방법 |
|---|---|
| API 호환 | OpenAPI 스키마 diff (`schemathesis`) |
| Stage ID 보존 | `assert ALL_STAGES == [...]` 단위 테스트 |
| Public symbol 보존 | `__all__` snapshot 테스트 |
| 이벤트 포맷 | golden file 비교 |

### 6.2 수동 검증

1. **레거시 시나리오**: 캔버스 워크플로우 10개 실행 → diff 0
2. **하네스 시나리오**: 12 스테이지 E2E 시나리오 5개 → diff 0
3. **외부 통합**: xgen-frontend `/harness` 페이지 → 동작 확인

### 6.3 비교 실행 (Side-by-side)

같은 입력을 캔버스와 하네스 두 경로로 실행 → 결과 비교 → **두 결과가 같거나 하네스가 더 좋아야** 통과.
사용자 노트 *"두 개를 테스트도 해보면 좋다"*의 구체화.

---

## 7. 무엇이 좋아지는가 — 리팩토링 후 체감

| 시나리오 | Before | After |
|---|---|---|
| 외부 기여자가 새 도구 추가 | 코어 PR + 리뷰 | PyPI 배포 + entry_point |
| 새 알고리즘으로 검색 교체 | s04 코드 수정 | 새 Strategy 클래스 1개 + entry_point |
| Capability 바인딩 디버깅 | 3 곳 코드 추적 | `state.metadata["bindings_log"]` 1곳 |
| WebSocket 채널 추가 | harness.py 대수술 | `EventChannel` 구현 1개 |
| 단위 테스트 | xgen 서비스 필요 | `NullServiceProvider` 주입 |
| 새 Stage 끼우기 | 코어 등록 + 순서 조정 | 외부 패키지 + `register_stage()` |

→ **"모두가 살을 얹을 수 있는 구조"의 구체적 형태.**

---

## 8. 결론 — 이 리팩토링이 왜 정당한가

1. **외부 호환성 보존** — API/Stage ID/시그니처 0 변화
2. **점진적 적용** — Phase별 독립 배포, 롤백 안전
3. **검증 가능** — 자동 + 수동 + side-by-side 비교
4. **외부 기여자 친화** — 코어 PR 없이 기능 추가 가능
5. **사용자 원칙 일치**:
   - "기존 로직 훼손 금지" → Side-by-side 패턴
   - "확장성 있는 구조" → entry_points 자동 발견
   - "v2 복사" → 외부 Stage 등록
   - "DAG 따라가는 오케스트레이터" → 이미 있음, 보존

> **이 리팩토링은 "더 잘 만들기"가 아니라 "더 잘 살게 만들기"이다.**
> 코드를 다시 쓰는 게 아니라, 외부가 우리 코드 위에서 살 수 있게 만드는 것.

---

## 부록 A. 우선순위 매트릭스

| 리팩토링 | 영향력 | 위험도 | 우선순위 |
|---|---|---|---|
| R3 Capability binder | 높음 (디버깅 즉시) | 낮음 | **1순위** |
| R4 Service 통일 | 높음 (정책 강제) | 중간 | **1순위** |
| R2 entry_points | 중간 (확장성) | 낮음 | **2순위** |
| R7 테스트 격리 | 높음 (CI 가능) | 낮음 | **2순위** |
| R5 EventChannel | 중간 (미래 대비) | 낮음 | 3순위 |
| R1 StageContract | 낮음 (메타데이터) | 0 | 3순위 |
| R6 Gallery 가이드 | 중간 (생태계) | 0 | 3순위 |
| R8 외부 Stage | 중간 (확장성) | 낮음 | 3순위 |

→ **R3 + R4 부터 시작.** 둘 다 *"정책 vs 실제"* 일치를 강제하는 작업으로, 가장 큰 ROI.

---

## 부록 B. 절대 하지 말 것

| ❌ 하지 말 것 | 왜 |
|---|---|
| 12 스테이지 ID 변경 | 캐시/이벤트/프리셋 깨짐 |
| 9개 엔드포인트 시그니처 변경 | 프론트 깨짐 |
| `Stage` 추상 메서드 시그니처 변경 | 외부 Stage 깨짐 |
| `__all__` 심볼 제거 | 외부 import 깨짐 |
| `execution.py` 에 harness 분기 추가 | 레거시 무침범 원칙 위반 |
| `editor/`, `service/` 에 harness 코드 추가 | 동상 |
| 한 PR에 여러 리팩토링 묶기 | 롤백 불가 |
| 호환 레이어 없이 직접 교체 | 외부 호출자 깨짐 |

---

## 부록 C. 다음에 읽을 문서

- `EXECUTION_DESIGN.md` — 이 모든 게 왜 만들어졌는지
- `docs/harness/06-INTEGRATION-GUIDE.md` — 이식 작업 가이드
- `docs/harness/00-ARCHITECTURE.md` — 전체 아키텍처
- `tools/gallery.py` — Gallery 표준 코드 (외부 기여자용)
