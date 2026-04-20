# xgen-harness-executor — 설계 철학 (Philosophy)

**문서 지위**: 🚨 **Stage/Strategy 를 추가·수정·리뷰하기 전에 읽어야 하는 단일 기준**. 이 문서의 "담당/비담당" 선언과 어긋나는 PR 은 거절 대상.

**왜 이 문서가 존재하는가**
- 과거에 각 Stage 의 책임 경계가 선언돼 있지 않아 "아무 Stage 에나 밀어넣는" 오염이 누적됐다 (예: `s01_input` 이 LLM provider 생성까지 떠안음, `s03_system_prompt` 가 RAG 검색을 직접 호출).
- 개발자 / 플러그인 기여자가 **"내 기능은 어느 Stage 에 얹어야 하는가"** 를 판단할 나래비(기준선)가 필요.
- 이 문서는 각 Stage 의 **담당 / 비담당 / 의심되면 어느 Stage 로 보낼지** 를 선언함.

---

## 1. 상위 원칙 (4축)

| 원칙 | 내용 |
|---|---|
| **1. 한 Stage = 한 책임** | Single Responsibility at Stage level. "입력" 과 "LLM 호출" 은 다른 책임 → 다른 Stage. |
| **2. 책임 침범 금지** | 다른 Stage 가 이미 하고 있는 일을 나도 한 번 더 하지 않는다. RAG 검색은 s06 한 곳. provider 생성은 s07 한 곳. |
| **3. 데이터 전달은 Artifact 로만** | Stage 간 통신은 `PipelineState` 의 선언된 필드(`state.rag_context` / `state.tool_definitions` / `state.pending_tool_calls` 등) 를 통해. 전역 변수 / 모듈 싱글턴 / 외부 store 금지. |
| **4. 분기는 Strategy 로만** | Stage 내부 if/elif 분기 대신 `strategy_resolver` 에 등록된 Strategy 선택. 새 동작 = 새 Strategy 등록, Stage 코드 수정 0. |

> 이 4 원칙이 `STAGE_CONTRACT.md` 에도 Contract 로 박혀 있어야 하며, 본 문서와 교차 참조한다.

---

## 2. 12 Stage 책임표

각 Stage 는 **"한 줄 정의 / 담당 / 비담당 / 의심되면 여기로"** 네 항목으로 선언된다.

### s01_input — 사용자 입력 정규화

- **한 줄 정의**: 사용자가 보낸 텍스트·파일·interaction_id 를 파이프라인이 읽을 수 있는 형태(message)로 변환한다.
- **담당**
  - 텍스트/파일 입력 검증 (빈 입력 거부)
  - 첨부 파일 → content block 변환 (base64 이미지, 텍스트 첨부)
  - `state.messages` 에 첫 user 메시지 push
  - 선택적 입력 복잡도 분류 (`with_classification` Strategy)
- **비담당 (v0.9.0+)**
  - ❌ LLM provider 생성 — **s07_llm** 으로
  - ❌ API key / base_url 해석 — **s07_llm** 으로
  - ❌ temperature / max_tokens 적용 — **s07_llm** 으로
  - ❌ MCP 도구 디스커버리 — **s04_tool_index** 로 (s04 가 MCP sessions 선택/등록 전담)
- **의심되면 여기로**: (없음 — 이 Stage 는 축소 유지)

### s02_memory — 과거 대화/실행 이력 로드

- **한 줄 정의**: 과거 턴의 메시지/실행 결과를 `state.previous_results` 에 싣는다.
- **담당**: interaction_id 기반 history 조회, execution_log / chat_history / documents 소스 전환
- **비담당**
  - ❌ 실시간 RAG 검색 — **s06_context** 로 (s02 의 documents 소스는 "과거 실행 결과물 조회" 로 제한)
- **의심되면 여기로**: "실행 시점에 생기는 정보" 면 여기 아님, s07 이후 Stage

### s03_system_prompt — 시스템 프롬프트 조립

- **한 줄 정의**: 여러 섹션(identity / rules / tool index / rag / history)을 우선순위 순으로 합쳐 `state.system_prompt` 문자열을 만든다.
- **담당**
  - 섹션 템플릿(identity, rules, citation instructions)
  - Tool index 섹션 (Progressive Disclosure Level 1 메타데이터)
  - `state.rag_context` 가 **이미 있으면** rag 섹션 포함
  - 섹션 우선순위 정렬 + `state.system_prompt` 쓰기
- **비담당 (v0.9.0+)**
  - ❌ RAG 검색 실행 — **s06_context** 로 (s03 는 state.rag_context 를 **읽기만** 한다)
  - ❌ Documents API 호출 — **s06_context** 로
- **의심되면 여기로**: 프롬프트 **텍스트 조립** 이면 여기, **데이터 수집** 이면 s02 / s06

### s04_tool_index — 도구 카탈로그 + declared capability 바인딩

