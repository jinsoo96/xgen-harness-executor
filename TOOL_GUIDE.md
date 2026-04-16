# xgen-harness 도구 개발 가이드

하네스 파이프라인에 끼울 수 있는 도구 패키지를 만드는 방법.

## 최소 요구사항

두 가지만 있으면 된다:

```python
# my_tool/__init__.py

TOOL_DEFINITIONS = [
    {
        "name": "search_documents",
        "description": "문서를 검색합니다",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색어"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
]

def call_tool(name: str, args: dict) -> dict:
    """도구 디스패처 — 이름으로 라우팅"""
    if name == "search_documents":
        return {"content": f"'{args['query']}' 검색 결과...", "is_error": False}
    return {"content": f"Unknown tool: {name}", "is_error": True}
```

이게 끝. 하네스가 자동으로 `TOOL_DEFINITIONS`를 읽고, 에이전트가 도구를 호출하면 `call_tool()`을 실행한다.

## 하네스에서 로드하기

```python
from xgen_harness.tools.gallery import load_tool_package

tools = load_tool_package("my_tool")
# → [GalleryTool("search_documents", ...)]

# 파이프라인에 바인딩
for tool in tools:
    state.tool_definitions.append(tool.to_api_format())
    state.metadata["tool_registry"][tool.name] = tool
```

## 권장 패키지 구조

```
my-tool/
├── pyproject.toml
├── my_tool/
│   ├── __init__.py        # __version__, TOOL_DEFINITIONS, call_tool
│   ├── core.py            # 비즈니스 로직 (stdlib만)
│   ├── tools.py           # TOOL_DEFINITIONS + call_tool (선택: 여기에 분리)
│   └── mcp_server.py      # MCP stdio 래퍼 (선택)
└── README.md
```

## TOOL_DEFINITIONS 스키마

```python
TOOL_DEFINITIONS = [
    {
        # 필수
        "name": "tool_name",              # 영문 소문자 + 언더스코어
        "description": "도구 설명",        # 에이전트가 읽는 설명
        "input_schema": {                  # JSON Schema
            "type": "object",
            "properties": { ... },
            "required": [ ... ],
        },

        # 선택
        "category": "search",             # 도구 카테고리 (검색, 문서, DB 등)
        "is_read_only": True,             # 읽기 전용이면 병렬 실행 가능
    },
]
```

## call_tool 규약

```python
def call_tool(name: str, args: dict) -> dict | str:
    """
    반환 형식:
    - dict: {"content": "결과 텍스트", "is_error": False}
    - str: 그냥 문자열 (is_error=False로 처리)

    async도 가능:
    async def call_tool(name: str, args: dict) -> dict:
        ...
    """
```

## 고급: ToolPackageSpec (메타데이터 포함)

```python
from xgen_harness.tools.gallery import ToolPackageSpec

def get_tool_spec() -> ToolPackageSpec:
    return ToolPackageSpec(
        name="my-tool",
        version="1.0.0",
        author="개발자 이름",
        description="도구 패키지 설명",
        tool_definitions=TOOL_DEFINITIONS,
        call_tool=call_tool,
        categories={
            "search": ["search_documents", "search_web"],
            "edit": ["edit_document"],
        },
    )
```

## 자동 발견 (entry_points)

`pyproject.toml`에 등록하면 `pip install`만으로 하네스가 자동 발견:

```toml
[project.entry-points."xgen_harness.tools"]
my_tool = "my_tool:get_tool_spec"
```

```python
from xgen_harness.tools.gallery import discover_gallery_tools

all_tools = discover_gallery_tools()
# pip install된 모든 xgen_harness.tools entry_point를 자동 로드
```

## MCP 서버 호환

MCP 서버로도 노출하려면 `mcp_server.py`를 추가:

```python
# my_tool/mcp_server.py
from my_tool import TOOL_DEFINITIONS, call_tool

# MCP stdio 프로토콜 구현
# (xgen-mcp-station이 이 서버를 subprocess로 실행)
```

실행: `python -m my_tool.mcp_server`

## 체크리스트

갤러리에 올리기 전 확인:

- [ ] `TOOL_DEFINITIONS` 리스트 존재
- [ ] `call_tool(name, args)` 함수 존재
- [ ] 각 도구의 `name`이 유니크 (패키지 내에서)
- [ ] `input_schema`가 유효한 JSON Schema
- [ ] `description`이 에이전트가 이해할 수 있는 자연어
- [ ] 에러 시 `{"content": "에러 메시지", "is_error": True}` 반환
- [ ] `pyproject.toml`에 버전, 의존성 명시
- [ ] (선택) `get_tool_spec()` → `ToolPackageSpec` 제공
- [ ] (선택) entry_points 등록

## 예시: PlateerLab 도구

```python
# document_adapter를 하네스에 로드
tools = load_tool_package("document_adapter")
# → [GalleryTool("create_docx"), GalleryTool("edit_pptx"), ...]

# synaptic_memory를 하네스에 로드
tools = load_tool_package("synaptic_memory")
# → [GalleryTool("search_knowledge"), GalleryTool("add_memory"), ...]
```
