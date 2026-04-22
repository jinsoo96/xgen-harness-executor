<div align="center">

# xgen-harness

### Harnee Engineering 철학 기반 12-Stage 에이전트 실행 프레임워크 

[![PyPI](https://img.shields.io/pypi/v/xgen-harness?color=blue&label=PyPI)](https://pypi.org/project/xgen-harness/)
[![Python](https://img.shields.io/pypi/pyversions/xgen-harness)](https://pypi.org/project/xgen-harness/)
[![License](https://img.shields.io/pypi/l/xgen-harness)](https://pypi.org/project/xgen-harness/)

```bash
pip install xgen-harness
```

</div>

---

## 한 줄 요약

> 워크플로우를 **"짜는 것"** 이 아니라 **"설정하는 것"** 으로 바꾼 에이전트 실행기.
> 사용자는 **무엇을** 할지 선언, 하네스는 **어떻게** 를 자동 조립.
> 현재 상태(`v0.11.1`): Stage × Strategy × Capability 3층, 40+ Strategy, 컴파일러(→ wheel), DAG 오케스트레이터.

---

## 인터페이스 구조 (실행 흐름)

```
  [Config 선언] → [Pipeline 조립] → [Runtime 바인딩] → [실행] → [Result]
        ↑               ↑                  ↑              |         |
        |               |                  |              |         |
   Save/Load JSON   Stage Plugin      Capability 자동   SSE Events  Replay
   Builder API     Strategy Swap     Tool/RAG/MCP      Guard 체인  Metrics
   Preset/Share    Variant 복사       Param Resolver    재시도 루프  Cost 집계
                   Artifact 선택
                                                            ↓
                                                    xgen.compile(wf)
                                                    → pip 가능한 wheel
```

| 박스 | 책임 | 핵심 API |
|---|---|---|
| **Config 선언** | `HarnessConfig` dataclass — provider/model/stage_params/active_strategies/capabilities/strategy_variants/external_inputs | `to_dict` / `from_dict` / `from_workflow` / `save` / `load` / `PipelineBuilder` / `PRESETS` |
| **Pipeline 조립** | `ArtifactRegistry.build_pipeline_stages` — 등록된 Stage + Strategy 자동 조립 | `register_stage` / `StrategyResolver` / `entry_points` |
| **Runtime 바인딩** | `ServiceProvider` 주입 + `CapabilityRegistry` 발행 + `ParameterResolver` | `ResourceRegistry.publish_capabilities` / `NodeAdapter` |
| **실행** | `Pipeline.run` 12 Stage 순차 + Agentic Loop + on_error/RetryEvent | `EventEmitter` / `verbose_events` |
| **Result** | `state.validated_output` + `MetricsEvent` + `DoneEvent` | SSE 스트림 / Replay (events 저장) |
| **Compile** | `xgen.compile(wf)` → `xgen-gallery-<name>` wheel — `pip install` 후 `arun("입력")` 한 줄 | `compile_workflow` / `build_wheel` / `load_snapshot` |

### 확장 통로 (통로 패턴 — 핵심 코드 불변)

| 확장 지점 | 빌트인 + 외부 플러그인 |
|---|---|
| Stage 추가 / swap | `register_stage()` · `entry_points(group="xgen_harness.stages")` |
| Strategy 추가 | `register_strategy()` · `entry_points(group="xgen_harness.strategies")` |
| **Strategy Variant** | `HarnessConfig.strategy_variants` — 디폴트 impl 을 파라미터만 바꿔 "복사본 v2" 로 노출 (v0.10.4) |
| **NodeAdapter 추가** | `register_node_adapter()` · `entry_points(group="xgen_harness.node_adapters")` |
| 이식 측 옵션 소스 | `register_option_source()` · `entry_points(group="xgen_harness.option_sources")` |
| 프론트 Stage selector | `registerStageSelector(stage_id, Component)` |
| Capability 추가 | `CapabilityRegistry.register()` · Adapter `publish_capabilities()` |
| LLM Provider 추가 | `register_provider()` · `entry_points(group="xgen_harness.providers")` |
| DependencyRule (컴파일러) | `register_dependency_rule()` · 외부 SDK 자동 wheel 의존성 주입 |
| Evaluation Criterion | `register_evaluation_criterion()` · s09 평가 기준 추가 |
| Fan-out Strategy (DAG) | `register_fan_out_strategy()` · 멀티에이전트 분기 규칙 |

---

## 3층 아키텍처 — Stage · Strategy · Resource

```
                       ┌──────────────────────────────────┐
   ┌──────────────────▶│           Resource               │
   │                   │  MCP · RAG · DB · API · Gallery  │
   │                   │  · Capability (선언형)           │
   │                   └──────────────────────────────────┘
   │                                   ▲
   │                                   │ register_service()
   │                                   │ register_tool_source()
   │      ┌────────────────────────────┴──────────────────┐
   │      │                  Strategy                     │
   │      │  ExponentialBackoff · AnthropicCache ·        │
   │      │  LLMJudge · RuleBased · Progressive3Level ·   │
   │      │  Threshold · AlwaysPass · ContentGuard · ...  │
   │      │  40+ 구현체, StrategyResolver 로 런타임 교체  │
   │      └───────────────────────────┬──────────────────┘
   │                                  │
   │        ┌─────────────────────────┴────────────────────┐
   └────────┤                     Stage                    │
            │  s01 Input · s02 History · s03 Prompt        │
            │  s04 Tool · s05 Strategy · s06 Context       │
            │  s07 LLM · s08 Act · s09 Judge               │
            │  s10 Decide · s11 Save · s12 Finalize        │
            │  12개 고정 슬롯 (Artifact 로 구현 swap)      │
            └──────────────────────────────────────────────┘
```

| 층 | 역할 | 실제 API |
|---|------|----------|
| **Stage** | 12개 고정 슬롯 — Artifact 로 구현 교체 | `register_stage()` / `disabled_stages` |
| **Strategy** | Stage 내부 알고리즘 교체 | `register_strategy()` / `active_strategies` / `strategy_variants` |
| **Resource** | 외부 자원 (MCP/RAG/DB/API/Capability) | `register_service()` / `CapabilityRegistry` |

**핵심 원칙**
- **라이브러리 ≠ 인프라**: 라이브러리는 URL·API 키·프로바이더 이름을 모른다 → 어댑터가 주입
- **Graceful skip**: 미등록 자원은 에러가 아니라 자동으로 건너뜀
- **무침범**: 새 Stage/Strategy/Tool 추가할 때 기존 코드 1줄도 수정할 필요 없음
- **하드코딩 0**: `if provider == "..."`, `if stage_id == "..."` 같은 분기 전부 제거 (v0.11.1 까지 이어진 정화)

---

## 확장성 & 안정성 현황 (v0.11.1 기준)

| 확장 지점 | 방식 | 등급 |
|----------|------|------|
| **Stage 추가** | `register_stage()` + entry_points 자동 발견 + Pipeline 전역 레지스트리 결합 | **A** |
| **Strategy 교체** | `StrategyResolver` 전역 레지스트리 (40+ 구현체, 런타임 교체) | **A** |
| **Strategy Variant** | 디폴트 건들지 않고 복사본 파라미터만 교체해 사용자 정의 v2 노출 (v0.10.4) | **A** |
| **LLM 프로바이더** | `PROVIDER_REGISTRY` + `register_provider()` + `PROVIDER_DEFAULT_MODEL` 단일 진실 소스 | **A** |
| **Tool 소스** | `ToolSource` Protocol + Gallery entry_points + [`TOOL_GUIDE`](../docs/harness/TOOL_GUIDE.md) | **A** |
| **서비스 URL** | `ServiceRegistry` + 환경변수 폴백 + graceful skip | **A** |
| **Capability** | 타입 무관 `CapabilityRegistry` (5개 인덱스, 3가지 바인딩 경로) | **A** |
| **Config 직렬화** | `dataclasses.fields()` 자동 순회 — 새 필드 추가해도 코드 수정 불필요 | **A** |
| **UI 옵션 자동 주입** | provider/model 드롭다운이 레지스트리에서 동적 해석 | **A** |
| **에러 처리** | `ErrorCategory` + `recoverable` + on_error 훅 (일반 예외도 복구 경유) | **A** |
| **Decide 분기** | `DecideStrategy.decide()` 로 Stage 내부 if/else 0줄 (v0.11.1) | **A** |
| **ContentGuard** | 사용자 정의 패턴 + PII(이메일/휴대폰/주민번호/카드) 감지 실구현 (v0.11.1) | **A** |
| **컴파일러** | `xgen.compile(wf)` → pip 가능 wheel. 외부 SDK 의존성도 `register_dependency_rule()` 로 자동 주입 | **A** |
| **DAG 오케스트레이터** | 멀티에이전트 SSE 분기/병합 + fan-out Strategy 레지스트리 | **A** |
| **전체 plug-and-play 성숙도** | | **~97%** |

모든 확장 지점이 **레지스트리 + 팩토리 + ABC/Protocol** 기반. 라이브러리 본체에 하드코딩된 프로바이더/모델/판정 로직 **0건**.

### 허브 정신 일관성 체크 (자동)

- `HarnessConfig.to_dict()` — `dataclasses.fields()` 순회로 자동 직렬화. 새 필드 추가 시 직렬화 코드 수정 불필요.
- `_extract_agent_config_from_nodes()` — `list_providers()` 순회로 per-provider 기본 모델을 레지스트리에서 해석.
- 사용자가 `register_provider("my_llm", MyProvider)` 한 줄 추가만 해도 config / UI / stage / 직렬화 전부 자동 반영.
- `s10_decide` 는 `DecideStrategy.decide()` 로 전적 위임 — 새 판단 알고리즘 추가도 Strategy 한 클래스로 끝.

---

## 3가지 바인딩 경로 (Capability 시스템)

사용자가 도구를 붙이는 방법은 셋 다 지원됨:

```
① 선언 바인딩                        ② 발견 바인딩                  ③ 자동 발행
   (capabilities 필드)                 (s05 natural intent)           (Adapter → Registry)
   ↓                                   ↓                              ↓
config.capabilities =                "뉴스 찾아서 요약해줘"          워크플로우 노드
["retrieval.web_search"]                    ↓                        → MCP/API/DB/RAG
   ↓                                   Matcher 매칭                   → publish_capabilities()
s04_tool 바인딩                      s04 바인딩                      → ①②가 사용 가능
```

실행 중 LLM이 빠뜨린 필수 파라미터는 `ParameterResolver` 가
`provided → context → llm_infer → default` 우선순위로 자동 채움.

---

## 빠른 시작

### 독립 실행 (어댑터 없이)

```python
from xgen_harness import Pipeline, PipelineState, HarnessConfig, EventEmitter
from xgen_harness.core.execution_context import set_execution_context

set_execution_context(api_key="sk-...", provider="openai", model="gpt-4o-mini")

config = HarnessConfig(
    provider="openai", model="gpt-4o-mini",
    capabilities=["retrieval.web_search"],   # 선언만 — 하네스가 조립
)
pipeline = Pipeline.from_config(config, EventEmitter())
state = PipelineState(user_input="오늘 한국 날씨 알려줘")

await pipeline.run(state)
print(state.final_output)
```

### xgen-workflow 연동

```python
from xgen_harness.adapters.xgen import XgenAdapter

adapter = XgenAdapter(db_manager=db_manager)
async for event in adapter.execute(workflow_data, input_data, user_id=user_id):
    yield event  # xgen SSE 포맷 (그대로 프론트에 전달 가능)
```

### 워크플로우 → wheel 컴파일 (v0.10.0+)

```python
import xgen_harness as xh

# 하네스 워크플로우 하나를 pip install 가능한 wheel 로 변환
result = xh.compile_workflow(
    harness_config=config,
    workflow_data={"nodes": [...], "edges": [...]},
    gallery_name="team_q_and_a",
    gallery_version="0.1.0",
    out_dir="./dist",
)
# → dist/xgen_gallery_team_q_and_a-0.1.0-py3-none-any.whl

# 받는 쪽은 한 줄로 설치 + 실행
# $ pip install xgen-gallery-team-q-and-a
# >>> from xgen_gallery_team_q_and_a import arun
# >>> await arun("안녕?")
```

`${OPENAI_API_KEY}` / `${MY_API_URL}` 같은 참조는 자동 스캔해서 `env.example` 생성. 외부 SDK 의존성도 `register_dependency_rule()` 로 자동 wheel 에 주입. 폐쇄망 시나리오(`pip install --no-index`)까지 검증됨.

### 확장 예시 (코드 수정 없이 꽂기)

```python
from xgen_harness import (
    register_provider, register_stage, register_tool_source, register_service,
)
from xgen_harness.core.strategy_resolver import register_strategy

# Strategy 교체
register_strategy("s09_judge", "evaluation", "strict", StrictJudge)

# LLM Provider 추가
register_provider("my_llm", MyLLMProvider)

# 새 Stage 플러그
register_stage("s99_custom", "default", MyCustomStage)

# 주변기기 연결
register_service("documents", "http://my-rag:8000")
register_tool_source(my_tool_source)
```

### Strategy Variant — 디폴트 건들지 않고 "복사해서 v2" (v0.10.4)

```python
config = HarnessConfig(
    provider="openai",
    strategy_variants={
        "s06_context": [
            {
                "name": "my_compactor_v2",
                "base": "token_budget",            # 디폴트 impl 을 복사
                "params": {"budget_ratio": 0.5},   # 파라미터만 교체
                "label": "엄격 압축",
            }
        ]
    },
    active_strategies={"s06_context": "my_compactor_v2"},
)
```

엔진이 `active_strategies[s06_context] == variant name` 이면 base impl 로 resolve 후 `params` 병합. 디폴트 구현체는 건들지 않으므로 사용자끼리 변형이 안 겹침.

### 구성 저장/로드

```python
# 빌더로 조립한 하네스 구성을 JSON 으로 보관
builder = (PipelineBuilder()
    .with_provider("openai", model="gpt-4o-mini")
    .with_rag("docs", top_k=5)
    .with_artifact("s04_tool", "lotte")
    .disable("s05_strategy"))
builder.save("./harness.json")

# 다른 프로세스에서 로드
loaded = PipelineBuilder.load("./harness.json")
pipeline = loaded.with_api_key("sk-...").build()

# HarnessConfig 도 동일한 API
config = HarnessConfig(provider="openai", capabilities=["retrieval.web_search"])
config.save("./config.json")
config = HarnessConfig.load("./config.json")
```

직렬화는 `dataclasses.fields()` 자동 순회라 새 필드 추가해도 직렬화 코드 수정 불필요. `api_key` 등 민감/런타임 객체는 자동 제외.

---

## 12 Stage 파이프라인

```
Phase A: 준비 (1회)
  s01 Input → s02 History → s03 Prompt → s04 Tool

Phase B: 에이전트 루프 (반복)
  s05 Strategy → s06 Context → s07 LLM → s08 Act → s09 Judge → s10 Decide
                                                                      ↓
                                                          계속 → s05 로 루프
                                                          완료 → Phase C

Phase C: 마무리 (1회)
  s11 Save → s12 Finalize
```

### Stage별 기능 (v0.11.0 리네이밍 반영)

| # | Stage ID | Display (KO) | 하는 일 | 설정 가능 항목 | Strategy |
|---|----------|----|--------|---------------|----------|
| 1 | **`s01_input`** | 입력 | Provider 생성, API 키 해석 | provider, model, temperature | default |
| 2 | **`s02_history`** | 이력 | 대화 이력 로드 | max_history, memory_collection | default, embedding_search |
| 3 | **`s03_prompt`** | 프롬프트 | 섹션 기반 조립 + RAG + Citation | system_prompt, citation_enabled | section_priority, simple |
| 4 | **`s04_tool`** | 도구 | MCP / Gallery / RAG 도구 수집 | mcp_sessions, rag_collections, rag_tool_mode | progressive_3level, eager_load |
| 5 | **`s05_strategy`** | 전략 | 자동/CoT/ReAct/None | planning_mode (auto/cot/react/none) | auto (complexity 연동) |
| 6 | **`s06_context`** | 컨텍스트 | RAG 검색 + 토큰 관리 | rag_collections, context_window, window_size | token_budget, sliding_window |
| 7 | **`s07_llm`** | LLM | 스트리밍 + 재시도 + 비용 추적 | max_tokens, max_retries, context_limit | retry: exponential_backoff / no_retry · parser: anthropic / openai · thinking: default / disabled |
| 8 | **`s08_act`** | 실행 | 도구 디스패치 | timeout, result_budget | sequential, **parallel** |
| 9 | **`s09_judge`** | 판정 | LLM Judge / Rule-based / None | criteria, threshold | llm_judge, rule_based, none |
| 10 | **`s10_decide`** | 결정 | Guard 체인 + 루프 판단 | max_iterations, guards, cost_budget_usd, content_blocked_patterns, content_detect_pii | **threshold** (Guard+Score+Response), **always_pass** |
| 11 | **`s11_save`** | 저장 | 실행 이력 DB 저장 | table_name, save_enabled | default, noop |
| 12 | **`s12_finalize`** | 마무리 | 메트릭스 + 포맷팅 | output_format (text/json/markdown) | default, format_json |

> ⚡ **v0.11.0 Stage ID 리네이밍** — 7개 스테이지 id 가 더 직관적인 이름으로 변경됨:
> `s02_memory → s02_history`, `s03_system_prompt → s03_prompt`, `s04_tool_index → s04_tool`,
> `s05_plan → s05_strategy`, `s08_execute → s08_act`, `s09_validate → s09_judge`, `s12_complete → s12_finalize`.
> 기존 저장된 워크플로우는 **alias 레이어** 로 계속 동작. `v0.12+` 에서 구 id 제거 예정.

---

## 서비스 연동 구조

라이브러리는 범용 이름(`documents`, `mcp`, `config`)으로 서비스를 조회하고,
어댑터가 실제 URL을 등록한다. 미등록 서비스는 graceful skip.

```python
# 어댑터 측 (XgenAdapter._register_xgen_services)
register_service("config", "http://xgen-core:8000")
register_service("documents", "http://xgen-documents:8000")
register_service("mcp", "http://xgen-mcp-station:8000")

# Stage 측 (라이브러리 내부)
url = get_service_url("documents")  # None이면 해당 기능 자동 skip
```

| 서비스 이름 | 사용 Stage | 용도 |
|------------|-----------|------|
| `config` | s01 | API 키 조회 (persistent_configs) |
| `documents` | s03, s06 | RAG 문서 검색 |
| `mcp` | s04, s08 | MCP 도구 디스커버리 + 실행 |
| (DB) | s02, s11 | 대화 이력 + 실행 로그 (ServiceProvider 주입) |

---

## RAG 연동

### 1. Pre-search (s06 컨텍스트)

사용자 입력으로 문서 검색 → 시스템 프롬프트에 자동 주입.

```python
config = HarnessConfig(
    provider="openai", model="gpt-4o-mini", preset="rag",
    stage_params={"s06_context": {"rag_collections": ["my_collection"]}}
)
```

### 2. Tool mode (에이전트 호출)

에이전트가 대화 중 필요할 때 직접 `rag_search` 도구를 호출.

```python
config = HarnessConfig(
    stage_params={
        "s04_tool": {
            "rag_collections": ["my_collection"],
            "rag_tool_mode": "tool",  # presearch / tool / both
        }
    }
)
```

### 3. Citation

```python
config = HarnessConfig(
    stage_params={
        "s03_prompt": {"citation_enabled": True}
    }
)
# → LLM 이 [DOC_1], [DOC_2] 형식으로 문서 인용
```

---

## API 키 해석 (동시성 안전)

`os.environ` 쓰기 0개. `contextvars` 기반으로 동시 실행 시 키가 섞이지 않음.

```
1. ExecutionContext (contextvars) ← 최우선
2. ServiceProvider.config.get_api_key() ← xgen-core persistent_configs
3. os.environ (읽기 전용 폴백)
```

```python
from xgen_harness.core.execution_context import set_execution_context
set_execution_context(api_key="sk-...", provider="openai", model="gpt-4o-mini")
```

---

## Preset 시스템

| Preset | 용도 | 특징 |
|--------|------|------|
| `minimal` | 단순 질의응답 | 도구/RAG/판정 없이 바로 대화 |
| `chat` | 멀티턴 대화 | 이전 대화 이력 유지 |
| `agent` | 에이전트 | 도구 + RAG + 전략 + 판정 + 루프 |
| `evaluator` | 품질 평가 | LLM Judge 엄격한 평가 |
| `rag` | 문서 검색 | 문서 기반 답변, 도구 없음 |

---

## 프로바이더

5종 빌트인 + LangChain 래핑 + 커스텀 등록.

```python
from xgen_harness.providers import register_provider, create_provider, wrap_langchain

# 빌트인: anthropic, openai, google, bedrock, vllm
provider = create_provider("openai", api_key, "gpt-4o-mini")

# LangChain 호환
from langchain_anthropic import ChatAnthropic
llm = ChatAnthropic(model="claude-sonnet-4-6")
provider = wrap_langchain(llm)

# 커스텀
register_provider("my_llm", MyProvider)
```

---

## Stage별 상세 — 설정, 연동, 확장

### s01 입력 (Input)

사용자 입력을 받아 LLM Provider를 생성하고, API 키를 해석한다.

**설정:**
```python
stage_params = {
    "s01_input": {
        "provider": "openai",          # anthropic / openai / google / bedrock / vllm
        "model": "gpt-4o-mini",        # 프로바이더별 모델
        "temperature": 0.7,            # 0.0 ~ 2.0
    }
}
```

**연동 서비스:** `config` (API 키 조회)

**API 키 해석 순서:**
1. `ExecutionContext.get_api_key()` (contextvars)
2. `ServiceProvider.config.get_api_key(provider)` (xgen-core persistent_configs)
3. `os.environ.get("OPENAI_API_KEY")` (읽기 전용 폴백)

**확장:** `register_provider("my_llm", MyProvider)` → 새 프로바이더 추가

> 💡 **v0.10.3 철학 재정립**: provider/model/temperature 하드코딩 연동 제거 — `PROVIDER_DEFAULT_MODEL` + `PROVIDER_CONTEXT_LIMITS` 레지스트리 경유로 일원화.

---

### s02 이력 (History)

이전 대화 이력을 로드하여 messages에 추가한다. `interaction_id`가 있을 때만 동작.

**설정:**
```python
stage_params = {
    "s02_history": {
        "max_history": 10,  # 최근 N개 대화만 로드 (1~20)
    }
}
```

**연동 서비스:** DB (ServiceProvider.database — 대화 이력 조회)

**bypass 조건:** `interaction_id` 없거나 이전 이력이 없으면 건너뜀

---

### s03 프롬프트 (Prompt)

시스템 프롬프트를 섹션 기반으로 조립한다. Identity → Rules → Tools → RAG → History → Citation 순서.

**설정:**
```python
stage_params = {
    "s03_prompt": {
        "system_prompt": "당신은 한국어 도우미입니다.",  # 직접 지정
        "include_rules": True,         # 기본 행동 규칙 포함
        "prompt_content": "...",       # 프롬프트 스토어에서 선택한 내용
        "citation_enabled": False,     # [DOC_1] 인용 형식 활성화
    }
}
```

**연동 서비스:** `documents` (RAG 검색 → 프롬프트에 주입)

**RAG 주입 방식:** `rag_collections` 가 metadata 에 있으면 ResourceRegistry → ServiceProvider → httpx 3단계 폴백으로 검색

**Strategy (cache):** `anthropic_cache` (기본, `cache_control: ephemeral`) / `no_cache` (명시적 끄기)

---

### s04 도구 (Tool)

MCP 세션, Gallery 패키지, 빌트인 도구를 수집하여 LLM에 전달할 도구 목록을 생성한다.

**설정:**
```python
stage_params = {
    "s04_tool": {
        "mcp_sessions": ["session-abc", "session-xyz"],  # MCP 세션 선택
        "rag_collections": ["my_docs"],    # RAG 도구로 등록할 컬렉션
        "rag_tool_mode": "both",           # presearch / tool / both
        "builtin_tools": ["discover_tools"],  # 빌트인 도구 선택
        "rag_top_k": 4,                    # RAG 검색 결과 수
    }
}
```

**연동 서비스:** `mcp` (MCP 세션 도구 디스커버리)

**RAG 도구 모드:**
- `presearch`: s06 에서 사전 검색만 (기본)
- `tool`: 에이전트가 `rag_search` 도구로 직접 호출
- `both`: 사전 검색 + 도구 모두 활성화

**Strategy (discovery):** `progressive_3level` (기본 — 메타데이터 → 스키마 캐시 → 빌트인 3단계) / `eager_load` (전체 일괄 로드)

**확장:** `register_tool_source(my_source)` → 커스텀 도구 소스 추가

---

### s05 전략 (Strategy)

실행 계획을 수립한다. 첫 번째 루프에서만 실행.

**설정:**
```python
stage_params = {
    "s05_strategy": {
        "planning_mode": "cot",  # cot (Chain-of-Thought) / react (ReAct) / none / auto
    }
}
```

**bypass 조건:** `planning_mode == "none"` 또는 루프 2회차 이상

---

### s06 컨텍스트 (Context)

RAG 문서 검색 + 토큰 예산 관리. 검색 결과를 시스템 프롬프트에 주입하고, 토큰 초과 시 메시지를 압축한다.

**설정:**
```python
stage_params = {
    "s06_context": {
        "rag_collections": ["assort_bb8b..."],  # 검색할 컬렉션
        "rag_top_k": 4,                         # 컬렉션당 검색 결과 수
        "context_window": 200000,                # 컨텍스트 윈도우 (토큰)
        "compaction_threshold": 80,              # 압축 시작 (% 사용)
    }
}
```

**연동 서비스:** `documents` (벡터 검색 API)

**Strategy (compactor):** `token_budget` (기본 — 3단계: 오래된 메시지 제거 → 시스템 프롬프트 섹션 가지치기) / `sliding_window` (최근 N개만 유지)

---

### s07 LLM 호출 (LLM)

LLM API를 호출하고 SSE로 스트리밍한다. 재시도, 비용 추적, 컨텍스트 크기 제한 포함.

**설정:**
```python
stage_params = {
    "s07_llm": {
        "max_tokens": 8192,            # 최대 출력 토큰 (256~32K)
        "max_retries": 3,              # 재시도 횟수
        "context_limit": 500000,       # 컨텍스트 크기 제한 (문자)
        "thinking_enabled": False,     # Extended Thinking 활성화
        "thinking_budget": 10000,      # Thinking 토큰 예산
    }
}
```

**컨텍스트 크기 제한:** `PROVIDER_CONTEXT_LIMITS` 레지스트리 (anthropic/openai/google: 500K, vllm: 50K). `context_limit` 으로 오버라이드 가능. 초과 시 중간 20% 자동 제거.

**재시도:** `ExponentialBackoffRetry` — RateLimitError(429) → 10/20/40초, OverloadError(529) → 1/2/4초, ServerError → 2/4/8초

**비용 추적:** `PRICING` 단일 진실 소스에서 모델별 가격 조회 — `ModelPricingCalculator` 가 cache hit 보정.

**Strategy slots:** `retry` / `parser` (anthropic / openai) / `thinking` / `token_tracker` / `cost_calculator` / `completion_detector`

---

### s08 실행 (Act)

LLM이 반환한 `tool_use`를 실제로 실행한다. 도구가 없으면 건너뜀.

**설정:**
```python
stage_params = {
    "s08_act": {
        "timeout": 60,           # 도구 실행 타임아웃 (초)
        "result_budget": 50000,  # 결과 최대 문자수
    }
}
```

**Strategy (executor):**
- `sequential` (기본): 순차 실행, 에러 허용, 예산 관리
- `parallel`: 읽기 도구 병렬 / 쓰기 도구(create/update/delete/…) 순차

**Strategy (router):** `composite` (기본 — chain + cache + fallback) / `mcp` / `builtin`

**도구 디스패치 순서:**
1. 빌트인 (`discover_tools`, `rag_search`)
2. ResourceRegistry (XgenAdapter 가 주입)
3. `register_tool_source()` 로 등록된 ToolSource
4. state.metadata 의 tool_registry (레거시 폴백)

**bypass 조건:** `pending_tool_calls` 가 비어있으면 건너뜀

---

### s09 판정 (Judge)

LLM 응답 품질을 평가한다. 텍스트 응답이 없으면 건너뜀.

**설정:**
```python
stage_params = {
    "s09_judge": {
        "criteria": ["relevance", "completeness", "accuracy", "clarity"],  # 평가 기준 선택
        "threshold": 0.7,  # 통과 기준 점수 (0.0~1.0)
    }
}
```

**Strategy:**
- `llm_judge` (기본): 별도 LLM 호출로 4가지 기준 평가, 선택된 기준만 가중평균
- `rule_based`: 길이/에러/키워드 기반 (LLM 비용 절감)
- `none`: 항상 통과

---

### s10 결정 (Decide)

루프 계속/완료를 판단한다. **Stage 내부 분기 0줄 — `DecideStrategy.decide()` 에 전적 위임 (v0.11.1).**

**설정:**
```python
stage_params = {
    "s10_decide": {
        "max_iterations": 10,
        "max_retries": 3,
        "guards": ["iteration", "cost_budget", "token_budget", "content"],
        "cost_budget_usd": 5.0,
        "token_budget": 500000,
        # ContentGuard 파라미터 (v0.11.1 실구현)
        "content_blocked_patterns": ["hack", "password\\s*:\\s*\\S+"],
        "content_detect_pii": True,
        "content_check_target": "both",  # input / output / both
    }
}
```

**Strategy (decide):**
- `threshold` (기본): Guard 체인 → pending_tool_calls → validation score → last_assistant_text 순 판단
- `always_pass`: 1회 실행 후 즉시 complete (루프 없음)

**Guard 체인 (chain — 순차 short-circuit):**
- `IterationGuard` — 최대 반복 횟수 초과 차단
- `CostBudgetGuard` — USD 비용 초과 차단
- `TokenBudgetGuard` — 토큰 예산 80% 경고 / 95% 차단
- `ContentGuard` — 정규식 금지 패턴 + 이메일/한국휴대폰/주민번호/카드번호 PII 감지 (v0.11.1 실구현, 기본값에서는 통과)

---

### s11 저장 (Save)

실행 결과를 DB에 저장한다.

**설정:**
```python
stage_params = {
    "s11_save": {
        "save_enabled": True,
        "table_name": "harness_execution_log",
    }
}
```

**연동 서비스:** DB (ServiceProvider.database)

**bypass:** `save_enabled == False` 면 건너뜀

---

### s12 마무리 (Finalize)

전체 메트릭스를 집계하고 출력을 포맷팅한다.

**설정:**
```python
stage_params = {
    "s12_finalize": {
        "output_format": "text",  # text / json / markdown
    }
}
```

**출력 포맷:**
- `text`: 그대로 출력 (기본)
- `json`: `{"content": "...", "model": "...", "tokens": {...}}` 구조화
- `markdown`: 제목 + 본문 + 모델 정보 푸터

---

## 워크플로우 컴파일러 (v0.10.0+)

하네스 워크플로우 하나를 `xgen.compile(wf)` 한 줄로 `pip install` 가능한 wheel 로 변환. 받는 쪽은 `pip install xgen-gallery-<name>` 후 `await arun("입력")` 한 줄.

```
HarnessConfig + workflow_data
         ↓
   xgen.compile(wf)
         ↓
snapshot.json + env.example + cli.py + __init__.py
         ↓
   build_wheel (python -m build)
         ↓
xgen_gallery_<name>-<ver>-py3-none-any.whl
         ↓
pip install xgen-gallery-<name>
         ↓
from xgen_gallery_<name> import arun
await arun("입력")
```

**컴파일러 설계**:
- `external_inputs` — 선언 (`HarnessConfig.external_inputs`) + `${VAR}` 자동 스캔 병행. `PROVIDER_API_KEY_MAP` 경유로 secret 타입 자동 확정.
- `DependencyResolver` + `register_dependency_rule()` — 외부 SDK 의존성 자동 wheel 주입 통로. 빌트인 5종 (xgen-harness / provider SDK / MCP / RAG / capability extras). 외부 패키지가 한 줄로 자기 의존성 선언 가능.
- `snapshot.py` — `compile_version=1.0`, JSON 직렬화 + PEP 503/440 validate.
- **3채널 배포** — 공개 PyPI / 사내 인덱스 / 로컬 wheel 모두 동일 산출물.
- **폐쇄망 완전 지원** — `pip download` 후 `pip install --no-index --find-links wheelhouse/` 동작 검증.

**산출 wheel 구조**:
- `[project.scripts]` 로 `xgen-gallery-<name>` CLI (`run` / `info` 서브커맨드) 제공
- `[project.entry-points."xgen_harness.galleries"]` 로 설치 갤러리 자동 발견
- `[mcp]` extras 로 `pip install xgen-gallery-<name>[mcp]` → `serve-mcp` 서브커맨드

**drift-free 연동 (v0.10.2)**: 엔진이 확정한 `dist_name` / `package_name` 을 `WheelBuildResult` 에 담아 이식측/프론트가 재조합하지 않도록 — 엔진 규약 변경이 자동 전파.

상세: [`docs/harness/2026-04-20-workflow-compiler.md`](../docs/harness/2026-04-20-workflow-compiler.md)

---

## DAG 오케스트레이터 (v0.10.4)

멀티에이전트 플로우를 DAG 로 표현 + fan-out / join 자동 관리. 서브 파이프라인마다 독립 `HarnessConfig` + `ExecutionContext` (API 키 격리). SSE 이벤트를 부모 스트림으로 자동 포워딩.

```python
from xgen_harness.orchestrator import DagOrchestrator, DagNode

orchestrator = DagOrchestrator()
orchestrator.add_node(DagNode(id="search", config=search_config))
orchestrator.add_node(DagNode(id="summarize", config=summarize_config, depends_on=["search"]))

async for event in orchestrator.run(initial_input="오늘 뉴스 요약"):
    yield event  # 통합 SSE 스트림
```

**Fan-out Strategy 레지스트리:** `register_fan_out_strategy()` — 분기 규칙을 외부에서 추가. 빌트인은 `broadcast` / `round_robin` / `capability_match`.

---

## 디렉토리 구조

```
xgen_harness/
├── core/                        # 핵심 엔진
│   ├── pipeline.py              # 3-Phase 실행 엔진
│   ├── stage.py                 # Stage ABC + I/O 계약
│   ├── state.py                 # PipelineState
│   ├── config.py                # HarnessConfig (22+ 필드 자동 직렬화)
│   ├── services.py              # ServiceProvider Protocol
│   ├── service_registry.py      # 서비스 URL 레지스트리 (register/get)
│   ├── execution_context.py     # contextvars 기반 API 키 격리
│   ├── strategy_resolver.py     # Strategy 레지스트리 (40+ 구현체)
│   ├── registry.py              # Stage 플러그인 (entry_points 자동 발견)
│   ├── presets.py               # 5개 Preset
│   ├── stage_config.py          # STAGE_ID_ALIASES + canonical_stage_id (v0.11.0)
│   └── artifact.py              # Artifact 시스템
│
├── stages/                      # 12 Stage 구현체
│   ├── s01_input.py ~ s12_finalize.py   # (v0.11.0 리네이밍 반영)
│   ├── interfaces.py            # Strategy ABC (DecideStrategy 포함)
│   └── strategies/              # 20+ Strategy 구현체
│       ├── retry.py             # Exponential / NoRetry
│       ├── tool_router.py       # Composite / MCP / Builtin
│       ├── tool_executor.py     # Sequential / Parallel
│       ├── evaluation.py        # LLMJudge / RuleBased / NoValidation
│       ├── discovery.py         # Progressive / Eager
│       ├── compactor.py         # TokenBudget / SlidingWindow
│       ├── guard.py             # Iteration / Cost / Token / Content (실구현)
│       ├── _decide.py           # Threshold / AlwaysPass (v0.11.1 진짜 구현)
│       ├── cache.py             # AnthropicCache / NoCache
│       ├── thinking.py          # DefaultThinking / NoThinking
│       ├── parser.py            # Anthropic / OpenAI / CompletionDetector
│       └── token_tracker.py     # TokenTracker / ModelPricing
│
├── providers/                   # LLM 프로바이더
│   ├── __init__.py              # 레지스트리 (register/create/wrap_langchain)
│   ├── base.py                  # LLMProvider ABC + ProviderEvent
│   ├── anthropic.py             # Anthropic (httpx SSE)
│   ├── openai.py                # OpenAI (httpx SSE)
│   ├── google.py / bedrock.py / vllm.py
│   └── langchain_adapter.py     # LangChain 래핑
│
├── compile/                     # 워크플로우 → wheel 컴파일러 (v0.10.0+)
│   ├── __init__.py              # xgen.compile()
│   ├── external_inputs.py       # 선언 + ${VAR} 자동 스캔
│   ├── snapshot.py              # WorkflowSnapshot
│   ├── deps.py                  # DependencyResolver + register_dependency_rule()
│   └── wheel.py                 # build_wheel (python -m build)
│
├── orchestrator/                # DAG 멀티에이전트 (v0.10.4 노출)
├── capabilities/                # Capability 선언형 시스템
├── adapters/                    # 외부 시스템 어댑터 (XgenAdapter 등)
├── tools/                       # 도구 시스템 (ToolSource / MCPClient / Gallery)
├── integrations/                # xgen 연동 (XgenServiceProvider, bridge, streaming)
├── events/                      # 이벤트 스트리밍
├── errors/                      # 에러 계층 (ErrorCategory / recoverable)
└── api/                         # FastAPI 라우터
```

---

## 외부 기여 / 확장 매뉴얼

라이브러리 소스 수정 0 — 외부 패키지 + entry_points + `register_*()` API 만으로 9개 지점 확장.

- 📌 **[docs/harness/INDEX.md](../docs/harness/INDEX.md)** — **모든 문서 단일 진입점**
- **[docs/harness/STAGE_CONTRACT.md](../docs/harness/STAGE_CONTRACT.md)** — Stage 1페이지 계약서 (가장 핵심)
- **[docs/harness/EXTENSION_POINTS.md](../docs/harness/EXTENSION_POINTS.md)** — 10개 확장 지점 전수 매뉴얼
- **[docs/harness/ANTHROPIC_OPENAI_PATTERNS.md](../docs/harness/ANTHROPIC_OPENAI_PATTERNS.md)** — Anthropic/OpenAI Tool use · Progressive Disclosure · Citations · Sandbox 패턴 ↔ 우리 구현 매핑
- **[docs/harness/TOOL_GUIDE.md](../docs/harness/TOOL_GUIDE.md)** — 도구 패키지 작성 가이드
- **`xgen-harness-stage-sample/`** — 외부 Stage 샘플 패키지 (pip install → entry_points → swap 까지 end-to-end 증명)

### 9 entry_points 그룹 (`pyproject.toml`)

```toml
[project.entry-points."xgen_harness.stages"]          # Stage 추가/swap
[project.entry-points."xgen_harness.strategies"]      # Stage 내부 알고리즘
[project.entry-points."xgen_harness.node_adapters"]   # 노드 카테고리 → tool_def
[project.entry-points."xgen_harness.option_sources"]  # UI 셀렉터 데이터
[project.entry-points."xgen_harness.tool_sources"]    # 외부 도구 디스패치
[project.entry-points."xgen_harness.providers"]       # LLM 프로바이더
[project.entry-points."xgen_harness.capabilities"]    # 선언형 도구 바인딩
[project.entry-points."xgen_harness.fan_out_strategies"]   # 멀티에이전트 분기
[project.entry-points."xgen_harness.evaluation_criteria"]  # s09 평가 기준
[project.entry-points."xgen_harness.galleries"]            # 컴파일된 갤러리 자동 발견
```

---

## 버전 이력

| 버전 | 주요 변경 |
|------|----------|
| **0.11.1** | 🧹 **Strategy 품격 — 하드코딩 제거 / 진짜 구현체**. `ThresholdDecide`/`AlwaysPassDecide` 가 이름표만 있던 마커에서 `DecideStrategy.decide()` 구현체로 승격, `s10_decide` 내부 분기 0줄로 축소. `ContentGuard` 정규식 + PII(이메일/휴대폰/주민번호/카드) 실구현. `ParallelToolExecutor` 공개 API(`__all__`) 승격. |
| **0.11.0** | ⚡ **Stage ID 리네이밍 + alias 하위호환**. s02_memory→s02_history, s03_system_prompt→s03_prompt, s04_tool_index→s04_tool, s05_plan→s05_strategy, s08_execute→s08_act, s09_validate→s09_judge, s12_complete→s12_finalize. `STAGE_ID_ALIASES` + `canonical_stage_id()` 로 구 id 저장 워크플로우도 계속 동작. |
| **0.10.4** | **Strategy Variants** — 디폴트 건들지 않고 "복사해서 v2" 로 사용자 정의 변형 노출. `HarnessConfig.strategy_variants` 신규 필드. |
| **0.10.3** | s01 하드코딩 연동 제거 — provider/model/temperature → `PROVIDER_DEFAULT_MODEL` + `PROVIDER_CONTEXT_LIMITS` 레지스트리 경유로 이관. |
| **0.10.2** | **drift-free 연동** — `WheelBuildResult.dist_name`/`package_name` 헤더 전달 → 이식측/프론트 프리픽스 재조합 제거. |
| **0.10.1** | 컴파일러 단계 5·6 — MCP stdio 서버 래퍼 (`serve-mcp` 서브커맨드), `xgen-gallery` entry_points 채널 통합. |
| **0.10.0** | **워크플로우 컴파일러 (MVP)** — `xgen.compile(wf)` 한 줄로 `pip install` 가능 wheel 생성. `external_inputs` + `DependencyResolver` + 폐쇄망 시나리오 검증. |
| **0.9.2** | Stage 책임 재정의 Phase 2 — s01 축소, `PROVIDER_CONTEXT_LIMITS` 레지스트리, anthropic 하드코딩 제거, NODE-WRAPPING 문서화. |
| **0.9.0** | Stage 책임 재정의 (철학 바로잡기) Phase 1 — 레지스트리 기반 선언 + 그래프 재배치 패턴. |
| **0.8.38** | Stage public 승격 + Verbose 4 이벤트 + Redis 우선 SSE 증명. |
| **0.8.37** | UX 개선 6트랙 (채팅 이어하기 / PD 배지 / 편집 링크 / 집계 배너 / s04 재그룹핑). |
| **0.8.36** | s06_context regression fix — `results` dict 가 search 결과 list 로 덮어씌워지던 variable shadow bug 해결. |
| **0.8.35** | 어댑터 고결성 audit fix — 9 entry_points 그룹 명시 lock-in. |
| **0.8.33** | UI 클릭 실제 동작 — 7 stage_param 누수 fix (folders/ontology/reranker 실 연동). |
| **0.8.32** | Progressive Disclosure Level 0 (`search_tools`) — Anthropic sandbox 패턴. |
| **0.8.29** | Stage 확장성 — entry_points `__` 구분자, `MultiAgentPlannerStage`, 외부 stage sample. |
| **0.8.27** | DocumentService 전면 확장 (embed/rerank/folders/ontology). |
| **0.8.26** | NodeAdapter 레지스트리 + xgen 노드 카테고리 bulk 등록. |
| **0.8.0** | Strategy 실구현, Guard 설정화, Progressive Disclosure. |
| **0.5.x** | ServiceRegistry, ExecutionContext, Plugin System. |
| **0.1.0** | 12 Stage 파이프라인 초기 구현. |

상세 변경 내역은 [CHANGELOG.md](CHANGELOG.md) 참조.

---

## Acknowledgement

설계·UI/UX 영감 및 12-stage harness 구성의 reference —
🎁 **[geny-executor](https://github.com/CocoRoF/geny-executor)** by [CocoRoF](https://github.com/CocoRoF).

본 프로젝트의 **Stage 고정 + Artifact 교체** 패턴, **DAG 오케스트레이터** 사고, **Progressive Disclosure** 환경 설계는 geny-executor 에서 많은 영감을 얻었습니다.
