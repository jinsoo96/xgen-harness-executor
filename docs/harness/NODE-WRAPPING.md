# Node Wrapping — 기존 캔버스 노드를 하네스 도구로 변환하는 알고리즘

**문서 지위**: 🚨 **xgen-workflow 캔버스 노드가 어떻게 하네스 LLM 도구로 말려 들어오는지의 단일 기준**. Node adapter / control policy / dispatch 분기 변경 시 이 문서와 함께 수정.

---

## 0. 왜 이 문서가 필요한가

`xgen-workflow` 의 캔버스 노드(예: `document_loaders/Qdrant`, `ml/MachineLearningTool`) 는 원래 **워크플로우 그래프의 박스** 로 설계됐다. 하네스는 이 노드를 **LLM 이 호출하는 함수(tool)** 로 소비한다. 두 세계의 인터페이스가 달라서 중간에 **Adapter** 층이 끼어 변환한다.

이 문서는 그 **변환 알고리즘**을 설명한다. 새 카테고리를 추가하거나 policy 규칙을 수정하거나 dispatch 분기를 손대려는 사람은 여기부터 읽는다.

---

## 1. 전체 그림 (3개 층)

```
┌─────────────────┐   ① build()    ┌──────────────────────┐  ② execute_tool()  ┌──────────────┐
│ 노드 JSON       │───────────────▶│ _XgenNodeRef         │───────────────────▶│ NodeClass    │
│ (workflow_data) │                │ (dispatch 참조 객체) │                    │ .execute()   │
└─────────────────┘                └──────────────────────┘                    └──────────────┘
        ↑                                 ↑                                          ↑
   입력 (캔버스)                    ResourceRegistry 저장                       실제 캔버스 노드
```

- **① build** — workflow_data 의 각 node 를 adapter 가 읽어 `_XgenNodeRef` 를 만들고 ResourceRegistry 의 3 슬롯에 등록.
- **② execute_tool** — LLM 이 tool_use 로 호출하면 `_XgenNodeRef` 타입이라 `_call_xgen_node` 로 라우팅.
- **③ execute** — `editor.node_composer.get_node_class_by_id(spec_id)` 로 실제 노드 클래스 로드 후 `execute()` 호출.

---

## 2. Bootstrap — 카테고리 → Builder 등록

`xgen_harness/adapters/xgen.py` 가 import 되는 순간 `bootstrap_xgen_node_adapters()` 가 자동 호출된다. `xgen_node_adapters.py:_XGEN_CATEGORY_ADAPTERS` 의 dict 를 순회하며 `register_node_adapter(NodeAdapter(...))` 로 등록.

```python
_XGEN_CATEGORY_ADAPTERS = {
    # LLM 도구로 wrap 됨 (tool_def 발행):
    "xgen_document_loaders": (["document_loaders"], _build_document_loader_tool, ...),
    "xgen_file_system":      (["file_system"],      _build_file_system_tool,      ...),
    "xgen_tools":            (["tools"],            _build_tools_category_tool,    ...),
    "xgen_arithmetic":       (["arithmetic"],       _build_math_tool,              ...),
    "xgen_ml":               (["ml"],               _build_ml_tool,                ...),
    # metadata-only (tool_def 발행 안 함 — Stage 자체 로직이 처리):
    "xgen_agents_meta":      (["agents"],      _build_generic_metadata_only, ...),
    "xgen_chat_models_meta": (["chat_models"], _build_generic_metadata_only, ...),
    "xgen_memory_meta":      (["memory"],      _build_generic_metadata_only, ...),
    "xgen_routers_meta":     (["routers"],     _build_generic_metadata_only, ...),
    "xgen_interaction_meta": (["interaction"], _build_generic_metadata_only, ...),
}
```

**외부 확장** — `xgen_harness.node_adapters` entry_points 그룹에 외부 패키지가 `NodeAdapter` 를 등록하면 `bootstrap_default_node_adapters()` 가 자동 발견. 엔진 코드 수정 없이 새 카테고리 추가.

---

## 3. Build 알고리즘 — Adapter 의 핵심 6단계

각 builder (`_build_document_loader_tool` 등) 는 공통 헬퍼 `_unpack_node_for_builder()` + `_apply_node_overrides()` 를 호출. 의사코드:

