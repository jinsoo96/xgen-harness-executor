# xgen-harness

**LLM 자율 조립 에이전트 하네스.** 10-stage 고정 파이프라인 한 줄 설정으로 에이전트를 돌리고, 그 워크플로우를 그대로 **컴파일**(npm tarball / Python wheel)해 env-only standalone(또는 MCP 서버)으로 실행한다.

- **도메인 agnostic 엔진** — xgen/canvas/cluster 같은 특정 플랫폼 어휘를 모른다. 도메인 지식은 외부 패키지가 `entry_points` 로 끼워넣는다(이식측 = `harness_bridge`).
- 버전 히스토리: [CHANGELOG.md](CHANGELOG.md).

```bash
pip install xgen-harness
# 선택: pip install "xgen-harness[api,mcp]"
```

---

## 핵심 개념
- **Planner 없음** (v1.1.0+): 10 Stage 가 항상 순서대로 실행. `HarnessConfig` 만 만들면 돈다.
- **Stage = 점진 노출**: 각 Stage 는 자기 담당(capability/도구/리소스)만 LLM 에 노출하고, 본문 LLM 이 그 안에서 도구를 자율 호출한다.
- **Strategy 핀**: Stage 구현만 골라 끼운다. 나머지는 디폴트.

```python
from xgen_harness import HarnessConfig, Pipeline

# 표준 — 직선 흐름
config = HarnessConfig(max_iterations=5)

# 특정 stage strategy 핀
config = HarnessConfig(active_strategies={
    "s06_context": "cascade",          # 컨텍스트 압축 자동
    "s08_decide":  "judge_then_loop",  # 응답 품질 평가(judge) 활성
})

# 도구 없이 단순 LLM 호출
config = HarnessConfig(active_strategies={"s04_tool": "none"})

# Pipeline 이 config + provider + tool_source 를 받아 10-stage 실행
# (이식측은 XgenAdapter 등으로 provider/tool_source 를 주입)
```

---

## 10 Stage
| # | Stage | 책임 | 기본 strategy |
|---|---|---|---|
| 0 | `s00_harness` | LLM 핸들 owner + 본문호출 dispatcher | streaming |
| 1 | `s01_input` ✱ | 입력 정규화·멀티모달 추출 | default |
| 2 | `s02_history` | 같은 interaction 이전 turn 로드 | default |
| 3 | `s03_prompt` | system_prompt·citation·thinking_mode | section_priority |
| 4 | `s04_tool` | 도구 카탈로그(ToolSource 단일 채널) + Capability 자동발견 | progressive_3level |
| 5 | `s05_policy` | Guard 체인 ×4 훅 (`guards` 비면 bypass) | — |
| 6 | `s06_context` | scope 선언(RAG/Ontology/DB/폴더/파일) + 토큰 컴팩션 (검색은 s07 도구) | cascade |
| 7 | `s07_act` | 도구 실행 (read 병렬 / write 직렬) | default |
| 8 | `s08_decide` ✱ | 루프 계속/종료 + (선택)응답 품질 judge | threshold |
| 9 | `s09_finalize` ✱ | 최종 응답 + Metrics + (선택)DB 기록 | default |

`✱` = `REQUIRED_STAGES`(비활성 불가). 그룹: 초기화(0~4, 1회) · 루프(5~8, `max_iterations`회) · 종료(9, 1회).

---

## 도구 — `ToolSource` 단일 채널
도구는 전부 `s04_tool` 의 **ToolSource 인터페이스**로 들어온다(`list_tools` / `call_tool`). 엔진은 인터페이스만 정의하고, 구체 소스(MCP·캔버스 노드·HTTP API·캔버스 워크플로우 등)는 외부/이식측이 구현·등록한다.
- **MCP 받기**: 외부 MCP 서버를 하네스 안 도구로 노출.
- **MCP 내보내기**: 하네스 워크플로우를 MCP stdio 서버로 발행.
- **Capability**: 선언적 도구 wiring 자동 발견.

---

## Compile — 워크플로우 → 단일 산출물
하네스 워크플로우를 **npm tarball / Python wheel** 로 컴파일하면 동일 spec 으로 env-only standalone 실행(+ `serve-mcp` 모드). NOM IR(wheel + MCP 카탈로그 + 격리 페이로드 단일 그래프) + 발행 전 Sandbox 격리 검증 지원.

---

## 외부 확장점 (`entry_points`)
외부 패키지가 자기 `pyproject.toml` 의 `[project.entry-points."xgen_harness.<group>"]` 에 항목을 추가하면 부팅 시 자동 발견·register (엔진 소스 수정 0). 주요 group:

`stages` · `strategies` · `node_adapters` · `tool_sources` · `providers` · `capabilities` · `fan_out_strategies` · `evaluation_criteria` · `orchestrators` · `sandbox_verifiers` · `tools`(갤러리) · `phases` · `node_plugins` · `model_pricing` · `term_expanders` · `guards` · `active_policy_renderers` · `collection_enrichers` · `resource_providers`

> 내장 Guard 5+종(token_budget·cost_budget·iteration·content·tool_precondition·hitl)도 `guards` group 으로 등록 — 외부 기여자도 같은 경로.

---

## 공식 Public API
`xgen_harness.__all__` 참조. 핵심: `Pipeline`, `PipelineState`, `HarnessConfig`, `TokenUsage`, `ALL_STAGES`, `REQUIRED_STAGES`, `PRESETS`, `compile_nom_graph`, `NOMGraph`. (`__version__` 은 설치 wheel 메타에서 런타임 조회.)

---

## 이식(PORT) 분리 원칙
- **엔진(이 레포)** = generic. 특정 플랫폼 어휘 금지.
- **이식측**(예: `xgen-workflow/harness_bridge`) = 엔진을 플랫폼에 연결(provider·tool_source·정책 default·locale·평가전략을 `entry_points`/주입으로 끼움).
- 의존 방향: 이식이 `xgen-harness>=N,<2.0` 으로 엔진 wheel 을 핀. **엔진 먼저 고치고 publish → 이식 핀 범프.**

---

## 릴리즈
`v*` 태그 push → GitHub Actions(`publish.yml`)가 PyPI Trusted Publishing(OIDC)으로 발행. 사용자 wheel 은 `publish-user-wheel.yml`(repository_dispatch). 절차: `version` bump → 태그 push.

링크: [Repository](https://github.com/jinsoo96/xgen-harness-executor) · [CHANGELOG](CHANGELOG.md)
