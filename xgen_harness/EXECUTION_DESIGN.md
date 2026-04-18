# XGEN Harness Execution — 처음부터 끝까지

> 이 문서는 xgen-harness-executor가 "왜 만들어졌고, 어떻게 동작하며, 왜 xgen의 심장이 되어야 하는가"를
> 한 호흡으로 읽어 이해할 수 있도록 정리한 단일 진실 문서이다. 모든 결정에는 **왜**를 붙였다.
>
> 대상 독자: 김진수 (저자) 본인, 이식 작업자, 외부 기여자.
> 버전 기준: v0.8.13 (2026-04-18).

---

## 0. 한 줄 정의

> **xgen-harness execution**은 *"에이전트가 무엇을 할지를 노드로 그리는 것"*이 아니라,
> *"에이전트가 어떤 환경에서 어떤 자원을 들고 어떻게 점진적으로 정보를 탐색해 답을 만들지"*를
> **12개의 정형화된 스테이지**로 표현하는 실행 엔진이다.

캔버스에서 "노드를 잇는 것"은 사람의 멘탈모델이다. 정작 LLM은 그 그래프를 **글자(프롬프트 + 도구 카탈로그)**로
받아서 자기 머리로 푼다. 그렇다면 **사람의 그래프를 LLM이 다시 풀어 쓰게 만드는 일**은 본질적으로 낭비다.
하네스는 이 낭비를 제거하기 위해 만들어졌다.

---

## 1. 왜 새 실행기인가 — 기존 캔버스 실행의 본질적 누수

### 1.1 캔버스 실행이 가진 4가지 누수

| # | 누수 | 무슨 뜻인가 | 왜 문제인가 |
|---|---|---|---|
| 1 | **그래프 강제** | 사용자가 노드를 일일이 잇는다 | 의도가 그래프 형태로 왜곡된다. "RAG로 검색해서 답해" 한 줄이 노드 4개로 분해된다 |
| 2 | **실행 경로 고정** | 그래프대로만 흐른다 | LLM이 "지금은 RAG가 필요 없다"고 판단해도 강제 호출된다 |
| 3 | **정보 일괄 투하** | 모든 도구 스키마를 한 번에 프롬프트에 넣는다 | 토큰 폭증 + 모델 혼란 (우리가 *Progressive Disclosure*로 해결할 지점) |
| 4 | **단계 책임 모호** | 노드 안에 입력 정규화/시스템 프롬프트/도구 바인딩이 섞여 있다 | 디버깅 시 어디서 망가졌는지 추적 불가 |

### 1.2 왜 이 누수들이 "구조적"인가

캔버스의 본질은 **공간적 표현**이다. 그러나 LLM의 본질은 **순차적 텍스트 처리**다.
공간을 텍스트로 번역하는 비용은 항상 에이전트가 짊어진다. 즉 **인터프리터(LLM)가 사람의 그림을 읽고 다시 푸는** 구조다.

> Anthropic, OpenAI가 공식 가이드에서 일관되게 말하는 것:
> *"Give the model the environment, the tools, and a clear objective. Let it plan."*
>
> 환경과 도구만 주면 모델이 스스로 푼다. 우리가 그래프로 절차를 못박는 것은 모델의 능력을 깎는다.

하네스는 이 메시지를 정확히 받아들인 결과물이다. **"그래프를 그리지 말고, 환경을 선언하라."**

### 1.3 그렇다고 캔버스를 부수지는 않는다

기존 캔버스는 운영 중이고, 사용자가 익숙하다. 그래서 하네스는 **별도 실행기 (executor 2)** 로 살고,
캔버스 실행기 (executor 1) 는 그대로 둔다. 분기 지점은 **엔드포인트 1곳**뿐이다.
- 레거시: `/api/agentflow/execute/based-id/stream`
- 하네스: `/harness/execute/stream`

→ **레거시 코드에 if-else 분기를 박지 않는다.** (`controller/workflow/endpoints/execution.py`에 harness import 0건 — 직접 검증 완료)

---

## 2. 핵심 추상화 — 5개의 단어로 모든 것을 설명한다

