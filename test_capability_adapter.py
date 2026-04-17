"""
Phase 6 검증 — ResourceRegistry.publish_capabilities()가 xgen 자산을
CapabilityRegistry로 자동 승격하는지 확인.

xgen-workflow DB/MCP/서비스 없이, 가짜 ServiceProvider + 워크플로우 JSON으로
api_tool / db_tool / rag_collection 경로를 검증.
"""

import asyncio

from xgen_harness import (
    CapabilityRegistry,
    ProviderKind,
    set_default_registry,
    get_default_registry,
)
from xgen_harness.adapters.resource_registry import ResourceRegistry, ResourceInfo
from xgen_harness.core.services import NullServiceProvider


# ---------- Fake workflow ----------


def fake_workflow_with_api_and_db() -> dict:
    return {
        "nodes": [
            {
                "id": "n1",
                "data": {
                    "functionId": "api_calling_tool",
                    "parameters": [
                        {"id": "tool_name", "value": "get_weather"},
                        {"id": "api_endpoint", "value": "https://api.weather.test/query"},
                        {"id": "method", "value": "GET"},
                        {"id": "description", "value": "도시 이름으로 현재 날씨 조회"},
                        {"id": "input_schema", "value": {
                            "type": "object",
                            "properties": {
                                "city": {"type": "string", "description": "도시 이름"},
                                "units": {"type": "string", "default": "metric",
                                          "enum": ["metric", "imperial"]},
                            },
                            "required": ["city"],
                        }},
                    ],
                },
            },
            {
                "id": "n2",
                "data": {
                    "functionId": "postgresql_query",
                    "parameters": [
                        {"id": "tool_name", "value": "lookup_user"},
                        {"id": "connection_id", "value": "main_db"},
                        {"id": "description", "value": "유저 ID로 프로필 조회"},
                    ],
                },
            },
        ]
    }


# ---------- 테스트 ----------


async def test_publish_api_and_db():
    set_default_registry(CapabilityRegistry())
    try:
        res = ResourceRegistry(NullServiceProvider())
        await res.load_all(fake_workflow_with_api_and_db(), {})

        cap_reg = get_default_registry()
        count = res.publish_capabilities()

        assert count >= 2, f"published={count}"

        weather = cap_reg.get("api.get_weather")
        assert weather is not None
        assert weather.provider_kind == ProviderKind.API
        assert weather.provider_ref == "https://api.weather.test/query"
        assert weather.tool_factory is not None

        # ParamSpec 자동 변환 확인
        names = {p.name for p in weather.params}
        assert {"city", "units"} <= names
        city_spec = next(p for p in weather.params if p.name == "city")
        assert city_spec.required is True
        units_spec = next(p for p in weather.params if p.name == "units")
        assert units_spec.enum == ["metric", "imperial"]
        assert units_spec.default == "metric"

        # DB도구
        db_cap = cap_reg.get("db.lookup_user")
        assert db_cap is not None
        assert db_cap.provider_kind == ProviderKind.DB

        # factory로 Tool 인스턴스 생성 가능
        tool = weather.tool_factory({})
        assert tool.name == "get_weather"
        assert "city" in tool.input_schema.get("properties", {})

        print(f"  ✅ publish_api_and_db — {count} capabilities (api={weather.name}, db={db_cap.name})")
    finally:
        set_default_registry(CapabilityRegistry())


