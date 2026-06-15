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

## 장기실행(multi-session) 메모리
세션은 분리 실행되고 각 세션은 이전 기억 없이 시작한다(Anthropic long-running-agents). `xgen_harness.memory` 가 세션 *간* 상태 인계를 책임진다(s06_context 의 세션 *내* 압축과 보완).
- **`ProgressLog` / `ProgressItem`** — 세션 밖에 사는 progress artifact(작업항목·검증절차·pass/fail). `pending()` 으로 다음 할 일 즉시 복원.
- **`EvidenceSet` / `EvidenceItem`**(v1.19.0) — 검색·추론 에이전트의 **외부화 작업기억**(Harness-1 차용). 중요도 태깅 + dedup + cap 된 근거 집합. 전체 본문은 PD(`pd_stores["evidence"]`)로 **step-in**(`fetch_pd`), compact 뷰는 **step-out**(`render()`). opt-in 빌트인 `curate`/`verify`/`list_evidence` 로 policy 는 의미 결정만, 하네스가 bookkeeping. 세션 압축에도 살아남음.
- **`SessionStore`**(Protocol) — `InMemory`/`File` 빌트인(무거운 의존성 0). 플랫폼은 `entry_points` 로 자기 DB 백엔드를 코어 수정 없이 끼운다. `save_session`/`load_session` 으로 세션↔store 인계.

## 안정 에러코드
모든 `HarnessError` 는 `exec.<component>.<reason>` 코드(메시지와 독립한 머신 식별자)를 들고 다닌다. 이식측·외부 소비자가 릴리즈 간 안정 분기 가능. `error_code(exc)` / `ALL_ERROR_CODES`.

---

## 외부 확장점 (`entry_points`)
외부 패키지가 자기 `pyproject.toml` 의 `[project.entry-points."xgen_harness.<group>"]` 에 항목을 추가하면 부팅 시 자동 발견·register (엔진 소스 수정 0). 주요 group:

`stages` · `strategies` · `node_adapters` · `tool_sources` · `providers` · `capabilities` · `fan_out_strategies` · `evaluation_criteria` · `orchestrators` · `sandbox_verifiers` · `tools`(갤러리) · `phases` · `node_plugins` · `model_pricing` · `term_expanders` · `guards` · `active_policy_renderers` · `collection_enrichers` · `resource_providers` · `session_stores`

> 내장 Guard 7종(token_budget·cost_budget·iteration·content·tool_precondition·hitl·tool_diversity)도 `guards` group 으로 등록 — 외부 기여자도 같은 경로. `tool_diversity`(v1.19.0)는 동일 도구를 같은 인자로 반복 호출(검색 붕괴)하면 PRE_TOOL 에서 차단·교정(Harness-1 차용).

---

## 공식 Public API
`xgen_harness.__all__` 참조. 핵심: `Pipeline`, `PipelineState`, `HarnessConfig`, `TokenUsage`, `ALL_STAGES`, `REQUIRED_STAGES`, `PRESETS`, `compile_nom_graph`, `NOMGraph`, `ProgressLog`, `EvidenceSet`, `EvidenceItem`, `Importance`, `SessionStore`, `FileSessionStore`, `ALL_ERROR_CODES`. (`__version__` 은 설치 wheel 메타에서 런타임 조회.)

---

## 이식(PORT) 분리 원칙
- **엔진(이 레포)** = generic. 특정 플랫폼 어휘 금지.
- **이식측**(예: `xgen-workflow/harness_bridge`) = 엔진을 플랫폼에 연결(provider·tool_source·정책 default·locale·평가전략을 `entry_points`/주입으로 끼움).
- 의존 방향: 이식이 `xgen-harness>=N,<2.0` 으로 엔진 wheel 을 핀. **엔진 먼저 고치고 publish → 이식 핀 범프.**

---

## 배포 (Release)

엔진은 PyPI 패키지 **`xgen-harness`** 로 배포된다. 버전을 올리고 두 가지 방법 중 하나로 발행한다.

**0. 버전 올리기 + 테스트**
```bash
# pyproject.toml 의 version 을 X.Y.Z 로 수정 후
python -m pytest -q          # tests/ 전부 green 확인
```

**A. 수동 배포 (어디서든 즉시 — PyPI 토큰만 있으면 됨)**
```bash
python -m build                                   # dist/xgen_harness-X.Y.Z-*.whl + .tar.gz
TWINE_USERNAME=__token__ TWINE_PASSWORD=<pypi-token> \
  python -m twine upload dist/xgen_harness-X.Y.Z*  # PyPI 발행
git push <remote> main && git push <remote> vX.Y.Z # GitHub 반영 (+ 태그)
```
> Windows 콘솔에서 진행바 유니코드가 깨지면 `PYTHONUTF8=1 ... twine upload --disable-progress-bar`.

**B. 자동 배포 (태그 push → GitHub Actions OIDC)**
`v*` 태그를 push 하면 `publish.yml` 이 **PyPI Trusted Publishing(OIDC)** 으로 발행한다(토큰 불필요). 사전 1회: PyPI → `xgen-harness` → Settings → Publishing 에 해당 repo + `publish.yml` 을 trusted publisher 로 등록. (여러 repo 를 등록하면 어디서 태그를 밀든 발행 가능.)
```bash
git tag vX.Y.Z && git push <remote> vX.Y.Z        # → Actions 가 build + publish
```

- 컴파일된 사용자 워크플로우 wheel(`plateer-xgen-wf-*`)은 `publish-user-wheel.yml`(cluster repository_dispatch) 으로 별도 발행.
- **이식측**(`xgen-harness>=N,<2.0` 핀)은 엔진 publish 후 핀을 범프하면 자동 반영.

링크: [Repository](https://github.com/PlateerLab/xgen-harness-executor) · [CHANGELOG](CHANGELOG.md)