| 추상화 | 책임 | 왜 이 추상화여야 하는가 |
|---|---|---|
| **Stage** | 입력 아티팩트 → 출력 아티팩트로 변환하는 단위 | 책임 단일화, 디버깅 추적 가능 |
| **Strategy** | 같은 Stage를 다른 방법으로 푸는 구현체 | 한 인터페이스 안에서 알고리즘을 갈아끼울 수 있어야 함 |
| **Artifact** | 스테이지 간 주고받는 표준 데이터 형식 | "다음 스테이지가 무엇을 받는다"가 코드가 아닌 스키마로 표현됨 |
| **Capability** | "할 수 있는 일"의 선언 (provider/params/factory) | "노드 잇기"가 아니라 "능력 선언"으로 의도를 표현 |
| **ServiceProvider** | 외부 자원(LLM, RAG, MCP, KV) 주입 인터페이스 | 라이브러리는 인프라를 모른다. xgen이 주입한다 |

### 2.1 왜 5개로 충분한가

이 5개로 *"무엇을(Capability) — 어디서(ServiceProvider) — 어떻게(Strategy) — 어떤 순서로(Stage) — 무엇을 주고받으며(Artifact)"*
가 모두 설명된다. 더 추가하면 추상화가 새는 것이고, 빼면 책임이 섞인다. 5개는 이 트레이드오프의 균형점이다.

---

## 3. 12 스테이지 — 왜 12개인가, 각자 왜 그 자리인가

### 3.1 전체 흐름

```
s01_input          ── 사용자 입력 정규화 + 첨부 파일 스캔
s02_memory         ── 이전 대화 / 세션 메모리 로드
s03_system_prompt  ── 시스템 프롬프트 + RAG 컨텍스트 합성
s04_tool_index     ── 도구/MCP/RAG 바인딩 + Progressive Disclosure 인덱스
s05_plan           ── (옵션) 계획 수립 + Capability 자동 발견
s06_context        ── 컨텍스트 윈도우 압축 / 트리밍
s07_llm            ── Provider 호출 (단일 호출 경계)
s08_execute        ── Tool call 실행 + 결과 반영 (루프)
s09_validate       ── 응답 검증 (Guardrail / 형식 / 안전성)
s10_decide         ── 다음 행동 결정 (계속 / 중단 / 재계획)
s11_save           ── 결과 영속화 (DB / 메모리 / 로그)
s12_complete       ── 종료 이벤트 + 메트릭 발행
```

### 3.2 왜 12개로 쪼갰는가

> "한 스테이지는 한 가지 책임만." — Single Responsibility Principle을 LLM 파이프라인에 적용한 결과.

각 스테이지는 다음 3가지 질문에 답할 수 있어야 한다:
1. **무엇을 받는가** (입력 아티팩트)
2. **무엇을 만드는가** (출력 아티팩트)
3. **언제 건너뛰는가** (`should_bypass`)

이 질문에 답할 수 없는 단위가 생기면 그건 스테이지가 아니라 "잡탕"이다. 12개는 이 기준을 만족하는 최소 분할이다.

### 3.3 각 스테이지가 왜 그 자리인가

| Stage | 자리 이유 | 핵심 제공 |
|---|---|---|
| s01_input | 정보 진입점 — 가장 먼저 정규화해야 이후 모든 단계가 일관된 입력을 본다 | `state.user_input`, `state.attached_files` |
| s02_memory | 시스템 프롬프트보다 먼저 — 메모리가 있어야 컨텍스트를 정확히 만들 수 있다 | `state.history` |
| s03_system_prompt | 도구 바인딩 직전 — 시스템 프롬프트에 RAG 컨텍스트를 합성해야 한다 | `state.system_prompt` |
| s04_tool_index | LLM 호출 전 — 도구 카탈로그를 인덱스해야 LLM이 본다 | `state.tool_definitions`, `state.tool_index` |
| s05_plan | 도구 인덱스 후 — 도구가 보여야 계획을 짤 수 있다. 자연어 → capability 매칭도 여기 | `planning_instruction`, `capability_bindings` |
| s06_context | LLM 호출 직전 — 토큰 한계 안에서 압축해야 한다 | trimmed messages |
| s07_llm | **단일 호출 경계** — Provider 호출은 정확히 여기 1곳에서만 | `state.llm_response` |
| s08_execute | LLM 응답 후 — tool call이 있으면 실행, 없으면 bypass | tool results, 다음 루프 트리거 |
| s09_validate | 출력 직전 — Guardrail/형식/PII 검증 | `state.validated_output` |
| s10_decide | validate 후 — 재실행 / 중단 / 종료 결정 | `state.next_action` |
| s11_save | decide 후 — 영속화는 의사결정 후에 | DB record |
| s12_complete | 마지막 — 메트릭 + done 이벤트 | `MetricsEvent`, `DoneEvent` |

