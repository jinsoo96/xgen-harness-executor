# xgen-harness

[![PyPI](https://img.shields.io/pypi/v/xgen-harness?color=blue&label=PyPI)](https://pypi.org/project/xgen-harness/)
[![Python](https://img.shields.io/pypi/pyversions/xgen-harness)](https://pypi.org/project/xgen-harness/)
[![License](https://img.shields.io/pypi/l/xgen-harness)](https://pypi.org/project/xgen-harness/)

```bash
pip install xgen-harness
```

12단계 파이프라인 기반 에이전트 실행 엔진.
라이브러리 자체는 특정 인프라에 의존하지 않으며, 어댑터가 외부 서비스를 끼워넣는 구조.

---

## 핵심 개념

```
라이브러리 (xgen-harness)          어댑터 (실행기가 만듦)
┌─────────────────────────┐    ┌───────────────────────────┐
│ 12 Stage Pipeline       │    │ XgenAdapter               │
│ Strategy × Stage        │    │  register_service(...)    │
│ get_service_url(name)   │◄───│  set_execution_context()  │
│ → None이면 skip         │    │  xgen 인프라 등록          │
└─────────────────────────┘    └───────────────────────────┘
```

- **라이브러리**는 서비스 URL, API 키, 프로바이더를 모른다
- **어댑터**가 `register_service()`, `set_execution_context()`로 끼워넣는다
- 미등록 서비스는 에러가 아니라 해당 기능을 건너뜀 (graceful skip)

---

## 빠른 시작

### 독립 실행 (어댑터 없이)

```python
from xgen_harness import Pipeline, PipelineState, HarnessConfig, EventEmitter
from xgen_harness.core.execution_context import set_execution_context

# API 키 주입 (contextvars 기반, 동시성 안전)
set_execution_context(api_key="sk-...", provider="openai", model="gpt-4o-mini")

config = HarnessConfig(provider="openai", model="gpt-4o-mini", preset="minimal")
pipeline = Pipeline.from_config(config, EventEmitter())
state = PipelineState(user_input="안녕하세요")

await pipeline.run(state)
print(state.final_output)
```

### xgen-workflow 연동

```python
from xgen_harness.adapters.xgen import XgenAdapter

adapter = XgenAdapter(db_manager=db_manager)
async for event in adapter.execute(workflow_data, input_data, user_id=user_id):
    yield event  # xgen SSE 포맷
```

---

## 12 Stage 파이프라인

```
Phase A: 준비 (1회)
  s01 입력 → s02 기억 → s03 시스템 프롬프트 → s04 도구 색인

Phase B: 에이전트 루프 (반복)
  s05 계획 → s06 컨텍스트 → s07 LLM → s08 도구 실행 → s09 검증 → s10 판단
                                                                    ↓
                                                        계속 → s05로 루프
                                                        완료 → Phase C

Phase C: 마무리 (1회)
  s11 저장 → s12 완료
```

### Stage별 기능

| # | Stage | 하는 일 | 설정 가능 항목 | Strategy |
|---|-------|--------|---------------|----------|
| 1 | **입력** | Provider 생성, API 키 해석 | provider, model, temperature | default, **with_classification** |
| 2 | **기억** | 대화 이력 로드 | max_history, memory_collection | default, **embedding_search** |
| 3 | **시스템 프롬프트** | 섹션 기반 조립 + RAG + Citation | system_prompt, citation_enabled | section_priority, simple |
| 4 | **도구 색인** | MCP/Gallery/RAG 도구 수집 | mcp_sessions, rag_collections, rag_tool_mode | progressive_3level, eager_load |
| 5 | **계획** | 자동/CoT/ReAct/None | planning_mode (**auto**/cot/react/none) | auto (complexity 연동) |
| 6 | **컨텍스트** | RAG 검색 + 토큰 관리 | rag_collections, context_window, window_size | token_budget, **sliding_window** |
| 7 | **LLM 호출** | 스트리밍 + 재시도 + 비용 추적 | max_tokens, max_retries, context_limit | streaming, batch |
| 8 | **도구 실행** | MCP/ToolSource/Registry 디스패치 | timeout, result_budget | default(순차), **parallel_read** |
| 9 | **검증** | LLM Judge / Rule-based / None | **criteria** (선택), threshold | llm_judge, rule_based, none |
| 10 | **판단** | Guard 체인 + 루프 판단 | max_iterations, **guards**, cost_budget_usd | threshold, always_pass |
| 11 | **저장** | 실행 이력 DB 저장 | table_name, save_enabled | default, noop |
| 12 | **완료** | 메트릭스 + 포맷팅 | output_format (text/json/markdown) | default, **format_json** |

---

## 확장 포인트 (코드 수정 없이)

```python
# LLM 프로바이더 추가
from xgen_harness import register_provider
register_provider("my_llm", MyLLMProvider)

# Strategy 교체
from xgen_harness.core.strategy_resolver import register_strategy
register_strategy("s09_validate", "evaluation", "strict", StrictJudge)

# Stage 플러그인 (entry_points 자동 발견도 지원)
from xgen_harness import register_stage
register_stage("s99_custom", "default", MyCustomStage)

# Tool 소스 추가
from xgen_harness import register_tool_source
register_tool_source(my_tool_source)  # ToolSource Protocol 구현

# 서비스 엔드포인트 등록 (어댑터에서 호출)
from xgen_harness import register_service
register_service("documents", "http://my-rag:8000")
register_service("mcp", "http://my-tools:8000")

# Preset 추가
from xgen_harness.core.presets import PRESETS
PRESETS["enterprise"] = {"disabled_stages": [...], "temperature": 0.2}
```

---

## 서비스 연동 구조

라이브러리는 범용 이름(`documents`, `mcp`, `config`)으로 서비스를 조회하고,
어댑터가 실제 URL을 등록한다.

```python
# 어댑터 측 (XgenAdapter._register_xgen_services)
register_service("config", "http://xgen-core:8000")
register_service("documents", "http://xgen-documents:8000")
register_service("mcp", "http://xgen-mcp-station:8000")

# Stage 측 (라이브러리 내부)
url = get_service_url("documents")  # 등록된 URL 반환, 미등록이면 None
if not url:
    logger.info("documents 미등록, RAG 건너뜀")
    return
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
        "s04_tool_index": {
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
        "s03_system_prompt": {"citation_enabled": True}
    }
)
# → LLM이 [DOC_1], [DOC_2] 형식으로 문서 인용
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
| `minimal` | 단순 질의응답 | 도구/RAG/검증 없이 바로 대화 |
| `chat` | 멀티턴 대화 | 이전 대화 이력 유지 |
| `agent` | 에이전트 | 도구 + RAG + 계획 + 검증 + 루프 |
| `evaluator` | 품질 검증 | LLM Judge 엄격한 평가 |
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

---

### s02 기억 (Memory)

이전 대화 이력을 로드하여 messages에 추가한다. `interaction_id`가 있을 때만 동작.

**설정:**
```python
stage_params = {
    "s02_memory": {
        "max_history": 10,  # 최근 N개 대화만 로드 (1~20)
    }
}
```

**연동 서비스:** DB (ServiceProvider.database — 대화 이력 조회)

**bypass 조건:** `interaction_id` 없거나 이전 이력이 없으면 건너뜀

---

### s03 시스템 프롬프트 (System Prompt)

시스템 프롬프트를 섹션 기반으로 조립한다. Identity → Rules → Tools → RAG → History → Citation 순서.

**설정:**
```python
stage_params = {
    "s03_system_prompt": {
        "system_prompt": "당신은 한국어 도우미입니다.",  # 직접 지정
        "include_rules": True,         # 기본 행동 규칙 포함
        "prompt_content": "...",       # 프롬프트 스토어에서 선택한 내용
        "citation_enabled": False,     # [DOC_1] 인용 형식 활성화
    }
}
```

**연동 서비스:** `documents` (RAG 검색 → 프롬프트에 주입)

**RAG 주입 방식:** `rag_collections`가 metadata에 있으면 ResourceRegistry → ServiceProvider → httpx 3단계 폴백으로 검색

---

### s04 도구 색인 (Tool Index)

MCP 세션, Gallery 패키지, 빌트인 도구를 수집하여 LLM에 전달할 도구 목록을 생성한다.

**설정:**
```python
stage_params = {
    "s04_tool_index": {
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
- `presearch`: s06에서 사전 검색만 (기본)
- `tool`: 에이전트가 `rag_search` 도구로 직접 호출
- `both`: 사전 검색 + 도구 모두 활성화

**확장:** `register_tool_source(my_source)` → 커스텀 도구 소스 추가

---

### s05 계획 (Plan)

실행 계획을 수립한다. 첫 번째 루프에서만 실행.

**설정:**
```python
stage_params = {
    "s05_plan": {
        "planning_mode": "cot",  # cot (Chain-of-Thought) / react (ReAct) / none
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

**압축 전략:** 예산 초과 시 첫 메시지 + 최근 3개만 유지

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

**컨텍스트 크기 제한:** Provider별 기본값 (anthropic/openai/google: 500K, vllm: 50K). `context_limit`으로 오버라이드 가능. 초과 시 중간 20% 자동 제거.

**재시도:** RateLimitError(429) → 10/20/40초, OverloadError(529) → 1/2/4초

**비용 추적:** `PRICING` 단일 진실 소스에서 모델별 가격 조회

---

### s08 도구 실행 (Execute)

LLM이 반환한 `tool_use`를 실제로 실행한다. 도구가 없으면 건너뜀.

**설정:**
```python
stage_params = {
    "s08_execute": {
        "timeout": 60,           # 도구 실행 타임아웃 (초)
        "result_budget": 50000,  # 결과 최대 문자수
    }
}
```

**도구 디스패치 순서:**
1. 빌트인 (`discover_tools`, `rag_search`)
2. ResourceRegistry (XgenAdapter가 주입)
3. `register_tool_source()`로 등록된 ToolSource
4. state.metadata의 tool_registry (레거시 폴백)

**bypass 조건:** `pending_tool_calls`가 비어있으면 건너뜀

---

### s09 검증 (Validate)

LLM 응답 품질을 평가한다. 텍스트 응답이 없으면 건너뜀.

**설정:**
```python
stage_params = {
    "s09_validate": {
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

### s10 판단 (Decide)

계속/종료를 판단한다. Guard 체인으로 예산 초과를 감지.

**설정:**
```python
stage_params = {
    "s10_decide": {
        "max_iterations": 10,  # 최대 루프 횟수
        "max_retries": 3,      # 검증 실패 시 재시도 횟수
    }
}
```

**판단 로직:**
1. Guard 체인 차단 (반복/비용/토큰 예산 초과) → `complete`
2. `pending_tool_calls` 있음 → `continue` (도구 실행 후 재시도)
3. 검증 점수 미달 + 재시도 가능 → `retry`
4. 텍스트 응답 있음 → `complete`

---

### s11 저장 (Save)

실행 결과를 DB에 저장한다.

**설정:**
```python
stage_params = {
    "s11_save": {
        "save_enabled": True,                    # 저장 활성화
        "table_name": "harness_execution_log",   # 테이블명
    }
}
```

**연동 서비스:** DB (ServiceProvider.database)

**bypass:** `save_enabled == False`이면 건너뜀

---

### s12 완료 (Complete)

전체 메트릭스를 집계하고 출력을 포맷팅한다.

**설정:**
```python
stage_params = {
    "s12_complete": {
        "output_format": "text",  # text / json / markdown
    }
}
```

**출력 포맷:**
- `text`: 그대로 출력 (기본)
- `json`: `{"content": "...", "model": "...", "tokens": {...}}` 구조화
- `markdown`: 제목 + 본문 + 모델 정보 푸터

---

## 디렉토리 구조

```
xgen_harness/
├── core/                        # 핵심 엔진
│   ├── pipeline.py              # 3-Phase 실행 엔진
│   ├── stage.py                 # Stage ABC + I/O 계약
│   ├── state.py                 # PipelineState
│   ├── config.py                # HarnessConfig
│   ├── services.py              # ServiceProvider Protocol
│   ├── service_registry.py      # 서비스 URL 레지스트리 (register/get)
│   ├── execution_context.py     # contextvars 기반 API 키 격리
│   ├── strategy_resolver.py     # Strategy 레지스트리
│   ├── registry.py              # Stage 플러그인 (entry_points 자동 발견)
│   ├── presets.py               # 5개 Preset
│   └── artifact.py              # Artifact 시스템
│
├── stages/                      # 12 Stage 구현체
│   ├── s01_input.py ~ s12_complete.py
│   ├── interfaces.py            # Strategy ABC
│   └── strategies/              # Strategy 구현체
│
├── providers/                   # LLM 프로바이더
│   ├── __init__.py              # 레지스트리 (register/create/wrap_langchain)
│   ├── base.py                  # LLMProvider ABC + ProviderEvent
│   ├── anthropic.py             # Anthropic (httpx SSE)
│   ├── openai.py                # OpenAI (httpx SSE)
│   └── langchain_adapter.py     # LangChain 래핑
│
├── adapters/                    # 외부 시스템 어댑터
│   ├── xgen.py                  # XgenAdapter (xgen-workflow 전용)
│   └── resource_registry.py     # 리소스 통합 레지스트리
│
├── tools/                       # 도구 시스템
│   ├── __init__.py              # ToolSource Protocol + 등록
│   ├── base.py                  # Tool ABC + ToolResult
│   ├── builtin.py               # discover_tools
│   ├── rag_tool.py              # RAG 검색 도구 (에이전트 호출)
│   ├── mcp_client.py            # MCP 서버 통신
│   └── gallery.py               # Gallery Tool 표준
│
├── integrations/                # xgen 연동 (어댑터 레이어)
│   ├── xgen_services.py         # XgenServiceProvider
│   ├── workflow_bridge.py       # Pipeline 실행 브릿지
│   └── xgen_streaming.py        # 이벤트 → SSE 변환
│
├── events/                      # 이벤트 스트리밍
├── errors/                      # 에러 계층
├── orchestrator/                # DAG 멀티에이전트
└── api/                         # FastAPI 라우터
```

---

## 버전 이력

| 버전 | 주요 변경 |
|------|----------|
| 0.8.0 | Strategy 실구현 (with_classification/embedding_search/sliding_window/parallel_read), Guard 설정화, Progressive Disclosure |
| 0.7.0 | RAG Tool Mode, 컨텍스트 크기 제한, Citation |
| 0.6.0 | 9개 파라미터 실연동, Strategy 구현 |
| 0.5.x | ServiceRegistry, ExecutionContext, Plugin System |
| 0.4.0 | ResourceRegistry, XgenAdapter |
| 0.3.0 | Provider Registry, Gallery Tools |
| 0.2.0 | ServiceProvider, workflow_bridge |
| 0.1.0 | 12 Stage 파이프라인 초기 구현 |