```python
def build(node, registry):
    # ━━━ ① 언팩 ━━━
    instance_id = node["id"]                         # 캔버스 인스턴스 id (그래프 내 유일)
    spec_id     = node["data"]["id"]                 # 노드 클래스 id (get_node_class_by_id 조회 키)
    category    = node["data"]["functionId"]         # document_loaders / tools / ml ...
    params_def  = node["data"]["parameters"]         # [{id, type, value, description}, ...]
    base        = {p.id: p.value for p in params_def}

    # ━━━ ② 중복 방지 ━━━
    if instance_id in registry._tool_executors:
        return   # 이미 등록됨

    # ━━━ ③ Policy 조회 (3-tier lookup) ━━━
    policy    = load_control_policy()                        # node_control_policy.json
    overrides = registry.get_node_overrides().get(instance_id, {})   # 사용자 toggle

    for p in params_def:
        # 우선순위: nodes[spec_id].params[p] > categories[cat] > global_default
        meta = (policy["nodes"].get(spec_id, {})
                      .get("params", {}).get(p.id)
                or policy["categories"].get(category, {})
                or policy["global_default"])
        control = meta["control"]           # manual | auto | switchable
        mode    = (overrides.get(p.id) or {}).get("mode") or meta["default_mode"]

        # ━━━ ④ control → 2 슬롯 분기 ━━━
        if control == "manual" or (control == "switchable" and mode == "manual"):
            # LLM 스키마에 노출 안 됨. _XgenNodeRef.params 에만.
            manual_params[p.id] = (overrides.get(p.id) or {}).get("value") \
                                  or base.get(p.id) \
                                  or p.default
        else:  # auto
            # input_schema.properties 에 노출 → LLM 이 런타임에 채움
            auto_props[p.id] = {"type": map_type(p.type), "description": p.description}

    # ━━━ ⑤ synthetic_auto (정의엔 없지만 LLM 이 채울 입력) ━━━
    for key, spec in policy["nodes"].get(spec_id, {}).get("synthetic_auto", {}).items():
        auto_props[key] = {"type": spec["type"], "description": spec["auto_hint"]}
        if spec.get("required"):
            auto_required.append(key)

    # ━━━ ⑥ ResourceRegistry 3 슬롯에 원자적 등록 ━━━
    tool_name = manual_params.get("tool_name") or f"{category}_{instance_id}"

    registry._tool_defs.append({                  # 1. LLM 이 볼 스키마
        "name": tool_name,
        "description": manual_params.get("description") or node["data"]["nodeName"],
        "input_schema": {
            "type": "object",
            "properties": auto_props,
            "required": auto_required,
        },
    })

    registry._tool_executors[tool_name] = _XgenNodeRef(    # 2. dispatch 키
        node_id=instance_id, spec_id=spec_id, category=category,
        params=manual_params,           # ← LLM 이 못 보는 값
        control_map=final_controls,     # ← 디버깅 / UI 피드백
    )

    registry._tool_infos.append(ResourceInfo(     # 3. UI 표시용 메타
        resource_type="rag_collection" if category == "document_loaders" else "custom_tool",
        name=tool_name,
        source=manual_params.get("collection_name") or category,
    ))
```

---

## 4. 3대 불변 규칙

### 규칙 1 — **Manual 값은 LLM 이 덮어쓸 수 없다** (이중 방어)

```python
# Dispatch 시:
merged = {**tool_input, **executor.params}
#          ^^^^^^^^^^^  먼저 전개 (LLM 입력)
#                       ^^^^^^^^^^^^^^^^^  나중에 전개 (manual — 우선)
```

- **1중 방어**: `input_schema.properties` 에 manual 키가 없어 LLM 스키마상 존재하지 않음.
- **2중 방어**: 설령 LLM 이 hallucinate 로 `{"collection_name": "hacked"}` 같은 키를 주입해도 `executor.params` 가 뒤에 spread 되어 덮어쓰기 차단.

이 규칙 덕분에 `api_key`, `db_dsn`, `tool_name` 같은 **민감값** 을 안전하게 manual 로 박을 수 있다.