### 3.4 왜 이 순서가 바뀔 수 없는가

각 스테이지의 **출력이 다음 스테이지의 입력이기 때문이다.** Artifact가 dependency를 강제한다. 예:
- s04는 `state.tool_definitions`를 만든다 → s05의 capability matching이 이걸 본다
- s07은 `state.llm_response`를 만든다 → s08이 이걸 파싱해서 tool call을 실행한다
- s08의 결과는 다시 s07로 루프 (tool call이 있는 한)

순서를 바꾸면 dependency가 깨진다. **이 순서는 협상 대상이 아니라 데이터 흐름의 결과**다.

---

## 4. Progressive Disclosure — 왜 이게 핵심인가

### 4.1 무엇이 문제였나

기존 방식: 100개의 도구 스키마를 모두 프롬프트에 넣는다 → 토큰 50K 소비 → 모델이 도구 선택에서 헤맨다.

### 4.2 하네스의 답

`s04_tool_index`의 default strategy `progressive_3level`가 이렇게 작동한다:

```
Level 1 (system prompt 안):
  - 도구 이름 + 1줄 설명만 (예: "rag_search: 문서 검색")
  - 토큰 비용 최소화

Level 2 (필요할 때 호출):
  - 빌트인 도구 `discover_tools(name)` 로 상세 스키마 가져오기
  - 모델이 *스스로* 필요한 도구만 선택해서 자세히 본다

Level 3 (실제 실행):
  - s08_execute에서 tool call 실행
```

근거 코드: `stages/s04_tool_index.py:4-6`, `tools/builtin.py`의 `DiscoverToolsTool`

### 4.3 왜 이 구조인가

> Anthropic의 공식 권고: *"Don't dump all tool schemas. Provide a discovery mechanism."*

- **모델 능력 보존**: 100개 스키마 중 실제 사용은 2-3개. 나머지는 노이즈
- **토큰 절약**: Level 1만 항상 노출. Level 2는 on-demand
- **확장성**: 도구가 1000개 늘어도 Level 1 비용은 선형으로 안 늘어남 (이름+1줄)

### 4.4 왜 이게 "노드 연결"보다 우월한가

노드를 일일이 연결하는 건 *"사람이 도구 선택을 미리 결정한 것"*이다.
Progressive Disclosure는 *"모델이 런타임에 도구 선택을 한다"*. **누가 도구를 더 잘 고를까?** 답은 명확하다.

---

## 5. Capability 자동 조립 — 노드 연결 ≠ 의도 표현

### 5.1 문제

사용자가 원하는 것: *"문서 검색해서 답하기"*.
캔버스로 표현하면: `Input → DocumentLoader → VectorDB → Agent → Output` (4 노드, 5 엣지).

→ **사용자의 한 줄 의도가 9개 객체로 폭증한다.**

### 5.2 하네스의 답

```python
config = HarnessConfig(
    capabilities=["rag_search"],            # 의도만 선언
    capability_params={"rag_search": {"collections": ["my_docs"], "top_k": 5}},
)
```

이걸 받은 시스템이 자동으로:
1. `CapabilityRegistry`에서 `rag_search` spec 조회
2. `materializer`가 spec의 factory를 호출해 Tool 인스턴스 생성
3. `state.tool_definitions`에 자동 등록 → s04에서 인덱스 → 모델이 사용

근거 코드: `capabilities/{schema, registry, matcher, materializer}.py`, `stages/s04_tool_index.py:103 _bind_capabilities()`

### 5.3 3가지 바인딩 경로 (왜 3개인가)