- **한 줄 정의**: LLM 이 볼 수 있는 도구 목록(`state.tool_definitions`, `state.tool_index`)을 확정한다.
- **담당**
  - MCP sessions / custom API tools / CLI skills / node tags 수집 및 등록
  - Progressive Disclosure (선택 수 ≥ threshold 시 search_tools 빌트인 주입)
  - **Declared** capability 바인딩 (`config.capabilities` 에 사용자가 명시한 것)
  - 도구 스키마 검증 (input_schema 누락 등)
- **비담당 (v0.9.0+)**
  - ❌ **Discovery** capability 바인딩 — **s05_plan** 이 자연어 의도로 추론
  - ❌ RAG 검색 실행 — **s06_context** 로 (s04 는 rag_collections "선택 목록" 만 metadata 에 저장)
  - ❌ LLM provider 초기화 — s07
- **의심되면 여기로**: "LLM 에게 노출될 도구 카탈로그" 면 여기

### s05_plan — 계획 수립 + discovery capability

- **한 줄 정의**: LLM 에게 넘길 planning_instruction 을 만들고, 자연어 의도 기반으로 capability 를 추가 발견한다.
- **담당**
  - planning_mode 전환 (auto / cot / react / capability / none)
  - Capability **discovery** — 사용자 자연어에서 의도 추출 → capability registry 검색 → top_k 바인딩
- **비담당**
  - ❌ 이미 declared 된 capability 재처리 — s04 가 이미 했음
  - ❌ 도구 실행 — s08
- **의심되면 여기로**: 실행 전 "어떻게 접근할지" 결정이면 여기

### s06_context — 실시간 컨텍스트 수집 + 토큰 윈도우 관리

- **한 줄 정의**: 지금 이 턴의 질문에 필요한 외부 데이터(RAG / DB / Ontology)를 검색해 `state.system_prompt` / `state.rag_context` 에 주입하고, 전체 컨텍스트가 토큰 예산을 넘으면 압축한다.
- **담당**
  - **RAG 검색 실행** (DocumentService.search 위임 → httpx 폴백)
  - folders → collections 확장
  - ontology / GraphRAG 쿼리
  - DB 스키마 요약 (DatabaseService.get_schema_summary)
  - Reranker 재정렬 (DocumentService.rerank)
  - 토큰 예산 관리 (token_budget / sliding_window Compactor Strategy)
- **비담당**
  - ❌ 프롬프트 섹션 템플릿 결정 — s03
  - ❌ 도구 선택 — s04
- **의심되면 여기로**: "실행 시점에 외부에서 데이터를 가져온다" 면 여기

### s07_llm — LLM 호출 + provider/api-key 관리

- **한 줄 정의**: `state.provider` 를 통해 LLM API 를 호출하고 응답/도구 호출/토큰 사용량을 수집한다.
- **담당 (v0.9.0+)**
  - **LLM provider 생성** (없으면 lazy init)
  - **API key 해석** (ExecutionContext → ServiceProvider → env → file)
  - **base_url 해석** (ServiceProvider → env)
  - 요청 조립 (system + messages + tool_definitions + temperature + max_tokens)
  - 재시도 (429/529/server) — ExponentialBackoff / NoRetry Strategy
  - 응답 파싱 — Anthropic / OpenAI Parser Strategy
  - 토큰/비용 추적 — DefaultTokenTracker / ModelPricingCalculator
  - Thinking block 처리 (선택)
  - Prompt caching 마커 — AnthropicCacheStrategy
- **비담당**
  - ❌ 도구 실행 — s08
  - ❌ 도구 카탈로그 결정 — s04
  - ❌ RAG 검색 — s06
- **의심되면 여기로**: LLM 호출 왕복 사이의 모든 것

### s08_execute — 도구 실행

- **한 줄 정의**: `state.pending_tool_calls` 의 tool_use 를 실행해 결과를 `state.tool_results` + user-role tool_result 메시지에 쌓는다.
- **담당**: router(composite / mcp / builtin) + executor(sequential / parallel)
- **비담당**
  - ❌ LLM 재호출 — s07 (루프는 Pipeline 이 s07↔s08 왕복)
  - ❌ 도구 결과로 컨텍스트 재주입 — s06 (다음 루프)
- **의심되면 여기로**: 도구 호출 자체의 수행이면 여기

### s09_validate — 응답 품질 평가

- **한 줄 정의**: 최종 응답 후보에 대해 점수(0~1)와 수치적 피드백을 `state.validation_score` / `state.validation_feedback` 에 기록한다.
- **담당**: llm_judge / rule_based / none Strategy. 평가만 하고 루프 결정은 안 함.
- **비담당**
  - ❌ retry 여부 결정 — **s10_decide**
  - ❌ 재실행 지시 — s10
- **의심되면 여기로**: "응답이 얼마나 좋은가?" 측정이면 여기

### s10_decide — 루프 제어

- **한 줄 정의**: validation_score / pending_tool_calls / iteration / cost / token budget 을 종합해 continue / complete / retry 를 결정한다.
- **담당**: Guard 체인 (cost/iteration/token), Decide Strategy (threshold / always_pass)
- **비담당**
  - ❌ 점수 매기기 — s09
