"""
Capability System Phase 1+2 검증 테스트

schema / registry / matcher 기본 동작 확인.
"""

import asyncio

from xgen_harness import (
    CapabilitySpec,
    CapabilityRegistry,
    CapabilityMatcher,
    MatchStrategy,
    ParamSpec,
    ProviderKind,
    get_default_registry,
    set_default_registry,
)


def make_fixtures() -> list[CapabilitySpec]:
    return [
        CapabilitySpec(
            name="retrieval.web_search",
            category="retrieval",
            description="웹에서 최신 정보 검색 (news, articles, general web)",
            tags=["web", "search", "news", "online"],
            aliases=["웹검색", "search_web"],
            params=[
                ParamSpec("query", "str", "검색어", required=True, source_hint="user_input"),
                ParamSpec("top_k", "int", "결과 개수", required=False, default=5),
            ],
            provider_kind=ProviderKind.XGEN_NODE,
            provider_ref="web_crawler",
            estimated_cost_usd=0.002,
        ),
        CapabilitySpec(
            name="retrieval.rag_search",
            category="retrieval",
            description="사내 문서 RAG 검색 — 업로드된 문서 컬렉션에서 관련 청크 찾기",
            tags=["rag", "document", "internal", "vector"],
            aliases=["문서검색"],
            params=[
                ParamSpec("query", "str", "검색 질의", required=True),
                ParamSpec("collection", "str", "대상 컬렉션", required=True),
                ParamSpec("top_k", "int", "결과 청크 수", required=False, default=8),
            ],
            provider_kind=ProviderKind.RAG,
            provider_ref="xgen_documents",
        ),
        CapabilitySpec(
            name="transform.summarize",
            category="transform",
            description="긴 텍스트를 요약. PDF/문서/기사 등에 적용 가능",
            tags=["summary", "summarize", "요약", "digest"],
            aliases=["요약"],
            params=[
                ParamSpec("text", "str", "요약 대상 텍스트", required=True),
                ParamSpec("length", "str", "요약 길이", required=False, default="medium",
                         enum=["short", "medium", "long"]),
            ],
            provider_kind=ProviderKind.BUILTIN,
            provider_ref="builtin.summarize",
        ),
        CapabilitySpec(
            name="io.file_read",
            category="io",
            description="파일 시스템에서 파일 읽기 (PDF, TXT, DOCX 지원)",
            tags=["file", "read", "pdf", "docx"],
            aliases=["파일읽기"],
            params=[
                ParamSpec("file_path", "file", "파일 경로", required=True, source_hint="user_input"),
            ],
            provider_kind=ProviderKind.XGEN_NODE,
            provider_ref="document_processor",
        ),
        CapabilitySpec(
            name="generation.image",
            category="generation",
            description="텍스트 프롬프트로 이미지 생성 (DALL-E, Stable Diffusion)",
            tags=["image", "dalle", "imagegen", "이미지생성"],
            params=[
                ParamSpec("prompt", "str", "이미지 설명", required=True),
                ParamSpec("size", "str", "이미지 크기", required=False, default="1024x1024"),
            ],
            provider_kind=ProviderKind.XGEN_NODE,
            provider_ref="image_gen",
            estimated_cost_usd=0.04,
            is_read_only=False,
        ),
    ]


# ---------- Tests ----------


def test_registry_basic():
    reg = CapabilityRegistry()
    for spec in make_fixtures():
        reg.register(spec)

    assert len(reg.list_all()) == 5
    assert reg.get("retrieval.web_search") is not None
    assert reg.get("웹검색") is not None  # alias 조회
    assert reg.has("generation.image")
    print("  ✅ registry_basic — 5개 등록, alias 조회 OK")


def test_registry_indexes():
    reg = CapabilityRegistry()
    reg.register_many(make_fixtures())

    # 카테고리별
    retrieval = reg.find_by_category("retrieval")
    assert len(retrieval) == 2
    assert {s.name for s in retrieval} == {"retrieval.web_search", "retrieval.rag_search"}

    # 태그별
    by_tag = reg.find_by_tag("rag")
    assert len(by_tag) == 1 and by_tag[0].name == "retrieval.rag_search"

    # 복수 태그 (any)
    any_hit = reg.find_by_tags(["web", "pdf"], mode="any")
    assert len(any_hit) == 2

    # 복수 태그 (all)
    all_hit = reg.find_by_tags(["web", "search"], mode="all")
    assert len(all_hit) == 1

    # 제공자별
    xgen_nodes = reg.find_by_provider(ProviderKind.XGEN_NODE)
    assert len(xgen_nodes) == 3
    print(f"  ✅ registry_indexes — category/tag/provider 인덱스 OK ({reg.stats()})")


def test_registry_idempotency():
    reg = CapabilityRegistry()
    spec = make_fixtures()[0]
    reg.register(spec)
    reg.register(spec)              # 중복 무시
    assert len(reg.list_all()) == 1
    reg.register(spec, overwrite=True)
    assert len(reg.list_all()) == 1
    print("  ✅ registry_idempotency — 중복 등록 처리 OK")