| 경로 | 트리거 | 사용 시나리오 |
|---|---|---|
| **선언 바인딩** | `config.capabilities = [...]` | 명확한 요구사항 (UI에서 체크박스 선택) |
| **자연어 발견** | `s05_plan`의 `mode=capability` 또는 `capability_discovery=true` | 사용자 입력에서 의도 자동 추출 |
| **자동 발행** | xgen 어댑터의 `ResourceRegistry.publish_capabilities()` | 워크플로우에 등록된 리소스를 시스템이 자동 인지 |

→ **3개 경로는 "사용자 명시성 정도"의 스펙트럼이다.** 사용자가 명확할수록 1번, 모호할수록 2/3번.

근거 코드: `s05_plan.py:106 _discover_and_bind_capabilities()`, `adapters/resource_registry.py`

### 5.4 왜 자연어 발견이 가능한가

`CapabilityMatcher`가 3단계 fallback으로 매칭한다:

```
1. exact_tag — 이름/태그/alias 정확 일치 (score 0.7~1.0)
2. keyword   — 토큰 부분 일치, 한국어 조사 대응 (score 0.3~1.0)
3. llm       — LLM judge (선택, llm_fn 주입 시)
```

근거 코드: `capabilities/matcher.py:67 match()`, `:93 amatch()`

→ **라이브러리는 LLM을 직접 호출하지 않는다.** llm_fn을 외부에서 주입한다. 왜? 라이브러리가 인프라(API 키, provider 선택)를 가지면 그건 라이브러리가 아니라 프레임워크다. 우리는 라이브러리를 유지한다.

---

## 6. xgen 통합 — 어떻게 끼워 넣는가 (레거시 무침범 검증 완료)

### 6.1 침투 지점 — 단 3줄

| 파일 | 변경 | 이유 |
|---|---|---|
| `controller/workflow/workflowController.py` | 2줄 (import + include_router) | FastAPI 라우터 등록 |
| `controller/workflow/models/requests.py` | 1줄 (`harness_config: Optional[Dict]`) | 워크플로우 저장 시 harness_config 동거 |

### 6.2 침투 0건 영역 (직접 grep 검증)

- `editor/` 전체 — AsyncWorkflowExecutor, 노드 시스템
- `service/` 전체 — DB, 세션, 메타
- `controller/workflow/endpoints/execution.py` — 749줄 레거시 실행 라우터
- `controller/workflow/utils/execution_core.py` — 스케줄러 공유 코어 (하네스와 무관)

→ **"executor 1을 절대 안 건드린다"는 약속을 지켰다.** 코드가 증명.

### 6.3 분기 모델

```
사용자 요청
   │
   ├── /api/agentflow/execute/based-id/stream   → 레거시 (캔버스 그대로)
   │   └── execution.py → AsyncWorkflowExecutor → 노드별 처리
   │
   └── /harness/execute/stream                  → 하네스 (별도 라우터)
       └── harness.py → XgenAdapter → Pipeline (12 stages)
```

→ **분기는 URL 경로에서 일어난다.** 코드 분기 0개.

### 6.4 XgenAdapter의 역할

`adapters/xgen.py`의 `XgenAdapter`가 다음을 담당:
- `workflow_data` (DB 저장 형식) → `HarnessConfig` 변환
- xgen의 `ServiceProvider` 주입 (Anthropic/OpenAI/Google API 키, RAG, MCP)
- `ResourceRegistry`를 통해 워크플로우 자원을 capability로 자동 발행
- Pipeline 실행 후 SSE 이벤트 변환 yield

→ 어댑터 레이어가 **"라이브러리(인프라 무지)"와 "xgen(인프라 보유)"을 분리하는 막**이다.

---

## 7. DAG 멀티 에이전트 — 왜 단일 에이전트로는 안 되는가

### 7.1 문제

복잡한 워크플로우는 *"검색 에이전트 → 분석 에이전트 → 보고서 에이전트"* 같은 다단계 협업이 필요하다.

### 7.2 답: `MultiAgentExecutor` + `DAGOrchestrator`

근거 코드: `orchestrator/multi_agent.py:27`, `orchestrator/dag.py:90`, `api/router.py:218`