async def test_publish_rag_collections():
    """RAG 컬렉션이 capability로 발행되는지 — ResourceInfo 직접 주입"""
    set_default_registry(CapabilityRegistry())
    try:
        res = ResourceRegistry(NullServiceProvider())
        # load_all 우회 — RAG 컬렉션 직접 추가
        res._rag_collections.append(
            ResourceInfo(
                resource_type="rag_collection",
                name="security_docs",
                description="보안 정책 문서 컬렉션",
                source="xgen_documents",
            )
        )

        count = res.publish_capabilities()
        assert count == 1

        cap = get_default_registry().get("retrieval.rag_security_docs")
        assert cap is not None
        assert cap.provider_kind == ProviderKind.RAG
        assert "rag" in cap.tags

        # ParamSpec 자동 구성 (query, top_k)
        names = {p.name for p in cap.params}
        assert names == {"query", "top_k"}

        query_spec = next(p for p in cap.params if p.name == "query")
        assert query_spec.required is True
        assert query_spec.source_hint == "user_input"

        print(f"  ✅ publish_rag_collections — {cap.name} + 자동 params")
    finally:
        set_default_registry(CapabilityRegistry())


async def test_publish_idempotent_overwrite():
    """두 번 발행해도 동일 이름은 덮어쓰기만 됨 (중복 등록 아님)"""
    set_default_registry(CapabilityRegistry())
    try:
        res = ResourceRegistry(NullServiceProvider())
        await res.load_all(fake_workflow_with_api_and_db(), {})

        n1 = res.publish_capabilities()
        n2 = res.publish_capabilities()  # 재호출

        reg = get_default_registry()
        assert n1 == n2
        # 중복 등록되지 않음
        assert len(reg.list_all()) == n1
        print(f"  ✅ publish_idempotent_overwrite — {n1} (재호출 후에도 동일)")
    finally:
        set_default_registry(CapabilityRegistry())


async def test_capability_materialize_after_publish():
    """발행된 capability를 materialize하면 Tool 인스턴스 나옴"""
    from xgen_harness.capabilities import materialize_capabilities

    set_default_registry(CapabilityRegistry())
    try:
        res = ResourceRegistry(NullServiceProvider())
        await res.load_all(fake_workflow_with_api_and_db(), {})
        res.publish_capabilities()

        report = materialize_capabilities(["api.get_weather", "db.lookup_user"])
        assert len(report.tools) == 2
        assert not report.unknown and not report.no_factory
        tool_names = {t.name for t in report.tools}
        assert tool_names == {"get_weather", "lookup_user"}
        print(f"  ✅ capability_materialize_after_publish — {report.summary()}")
    finally:
        set_default_registry(CapabilityRegistry())


async def test_rag_tool_executes_without_service():
    """RAG 도구 실행 경로 — service 없으면 빈 문자열, 에러 아님"""
    set_default_registry(CapabilityRegistry())
    try:
        res = ResourceRegistry(NullServiceProvider())
        res._rag_collections.append(
            ResourceInfo(resource_type="rag_collection", name="docs", description="")
        )
        res.publish_capabilities()

        cap = get_default_registry().get("retrieval.rag_docs")
        tool = cap.tool_factory({"top_k": 3})
        result = await tool.execute({"query": "test"})
        assert result.is_error is False
        # NullServiceProvider라 결과는 "(no results)"
        assert "no results" in result.content.lower() or result.content == "(no results)"
        print(f"  ✅ rag_tool_executes_without_service — {result.content[:50]}")
    finally:
        set_default_registry(CapabilityRegistry())


# ---------- 런너 ----------


async def run():
    tests = [
        test_publish_api_and_db,
        test_publish_rag_collections,
        test_publish_idempotent_overwrite,
        test_capability_materialize_after_publish,
        test_rag_tool_executes_without_service,
    ]
    failed = 0
    for t in tests:
        try:
            await t()
        except AssertionError as e:
            print(f"  ❌ {t.__name__} — {e}")
            failed += 1
        except Exception as e:
            print(f"  💥 {t.__name__} — {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()
            failed += 1
    return failed


if __name__ == "__main__":
    print("=" * 60)
    print("Capability System Phase 6 — Adapter 자동 발행 테스트")
    print("=" * 60)
    n = asyncio.run(run())
    print("=" * 60)
    if n == 0:
        print("🎉 Phase 6 전부 통과!")
    else:
        print(f"❌ 실패 {n}건")
    print("=" * 60)