def test_matcher_exact():
    reg = CapabilityRegistry()
    reg.register_many(make_fixtures())
    matcher = CapabilityMatcher(reg)

    # 이름 정확 일치
    r = matcher.match("retrieval.web_search")
    assert r and r[0].score == 1.0 and r[0].strategy == "exact_tag"

    # 태그 정확 일치
    r = matcher.match("rag")
    assert r and r[0].spec.name == "retrieval.rag_search"
    assert r[0].score >= 0.9

    # alias 일치
    r = matcher.match("웹검색")
    assert r and r[0].spec.name == "retrieval.web_search"

    print("  ✅ matcher_exact — 이름/태그/alias 정확 매칭 OK")


def test_matcher_keyword():
    reg = CapabilityRegistry()
    reg.register_many(make_fixtures())
    matcher = CapabilityMatcher(reg, min_score=0.2)

    # "뉴스 찾아서 요약해줘" → web_search + summarize 후보
    r = matcher.match("뉴스 찾아서 요약해줘")
    names = {m.spec.name for m in r}
    assert "transform.summarize" in names or "retrieval.web_search" in names

    # "PDF 파일 읽어서" → file_read
    r = matcher.match("PDF 파일 읽어서 내용 추출")
    names = {m.spec.name for m in r}
    assert "io.file_read" in names

    # "이미지 생성해줘" → image gen
    r = matcher.match("이미지 생성해줘")
    names = {m.spec.name for m in r}
    assert "generation.image" in names

    print(f"  ✅ matcher_keyword — 한/영 자연어 매칭 OK")


async def test_matcher_llm_fallback():
    reg = CapabilityRegistry()
    reg.register_many(make_fixtures())

    # fake LLM — "번역" intent에 대해 web_search를 0.85로 찍어준다고 가정
    async def fake_llm(intent, specs):
        # intent에 "내부" 들어있으면 rag를 최고점으로
        if "내부" in intent or "사내" in intent:
            return [("retrieval.rag_search", 0.95)]
        return []

    matcher = CapabilityMatcher(reg, llm_fn=fake_llm)
    r = await matcher.amatch("사내 자료에서 보안 정책 찾아줘")
    assert r and r[0].spec.name == "retrieval.rag_search"
    assert r[0].strategy == "llm"
    print("  ✅ matcher_llm_fallback — LLM fallback 재랭킹 OK")


def test_default_registry_singleton():
    reg1 = get_default_registry()
    reg2 = get_default_registry()
    assert reg1 is reg2

    reg1.clear()
    reg1.register(make_fixtures()[0])
    assert reg2.has("retrieval.web_search")

    # 교체 테스트
    new_reg = CapabilityRegistry()
    set_default_registry(new_reg)
    assert get_default_registry() is new_reg
    # 원복
    set_default_registry(reg1)

    print("  ✅ default_registry — 싱글톤 + 교체 OK")


def test_serialize():
    spec = make_fixtures()[0]
    d = spec.to_dict()
    assert d["name"] == "retrieval.web_search"
    assert d["provider_kind"] == "xgen_node"
    assert len(d["params"]) == 2
    assert d["params"][0]["required"] is True
    print("  ✅ serialize — to_dict() OK")


def test_param_spec():
    spec = make_fixtures()[0]
    required = spec.required_params()
    optional = spec.optional_params()
    assert len(required) == 1 and required[0].name == "query"
    assert len(optional) == 1 and optional[0].default == 5
    print("  ✅ param_spec — required/optional 분리 OK")


# ---------- 런너 ----------


def run_sync_tests():
    tests = [
        test_registry_basic,
        test_registry_indexes,
        test_registry_idempotency,
        test_matcher_exact,
        test_matcher_keyword,
        test_default_registry_singleton,
        test_serialize,
        test_param_spec,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"  ❌ {t.__name__} — {e}")
            failed += 1
        except Exception as e:
            print(f"  💥 {t.__name__} — {type(e).__name__}: {e}")
            failed += 1
    return failed


async def run_async_tests():
    tests = [test_matcher_llm_fallback]
    failed = 0
    for t in tests:
        try:
            await t()
        except AssertionError as e:
            print(f"  ❌ {t.__name__} — {e}")
            failed += 1
        except Exception as e:
            print(f"  💥 {t.__name__} — {type(e).__name__}: {e}")
            failed += 1
    return failed


if __name__ == "__main__":
    print("=" * 60)
    print("Capability System Phase 1+2 테스트")
    print("=" * 60)

    sync_failed = run_sync_tests()
    async_failed = asyncio.run(run_async_tests())

    total_failed = sync_failed + async_failed
    print("=" * 60)
    if total_failed == 0:
        print("🎉 전부 통과!")
    else:
        print(f"❌ 실패 {total_failed}건")
    print("=" * 60)