```
워크플로우 데이터 (nodes, edges)
   │
   ▼
MultiAgentExecutor._build_dag()
   ├── agents/* 노드 → AgentNode
   ├── document_loaders/* → 부모 에이전트의 RAG capability로 흡수
   ├── mcp/* → 부모 에이전트의 MCP capability로 흡수
   └── edges → DAGEdge
   │
   ▼
DAGOrchestrator
   ├── 토폴로지 정렬
   ├── 레벨별 병렬 실행 (asyncio.gather)
   └── 결과 전달 (선행 에이전트의 출력 → 후행의 입력)
```

### 7.3 왜 DAG인가

- **병렬성**: 의존성 없는 에이전트는 동시 실행 가능
- **추적성**: 각 노드별 입출력이 분리되어 디버깅 용이
- **재실행성**: 실패한 노드만 재시도 가능

→ 사용자 노트에 명시된 *"=> DAG 따라가는 오케스트레이터 있어야 할 것 같음"* 요구를 정확히 충족.

---

## 8. Gallery — 외부 기여자 생태계

### 8.1 문제

도구는 무한히 늘어난다. 모두를 라이브러리에 박을 수는 없다.

### 8.2 답: 도구 패키지 표준 + 자동 발견

`tools/gallery.py`의 `ToolPackageSpec`이 정의하는 표준:

```python
# 외부 패키지가 제공해야 할 형식
TOOL_DEFINITIONS = [
    {"name": "...", "description": "...", "input_schema": {...}},
    ...
]

def call_tool(name: str, args: dict) -> dict:
    ...
```

- 패키지가 `entry_points`에 `xgen_harness.tools`로 등록 → 자동 발견
- `discover_gallery_tools()`가 모든 등록 패키지를 로드

### 8.3 갤러리 노티스 (외부 기여자에게 보낼 메시지)

```
[xgen-gallery 도구 등록 형식]
1. 패키지에 TOOL_DEFINITIONS 와 call_tool 을 정의하세요
2. setup.py / pyproject.toml 에 entry_point 등록:
     xgen_harness.tools = your_package = your_package.tools:spec
3. xgen-gallery에 PR 또는 PyPI 배포 후 등록 신청
```

→ **이게 "살을 얹을 수 있는 구조"의 구체적 형태**다.

---

## 9. 실행 흐름 — 사용자 입력부터 응답까지 한 번 따라가기

```
1. 사용자가 /harness 페이지에서 입력 "내 문서에서 출장 정책 찾아줘"
2. POST /harness/execute/stream {workflow_id, input_data, harness_config}
3. harness.py → _load_harness_workflow → workflow_data 로드
4. XgenAdapter.execute() 시작
   ├── HarnessConfig 변환
   ├── ServiceProvider 주입 (xgen-core /api/data/config 에서 API 키 조회)
   ├── ResourceRegistry → capabilities 자동 발행
   └── Pipeline.run(state)
        ├── s01: input 정규화 → "출장 정책 찾아줘"
        ├── s02: 이전 대화 로드 (interaction_id 기준)
        ├── s03: 시스템 프롬프트 합성
        ├── s04: rag_search capability 바인딩 (선언) + Progressive index
        ├── s05: planning_mode=auto → "moderate" → CoT 지시 주입
        ├── s06: context 트리밍
        ├── s07: Anthropic claude-sonnet-4 호출
        │     → 모델: "rag_search 도구를 호출해야겠다"
        ├── s08: rag_search 실행 → 5개 청크 결과
        │     → 다시 s07으로 루프
        ├── s07: LLM 재호출 (도구 결과 포함)
        │     → 최종 답변 생성
        ├── s09: validate (PII / 형식)
        ├── s10: decide → done
        ├── s11: execution_io 저장 + harness_execution_log 저장
        └── s12: MetricsEvent + DoneEvent yield
5. 각 스테이지의 이벤트가 SSE로 실시간 스트리밍
6. _harness_stream() finally 블록에서 harness_execution_log 영속화
```

→ **이 한 흐름을 끝까지 따라갈 수 있다는 것 자체**가 "디버깅 가능한 시스템"의 정의다.

---

## 10. 이식 방법 — 단계별 (이미 진행 중)

### 10.1 이미 끝난 것 (v0.8.13 기준)