### 규칙 2 — **Policy lookup 은 specific → general**

```
policy.nodes[spec_id].params[param_id]        ← 1순위 (노드별 특정 파라미터)
    ↓ 없으면
policy.categories[category].default_control   ← 2순위 (카테고리 기본)
    ↓ 없으면
policy.global_default                          ← 최후 ({"switchable", "manual"})
```

이관 가능성: 특정 노드 id 매핑 (예: `document_loaders/QdrantRetrievalToolHard`) 은 엔진의 policy JSON 에 박혀있지만, **이상적으로는 해당 노드를 소유한 플러그인 패키지가 `xgen_harness.node_policies` entry_points 로 공급해야** 한다. 현재는 backward-compat 으로 엔진 내장.

### 규칙 3 — **Dispatch 는 type switch** (`execute_tool` 의 분기)

```python
async def execute_tool(self, tool_name, tool_input):
    executor = self._tool_executors[tool_name]
    if   isinstance(executor, Tool):           return await executor.execute(...)
    elif isinstance(executor, _MCPToolRef):    return await self._call_mcp_tool(...)
    elif isinstance(executor, _APIToolRef):    return await self._call_api_tool(...)
    elif isinstance(executor, _DBToolRef):     return await self._call_db_tool(...)
    elif isinstance(executor, _XgenNodeRef):   return await self._call_xgen_node(...)
    elif callable(executor):                   return await executor(tool_name, tool_input)
```

같은 `tool_executors` dict 에 **5종 타입**이 공존한다. LLM 관점에서는 구분 없이 하나의 도구로 보이고, dispatch 시점에 타입으로 갈라진다. 새 타입을 추가하려면 (a) builder 가 해당 타입을 register, (b) `execute_tool` 에 분기 한 줄 추가.

---

## 5. 실예시 — `document_loaders/Qdrant`

### 입력 (캔버스 JSON)

```json
{
  "id": "node_12345",
  "data": {
    "id": "document_loaders/Qdrant",
    "functionId": "document_loaders",
    "nodeName": "Qdrant Search",
    "parameters": [
      { "id": "collection_name", "value": "assort",  "type": "STR" },
      { "id": "top_k",           "value": 5,         "type": "INT" }
    ]
  }
}
```

### Policy (node_control_policy.json 발췌)

```json
"document_loaders/Qdrant": {
  "params": {
    "collection_name":  { "control": "manual" },
    "top_k":            { "control": "switchable", "default_mode": "manual" }
  },
  "synthetic_auto": {
    "query": { "type": "string", "auto_hint": "Search query...", "required": true }
  }
}
```

### 출력 (ResourceRegistry 에 쓴 내용)

```python
# registry._tool_defs 에 추가된 항목
{
  "name": "rag_node_12345",
  "description": "Document retrieval on 'assort' (top_k=5)",
  "input_schema": {
    "type": "object",
    "properties": {
      "query": { "type": "string", "description": "Search query..." }
    },
    "required": ["query"]
  }
}

# registry._tool_executors 에 추가된 항목
"rag_node_12345": _XgenNodeRef(
  node_id="node_12345",
  spec_id="document_loaders/Qdrant",
  category="document_loaders",
  params={ "collection_name": "assort", "top_k": 5 },   # ← LLM 이 못 봄
  control_map={...},
)
```

LLM 관점에서는 **`query` 만 있는** 도구로 보인다. `collection_name` / `top_k` 는 이미 박혀 있어 LLM 이 결정할 필요도 없고 덮어쓸 수도 없다.

### LLM tool_use + Dispatch

```json
{ "name": "rag_node_12345", "input": { "query": "인기 카테고리" } }
```

```python
merged = {"query": "인기 카테고리", "collection_name": "assort", "top_k": 5}
await _call_xgen_node("node_12345", "document_loaders/Qdrant", "document_loaders", merged)
#   └─ editor.node_composer.get_node_class_by_id("document_loaders/Qdrant")
#      └─ NodeClass.execute(query="인기 카테고리", collection_name="assort", top_k=5)
```

---

## 6. metadata-only 카테고리

`agents` / `chat_models` / `memory` / `routers` / `interaction` 은 **tool_def 를 발행하지 않는다**. 이유:

- `agents` — Stage `s07_llm` 자체가 에이전트 호출을 한다. 별도 tool 로 감싸면 중복.
- `chat_models` — Stage `s01/s07` 의 provider 설정과 겹친다 (v0.9.2+ s07 전담).
- `memory` — Stage `s02_memory` 가 히스토리를 불러온다.
- `routers` — Stage `s10_decide` 가 루프 제어를 한다.
- `interaction` — 실행 메타 (interaction_id) 는 실행 전에 주어진다.

이들 노드는 `_build_generic_metadata_only` 로 처리 — `ResourceInfo` 만 발행해 **"이 카테고리 노드가 존재함"** 을 UI/Capability 에 알린다. LLM 스키마에는 노출 안 됨.

---

## 7. Soft import — 라이브러리 독립성

`_call_xgen_node` 에서:

```python
try:
    from editor.node_composer import get_node_class_by_id
except Exception as e:
    return f"Error: xgen-workflow editor.node_composer is unavailable..."
```

→ 라이브러리 단독 사용 (xgen-workflow 없는 환경, 예: 테스트) 에서도 import 자체는 성공. 실제 호출 시만 graceful 에러 문자열 반환. 이 덕분에 엔진은 xgen-workflow 에 강결합되지 않는다.

---

## 8. 외부 확장 — 플러그인으로 새 카테고리

### 옵션 A — `xgen_harness.node_adapters` entry_points

```toml
# 외부 패키지의 pyproject.toml
[project.entry-points."xgen_harness.node_adapters"]
my_custom_category = "my_pkg.adapters:register"
```

```python
# my_pkg/adapters.py
from xgen_harness.adapters.node_adapters import NodeAdapter

def register():
    return NodeAdapter(
        name="my_custom_category",
        function_ids=["my_category"],
        build=my_builder,
        resource_type="custom_tool",
        description="...",
    )
```

### 옵션 B — `XGEN_HARNESS_NODE_POLICY_PATH` env 로 policy 주입

외부 policy JSON 경로를 환경변수로 지정하면 builtin policy 와 merge 됨. 특정 노드의 control 정책만 바꾸고 싶을 때.

### 옵션 C — 이식 측 workflow_data 전처리

**권장**: xgen-workflow `feature/harness-v2` 의 `controller/workflow/utils/harness_inherit.py` 처럼, 실행 엔드포인트 진입 시 workflow_data 를 전처리해 노드 파라미터를 변환. 엔진 코드 수정 0, 이식측만 변경.

→ 이 방식은 `feedback_engine_untouched_integrate_in_port.md` 메모의 원칙과 일치.

---

## 9. 한계와 다음 단계

### 지금 남은 오염
- `_XGEN_CATEGORY_ADAPTERS` dict 가 엔진 안에 있음 → 엔진이 특정 카테고리 이름을 안다. (후속: 플러그인 이관)
- `node_control_policy.json` 에 `document_loaders/QdrantRetrievalToolHard` 같은 특정 노드 id 박힘 → 플러그인이 자기 policy 로 공급해야 함.
- `functionId` 문자열 매칭이 category 전체 — 하위 카테고리가 세분화되면 분류 기준이 부족해질 수 있음.

### 로드맵
- v0.9.x: Stage ID 하드코딩 레지스트리 전환
- v0.10.x: `_XGEN_CATEGORY_ADAPTERS` → 외부 plugin entry_points 로 이관 (xgen-nodes-* 패키지화)
- v1.0: 본체 내장 노드 매핑 제거, 플러그인 로딩만 허용

---

## 참고

- `xgen_harness/integrations/xgen_node_adapters.py` — 실제 builder 구현 6종
- `xgen_harness/integrations/node_control_policy.json` — 현재 policy 데이터
- `xgen_harness/adapters/node_adapters.py` — NodeAdapter 인터페이스 + entry_points 로더
- `xgen_harness/adapters/resource_registry.py` — `_XgenNodeRef` dispatch (`execute_tool`)
- `docs/harness/00-PHILOSOPHY.md` — Stage 책임 경계
- `docs/harness/EXTENSION_POINTS.md` — 9개 확장 지점 개요