- **의심되면 여기로**: "다음에 뭐 할지" 결정이면 여기

### s11_save — 실행 레코드 저장

- **한 줄 정의**: 완료된 턴의 입력/출력/metrics 를 `harness_execution_log` 에 insert 한다.
- **담당**: DB insert 한 번, DB 없으면 graceful skip
- **비담당**: 메트릭스 이벤트 발행 — s12
- **의심되면 여기로**: 영속 저장이면 여기

### s12_complete — 최종화 + 메트릭스 이벤트

- **한 줄 정의**: `state.final_output` 을 출력 포맷으로 다듬고 duration/tokens/cost 를 metrics 이벤트로 발행한다.
- **담당**: output_format (text/markdown/json), MetricsEvent 발행
- **비담당**: DB 저장 — s11
- **의심되면 여기로**: 파이프라인 외부로 결과를 내보내는 모든 것

---

## 3. 추가 감사할 때 쓰는 결정 트리

```
[새 기능 또는 버그 픽스 시작]
   │
   ▼
  Q1. 이 로직은 언제 실행되어야 하는가?
   ├─ 사용자 입력 수신 직후 (아직 LLM 호출 전)    → s01 또는 s02
   ├─ 매 LLM 호출 전에 컨텍스트를 주입할 때         → s03 (템플릿) or s06 (데이터)
   ├─ 매 LLM 호출 직전 (tool_def / provider 확정)  → s04 (tools) or s07 (provider)
   ├─ LLM 응답 직후                                → s07 (파싱) or s09 (평가)
   ├─ 도구 응답이 필요할 때                         → s08
   ├─ 루프를 계속할지 결정할 때                     → s10
   └─ 턴 종료 후                                   → s11 or s12
   │
   ▼
  Q2. 이 로직이 외부 서비스(Documents/Database/MCP)를 호출하는가?
   ├─ 예 → ServiceProvider 위임이 우선. httpx 직접 호출은 fallback 으로만.
   └─ 아니오 → Stage 내부 순수 로직.
   │
   ▼
  Q3. 이 로직이 Stage 선택에 따라 달라지는가?
   ├─ 예 → Strategy 로 추출 (if/elif 금지)
   └─ 아니오 → Stage 메서드로 충분
   │
   ▼
  Q4. 데이터는 어떻게 다른 Stage 에 전달되는가?
   ├─ state.<필드> 에 쓰기 (필드가 선언돼 있는가 확인)
   └─ 선언 안 돼 있으면 PipelineState 에 먼저 추가
```

---

## 4. Stage ID 하드코딩 방지

- `stages/strategies/strategy_resolver.py` 에서 `register_strategy("s01_input", ...)` 같은 문자열 ID 는 허용 (등록부).
- **그 외에** 다른 모듈에서 `"s01_input"` 문자열을 직접 참조하는 건 **레지스트리 조회로 교체** 대상.
- 외부 기여자가 `s99_lotte_approval` 같은 Stage 를 추가할 때:
  - `ArtifactRegistry.register_stage(...)` 로 등록 → `ALL_STAGES` 가 자동 확장.
  - Phase 분류 (`ingress` / `loop` / `egress`) 는 Stage 클래스의 `phase` property 로 선언.
  - 순서(`order`) 충돌 시 builder 가 경고.

---

## 5. Backward compatibility

- `harness_config.provider / model / temperature` 등의 **포맷은 유지**. 이 값이 어느 Stage 에서 "해석" 되는지만 v0.9.0 에서 재배치.
- 기존 저장된 워크플로우 JSON 은 영향 없음.
- 새 버전을 쓰는 이식측이 해야 할 일: 없음 (엔진 내부 이관).

---

## 6. 버전별 철학 변천

| 버전 | 변경 |
|---|---|
| v0.8.x | `s01_input` 이 provider/api-key/MCP discovery 까지 포함 (책임 과잉). s03 와 s06 가 RAG 검색 중복 실행. |
| **v0.9.0** | 이 문서 신설. s01 → s07 provider 이관, s03 RAG 제거 (s06 가 단독 담당), s04/s05 capability 분할 명시. Stage ID 하드코딩 재감사. |
| (이후) | Stage plugin 플러그인 entry_points 정리, Phase registry 확장. |

---

## 7. 이 문서를 언제 다시 봐야 하는가

- **새 Stage 를 제안할 때**
- **새 Strategy 를 제안할 때**
- **기존 Stage 의 파라미터를 추가할 때**
- **RAG / MCP / DB 같은 외부 자원을 연결할 때**
- **리뷰어가 PR 을 보고 "이건 어느 Stage 책임이지?" 라고 질문할 때**
- **Contributor 온보딩 시**

이 문서가 의심되면 업데이트 PR 을 같이 올려라. Stage 책임이 바뀌었는데 문서가 뒤늦게 따라가면 또 오염이 누적된다.