- [x] xgen-harness-executor PyPI 배포
- [x] xgen-workflow에 harness.py 라우터 추가 (3줄 침투만)
- [x] XgenAdapter 구현 (workflow_data → HarnessConfig 변환)
- [x] xgen-frontend에 `/harness` 페이지 + canvas-harness 컴포넌트
- [x] Capability 시스템 Phase 1~11 완료
- [x] Multi-agent + DAG orchestrator
- [x] 12 스테이지 + 40+ Strategy 구현

### 10.2 남은 이식 작업

| Phase | 작업 | 왜 |
|---|---|---|
| A | s05_plan에 LLM judge llm_fn 주입 (xgen 어댑터에서) | capability LLM 단계 활성화 |
| B | Redis 우선 조회 흐름 통일 (현재 ServiceRegistry 경유) | 정책 vs 실제 일치 |
| C | xgen-gallery 노티스 페이지 (외부 기여자용) | 생태계 확장 |
| D | 캔버스 점진적 비활성화 (메뉴에서 숨김 → 비활성화 → 제거) | 중복 제거 |
| E | harness_execution_log → 통합 대시보드 | 운영 가시성 |

각 Phase는 독립적이며, 어떤 것도 레거시를 건드리지 않는다.

---

## 11. 효과 — 무엇이 좋아지는가

### 11.1 사용자 측면

| 항목 | 캔버스 | 하네스 |
|---|---|---|
| 단일 에이전트 만들기 | 노드 4개 + 엣지 5개 | 체크박스 3개 |
| 학습 곡선 | "노드 시스템 이해" 필요 | 12 스테이지 의미만 알면 됨 |
| 실패 디버깅 | "어느 노드?" 추적 | "어느 스테이지?" 즉시 보임 |
| 재사용 | 워크플로우 복제 | 프리셋 + 커스텀 stage_params |

### 11.2 개발자 측면

| 항목 | 캔버스 | 하네스 |
|---|---|---|
| 새 도구 추가 | 노드 클래스 + UI 컴포넌트 + 백엔드 처리 | Capability spec 등록 (한 파일) |
| 새 알고리즘 추가 | 새 노드 타입 추가 | 같은 Stage에 새 Strategy 등록 |
| 외부 기여 | 코어 PR 필요 | entry_points + PyPI |
| 테스트 | 워크플로우 JSON 빌드 필요 | `Pipeline.run(state)` 직접 호출 |

### 11.3 운영 측면

- **이벤트 표준화**: StageEnter/Exit, ToolCall/Result, Metrics, Done — 동일 포맷 SSE
- **메트릭 표준화**: duration_ms, cost_usd, tokens — `MetricsEvent`로 통일
- **취소 표준화**: `executor.cancel()` 한 메서드로 모든 스테이지 중단

→ **운영자가 캔버스 노드별 로그를 추적하지 않아도 된다.** 스테이지 ID로 모든 게 추적된다.

---

## 12. 왜 "Harness Execution"이라는 이름인가

> Harness = 마구. 말의 힘을 정확히 마차로 전달하는 장치.

LLM은 강력한 말이다. 캔버스는 "말에게 길을 일일이 그려준 것"이다. 하네스는 "말에게 환경과 목적지만 주고
힘을 잘 전달받는 마구"다. **에이전트의 능력을 깎지 않으면서 통제 가능하게 만드는 것 — 그게 하네스다.**

---

## 13. 결론 — 왜 이게 xgen의 심장인가

1. **레거시를 해치지 않으면서** 새 실행 패러다임을 도입한다 → 안전한 전환
2. **정형화된 12 스테이지**로 일관성과 디버깅 가능성을 확보한다 → 안정성
3. **Strategy / Capability / Gallery**로 외부 기여자가 살을 얹을 수 있다 → 확장성
4. **Progressive Disclosure**로 LLM 능력을 깎지 않는다 → 모델 시대의 정답
5. **DAG + Multi-Agent**로 복잡한 협업을 구조적으로 표현한다 → 미래 대비

> **"환경만 깔아주면 모델이 푼다"는 Anthropic·OpenAI 공통 메시지를 xgen에 녹여낸 단 하나의 결과물.**

이 문서를 다 읽었다면, 이제 `REFACTORING_PLAN.md`로 넘어가서 *"엔드포인트와 스테이지를 안 해치고 더 다듬는 법"*을 본다.
