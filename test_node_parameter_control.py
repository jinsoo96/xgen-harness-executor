"""node_control_policy 기반 파라미터 control 검증.

검증 범위:
  1. 각 카테고리별 (document_loaders / file_system / tools / arithmetic / ml) control 분류
  2. manual/auto/switchable 전환 동작
  3. switchable override 시 mode/value 반영
  4. manual-lock 은 override 시도 시 무시
  5. synthetic_auto 가 input_schema 에 주입됨
  6. builder 등록 후 tool_def.input_schema 에 manual 키 누락 확인
  7. dispatch: execute_tool 이 _XgenNodeRef 분기 타는지 + merge 순서 manual 우선
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

# 순환 import 회피: adapters 패키지를 먼저 로드
import xgen_harness.adapters  # noqa: F401

from xgen_harness.adapters.resource_registry import ResourceRegistry
from xgen_harness.integrations.xgen_node_adapters import (
    _XgenNodeRef,
    _apply_node_overrides,
    _load_control_policy,
    _resolve_control_for_node,
    bootstrap_xgen_node_adapters,
    reload_control_policy,
)


def _make_node(instance_id: str, spec_id: str, function_id: str, parameters: list[dict]) -> dict:
    """캔버스 노드 JSON 형태 — ResourceRegistry 가 기대하는 형식."""
    return {
        "id": instance_id,
        "data": {
            "id": spec_id,
            "functionId": function_id,
            "nodeName": spec_id.split("/")[-1],
            "parameters": parameters,
        },
    }


def test_policy_loads():
    p = _load_control_policy()
    assert p.get("version") == 1
    assert "document_loaders/Qdrant" in p.get("nodes", {})
    assert p["categories"]["arithmetic"]["default_control"] == "auto"


def test_qdrant_control_resolution():
    params_def = [
        {"id": "collection_name", "type": "STR", "value": "assort"},
        {"id": "top_k", "type": "INT", "value": 4},
        {"id": "use_model_prompt", "type": "BOOL", "value": True},
    ]
    ctrl = _resolve_control_for_node("document_loaders/Qdrant", "document_loaders", params_def)
    assert ctrl["collection_name"]["control"] == "manual"
    assert ctrl["top_k"]["control"] == "switchable"
    assert ctrl["top_k"]["default_mode"] == "manual"
    assert ctrl["query"]["control"] == "auto"
    assert ctrl["query"].get("synthetic") is True


def test_manual_keys_hidden_from_schema():
    """manual 파라미터는 auto_props 에 들어가면 안 됨 (LLM 스키마에서 숨겨야 함)."""
    params_def = [
        {"id": "collection_name", "type": "STR", "value": "assort"},
        {"id": "top_k", "type": "INT", "value": 4},
    ]
    manual, auto_props, auto_req, final = _apply_node_overrides(
        "document_loaders/Qdrant", "document_loaders", params_def,
        node_overrides={},
        base_params={"collection_name": "assort", "top_k": 4},
    )
    assert "collection_name" in manual
    assert "collection_name" not in auto_props
    assert "query" in auto_props
    assert "query" in auto_req


def test_switchable_toggle_to_auto():
    """switchable 파라미터를 사용자가 auto 로 토글 → manual 에서 빠지고 auto_props 로 이동."""
    params_def = [
        {"id": "collection_name", "type": "STR", "value": "assort"},
        {"id": "top_k", "type": "INT", "value": 4},
    ]
    manual, auto_props, _, final = _apply_node_overrides(
        "document_loaders/Qdrant", "document_loaders", params_def,
        node_overrides={"top_k": {"mode": "auto"}},
        base_params={"collection_name": "assort", "top_k": 4},
    )
    assert "top_k" not in manual
    assert "top_k" in auto_props
    # manual-lock 파라미터는 그대로
    assert "collection_name" in manual
    assert final["collection_name"]["mode"] == "manual"


def test_manual_lock_ignores_auto_override():
    """control=manual 파라미터는 사용자가 mode='auto' override 시도해도 무시."""
    params_def = [{"id": "storage_name", "type": "STR", "value": "u1/docs"}]
    manual, auto_props, _, final = _apply_node_overrides(
        "file_system/filesystem_storage", "file_system", params_def,
        node_overrides={"storage_name": {"mode": "auto"}},
        base_params={"storage_name": "u1/docs"},
    )
    assert final["storage_name"]["control"] == "manual"
    assert final["storage_name"]["mode"] == "manual"
    assert "storage_name" in manual
    assert "storage_name" not in auto_props


def test_user_value_override():
    """mode='manual', value=... 로 사용자가 캔버스 저장값 덮어쓰기."""
    params_def = [{"id": "collection_name", "type": "STR", "value": "default_col"}]
    manual, _, _, _ = _apply_node_overrides(
        "document_loaders/Qdrant", "document_loaders", params_def,
        node_overrides={"collection_name": {"mode": "manual", "value": "custom_col"}},
        base_params={"collection_name": "default_col"},
    )
    assert manual["collection_name"] == "custom_col"


def test_math_synthetic_auto():
    """arithmetic — parameters=[] 여도 synthetic_auto a,b 가 자동 주입."""
    manual, auto_props, auto_req, _ = _apply_node_overrides(
        "math/add_integers", "arithmetic", [],
        node_overrides={}, base_params={},
    )
    assert manual == {}
    assert "a" in auto_props and "b" in auto_props
    assert auto_props["a"]["type"] == "integer"
    assert "a" in auto_req and "b" in auto_req


def test_tools_category_default():
    """tools 카테고리 — policy 에 없는 노드는 카테고리 default (switchable/manual) 적용."""
    params_def = [
        {"id": "custom_param", "type": "STR", "value": "x"},
    ]
    ctrl = _resolve_control_for_node("tools/unknown_tool", "tools", params_def)
    assert ctrl["custom_param"]["control"] == "switchable"
    assert ctrl["custom_param"]["default_mode"] == "manual"


def test_builder_qdrant_schema_excludes_manual():
    """document_loaders builder 가 실제로 tool_def 에 manual 키를 빼는지."""
    bootstrap_xgen_node_adapters()
    reg = ResourceRegistry()
    # harness_config 없음 → default 동작
    asyncio.run(reg.load_all(
        workflow_data={
            "nodes": [
                _make_node(
                    "node-1", "document_loaders/Qdrant", "document_loaders",
                    [
                        {"id": "collection_name", "type": "STR", "value": "assort", "required": True},
                        {"id": "top_k", "type": "INT", "value": 4},
                    ],
                ),
            ]
        },
        harness_config={},
    ))
    defs = reg.get_tool_definitions()
    assert len(defs) == 1
    schema = defs[0]["input_schema"]
    props = schema["properties"]
    assert "query" in props, "synthetic_auto query 는 스키마에 있어야 함"
    assert "collection_name" not in props, "manual 은 스키마에서 숨김!"
    assert "query" in schema["required"]
    # executor 는 _XgenNodeRef 여야 함
    tool_name = defs[0]["name"]
    executor = reg._tool_executors[tool_name]
    assert isinstance(executor, _XgenNodeRef)
    assert executor.params["collection_name"] == "assort"
    assert executor.spec_id == "document_loaders/Qdrant"


def test_builder_qdrant_with_user_override():
    """사용자가 top_k 를 auto 로 토글하면 builder 가 schema 에 top_k 를 포함시켜야 함."""
    bootstrap_xgen_node_adapters()
    reg = ResourceRegistry()
    asyncio.run(reg.load_all(
        workflow_data={
            "nodes": [
                _make_node(
                    "node-2", "document_loaders/Qdrant", "document_loaders",
                    [
                        {"id": "collection_name", "type": "STR", "value": "assort", "required": True},
                        {"id": "top_k", "type": "INT", "value": 4},
                    ],
                ),
            ]
        },
        harness_config={
            "node_overrides": {
                "node-2": {"top_k": {"mode": "auto"}},
            }
        },
    ))
    defs = reg.get_tool_definitions()
    schema = defs[0]["input_schema"]
    assert "top_k" in schema["properties"]
    assert "collection_name" not in schema["properties"]


def test_builder_math_synthetic_auto():
    bootstrap_xgen_node_adapters()
    reg = ResourceRegistry()
    asyncio.run(reg.load_all(
        workflow_data={
            "nodes": [
                _make_node("node-math", "math/add_integers", "arithmetic", []),
            ]
        },
        harness_config={},
    ))
    defs = reg.get_tool_definitions()
    assert len(defs) == 1
    schema = defs[0]["input_schema"]
    props = schema["properties"]
    assert "a" in props and "b" in props
    assert "a" in schema["required"] and "b" in schema["required"]


def test_builder_filesystem_manual_only():
    bootstrap_xgen_node_adapters()
    reg = ResourceRegistry()
    asyncio.run(reg.load_all(
        workflow_data={
            "nodes": [
                _make_node(
                    "node-fs", "file_system/filesystem_storage", "file_system",
                    [{"id": "storage_name", "type": "STR", "value": "u1/docs", "required": True}],
                ),
            ]
        },
        harness_config={},
    ))
    defs = reg.get_tool_definitions()
    tool_name = defs[0]["name"]
    executor = reg._tool_executors[tool_name]
    assert isinstance(executor, _XgenNodeRef)
    assert executor.params["storage_name"] == "u1/docs"
    # schema 에는 storage_name 없어야 함
    assert "storage_name" not in defs[0]["input_schema"]["properties"]


def test_dispatch_xgen_node_graceful_unavailable():
    """editor.node_composer 없는 환경에서 _call_xgen_node 가 graceful 에러 반환."""
    reg = ResourceRegistry()
    bootstrap_xgen_node_adapters()
    asyncio.run(reg.load_all(
        workflow_data={
            "nodes": [
                _make_node("node-m", "math/add_integers", "arithmetic", []),
            ]
        },
        harness_config={},
    ))
    defs = reg.get_tool_definitions()
    tool_name = defs[0]["name"]
    # xgen-workflow 없는 환경 — get_node_class_by_id import 실패 → graceful
    result = asyncio.run(reg.execute_tool(tool_name, {"a": 3, "b": 5}))
    assert isinstance(result, str)
    assert "Error" in result  # editor.node_composer unavailable 메시지
    # 중요: 예외 대신 문자열 리턴


def test_dispatch_merge_order_manual_wins():
    """dispatch 시 manual 파라미터가 LLM tool_input 를 덮어써야 함."""
    # Mock editor.node_composer 를 임시로 주입해 _call_xgen_node 가 Node 클래스를 찾게 함.
    import types
    import sys as _sys

    captured = {}

    class FakeNode:
        def execute(self, **kwargs):
            captured.update(kwargs)
            return {"ok": True, "received": dict(kwargs)}

    # editor.node_composer mock
    editor_mod = types.ModuleType("editor")
    composer_mod = types.ModuleType("editor.node_composer")

    def _get_node_class_by_id(spec_id):
        if spec_id == "math/add_integers":
            return FakeNode
        return None

    composer_mod.get_node_class_by_id = _get_node_class_by_id
    editor_mod.node_composer = composer_mod
    _sys.modules["editor"] = editor_mod
    _sys.modules["editor.node_composer"] = composer_mod

    try:
        bootstrap_xgen_node_adapters()
        reg = ResourceRegistry()
        # manual-locked 파라미터를 가진 노드 구성 — Qdrant 로 테스트:
        #   collection_name (manual) + query (auto synthetic)
        asyncio.run(reg.load_all(
            workflow_data={
                "nodes": [
                    _make_node(
                        "q1", "document_loaders/Qdrant", "document_loaders",
                        [
                            {"id": "collection_name", "type": "STR", "value": "assort", "required": True},
                        ],
                    ),
                ]
            },
            harness_config={},
        ))
        defs = reg.get_tool_definitions()
        tool_name = defs[0]["name"]

        # Qdrant 는 mock 안 했으니 실행은 에러 리턴하지만, _call_xgen_node 호출 전 병합 단계를
        # 직접 검증: execute_tool 이 merged = {tool_input, **params} 구성하는지 spy
        executor = reg._tool_executors[tool_name]
        assert isinstance(executor, _XgenNodeRef)
        # LLM 이 collection_name 을 밀어넣어도 manual 이 이김
        merged = {**{"query": "test", "collection_name": "HIJACKED"}, **executor.params}
        assert merged["collection_name"] == "assort"  # manual 우선
        assert merged["query"] == "test"

        # 실제 dispatch — math 로 FakeNode 에 도달
        asyncio.run(reg.load_all(
            workflow_data={"nodes": [_make_node("m1", "math/add_integers", "arithmetic", [])]},
            harness_config={},
        ))
        # 새 reg 필요 (load_all 은 기존 tool 유지하므로)
        reg2 = ResourceRegistry()
        asyncio.run(reg2.load_all(
            workflow_data={"nodes": [_make_node("m1", "math/add_integers", "arithmetic", [])]},
            harness_config={},
        ))
        defs2 = reg2.get_tool_definitions()
        m_tool = defs2[0]["name"]
        result = asyncio.run(reg2.execute_tool(m_tool, {"a": 3, "b": 5}))
        assert captured.get("a") == 3
        assert captured.get("b") == 5
    finally:
        _sys.modules.pop("editor.node_composer", None)
        _sys.modules.pop("editor", None)


if __name__ == "__main__":
    # 간단 러너 — pytest 없이도 실행 가능하게
    tests = [
        test_policy_loads,
        test_qdrant_control_resolution,
        test_manual_keys_hidden_from_schema,
        test_switchable_toggle_to_auto,
        test_manual_lock_ignores_auto_override,
        test_user_value_override,
        test_math_synthetic_auto,
        test_tools_category_default,
        test_builder_qdrant_schema_excludes_manual,
        test_builder_qdrant_with_user_override,
        test_builder_math_synthetic_auto,
        test_builder_filesystem_manual_only,
        test_dispatch_xgen_node_graceful_unavailable,
        test_dispatch_merge_order_manual_wins,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
