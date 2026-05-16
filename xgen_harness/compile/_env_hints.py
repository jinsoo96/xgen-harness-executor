"""산출물 README 의 'Required Environment' 섹션 자동 생성 (v1.10.6+).

spec.config (또는 harness_config dict) 의 박힌 값을 보고 외부 실행자가
wire 해야 할 환경 변수를 추론. npm wrapper README 와 Python wheel README
양쪽이 같은 helper 를 호출 — 단일 진실 소스.

추론 규칙 (보수적: 모르면 박지 않음):
  - provider 값 → `{PROVIDER.upper()}_API_KEY` (openai/anthropic 만).
    vllm/google/bedrock 은 별도 인증 메커니즘이라 except.
  - rag_collections 비어있지 않음 → QDRANT_URL, QDRANT_API_KEY (옵션).
  - ontology_collections 비어있지 않음 → 동일 (Qdrant 사용 패턴).
  - mcp_sessions 비어있지 않음 → MCP_STATION_URL.
  - db_connections 비어있지 않음 → 각 connection 별 DSN 안내 (구체 값은 사용자 책임).
  - 외부 LLM endpoint 사용 시 → XGEN_HARNESS_PROVIDER / BASE_URL / MODEL.
"""

from __future__ import annotations

from typing import Any


def _provider_to_env(provider: str) -> str | None:
    """provider 식별자 → API key env 이름. 미지원/None 인증 provider 는 None."""
    p = (provider or "").strip().lower()
    mapping = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }
    return mapping.get(p)


def derive_required_envs(
    config: dict[str, Any],
    *,
    tool_definitions: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """spec.config / harness_config dict → required env 항목 리스트.

    각 항목: {"name": "OPENAI_API_KEY", "purpose": "...", "example": "sk-..."}

    Args:
        config: HarnessConfig dict (또는 spec.config).
        tool_definitions: spec.tool_definitions list. 각 항목의 ``call_spec.
            secrets_keys`` 가 비어있지 않으면 노드별 시크릿 자리를 ENV 안내로
            추가. 외부 산출물이 cluster bridge 로 forward 될 때 사용자가 채워야
            하는 자리 (예: ``XGEN_TOOL__MCP_NAVER_NEWS_MCP__NAVER_CLIENT_ID``).
    """
    out: list[dict[str, str]] = []

    # LLM API key
    provider = config.get("provider", "")
    api_env = _provider_to_env(provider)
    if api_env:
        example = "sk-..." if "openai" in (provider or "").lower() else "sk-ant-..."
        out.append({
            "name": api_env,
            "purpose": f"{provider} LLM 호출 인증",
            "example": example,
        })

    # RAG (Qdrant) — rag_collections 또는 ontology_collections 비어있지 않음
    rag = bool(config.get("rag_collections") or config.get("ontology_collections"))
    if rag:
        out.append({
            "name": "QDRANT_URL",
            "purpose": "RAG / 온톨로지 검색용 Qdrant endpoint",
            "example": "http://localhost:6333",
        })
        out.append({
            "name": "QDRANT_API_KEY",
            "purpose": "Qdrant Cloud 사용 시 (자체 호스팅 시 생략)",
            "example": "(자체 호스팅 시 생략)",
        })

    # MCP — mcp_sessions 비어있지 않음
    if config.get("mcp_sessions"):
        out.append({
            "name": "MCP_STATION_URL",
            "purpose": "spec.config.mcp_sessions 의 MCP 서버 dispatch endpoint",
            "example": "http://localhost:8030",
        })

    # DB — db_connections 비어있지 않음
    if config.get("db_connections"):
        out.append({
            "name": "DATABASE_URL",
            "purpose": "spec.config.db_connections 의 데이터베이스 연결 (DSN)",
            "example": "postgresql://user:pass@host:5432/db",
        })

    # 외부 LLM endpoint 옵션 (모든 워크플로우 해당)
    out.append({
        "name": "XGEN_HARNESS_PROVIDER",
        "purpose": "(옵션) 외부 LLM endpoint 사용 시 provider id override",
        "example": "openai",
    })
    out.append({
        "name": "XGEN_HARNESS_BASE_URL",
        "purpose": "(옵션) 외부 LLM endpoint URL — vLLM / 사내 서버 / OpenAI 호환",
        "example": "https://my-llm.example.com/v1",
    })
    out.append({
        "name": "XGEN_HARNESS_MODEL",
        "purpose": "(옵션) 모델 이름 override",
        "example": "qwen2.5-72b-instruct",
    })

    # 노드 시크릿 자리 — tool_definitions[i].call_spec 의 3 경로 모두 인입:
    #   1) secrets_keys: ENV 이름 = 헤더 이름 동일 패턴
    #   2) secret_header_map: { header_name: env_key } 명시 매핑 (Naver/Brave 등)
    #   3) secret_body_map: { body_placeholder: env_key } 명시 매핑 (Tavily 등)
    # freeze 시점에 노드별 사용자 입력 자리를 ENV 이름으로 변환해 박아둔 항목.
    # 외부 실행자가 자기 환경에 ENV 박으면 engine-node 가 헤더/바디에 inject 해
    # 외부 API 직접 호출 (cluster 의존 0). 중복 ENV 이름은 dedup.
    seen_env: set[str] = {item["name"] for item in out}

    def _add_env(env_name: str, tool_name: str) -> None:
        env_name = (env_name or "").strip()
        if not env_name or env_name in seen_env:
            return
        seen_env.add(env_name)
        param_label = ""
        if "__" in env_name:
            tail = env_name.split("__")[-1]
            param_label = tail.replace("_", " ").lower()
        purpose = (
            f"`{tool_name}` 노드의 사용자 입력 자리"
            + (f" ({param_label})" if param_label else "")
        )
        out.append({
            "name": env_name,
            "purpose": purpose,
            "example": "(자기 발급 키 입력)",
        })

    for td in tool_definitions or []:
        if not isinstance(td, dict):
            continue
        cspec = td.get("call_spec") or {}
        if not isinstance(cspec, dict):
            continue
        tool_name = td.get("name") or ""
        for k in (cspec.get("secrets_keys") or []):
            _add_env(str(k), tool_name)
        for env_name in (cspec.get("secret_header_map") or {}).values():
            _add_env(str(env_name), tool_name)
        for env_name in (cspec.get("secret_body_map") or {}).values():
            _add_env(str(env_name), tool_name)

    return out


def render_required_envs_markdown(
    config: dict[str, Any],
    *,
    header_level: int = 2,
    tool_definitions: list[dict[str, Any]] | None = None,
) -> str:
    """Markdown 섹션 문자열 (헤더 + 테이블). 항목 0 이어도 안내 줄 1 행."""
    envs = derive_required_envs(config, tool_definitions=tool_definitions)
    h = "#" * header_level
    lines = [
        f"{h} Required Environment",
        "",
        "이 워크플로우 실행 시 외부 환경에 다음 변수를 wire 하세요.",
        "(필수/옵션 구분은 워크플로우의 박힌 리소스에 따라 달라집니다.)",
        "",
        "| Variable | Purpose | Example |",
        "|---|---|---|",
    ]
    if not envs:
        lines.append("| _(자동 추론된 필수 env 없음)_ | _워크플로우에 박힌 리소스 없음_ | _-_ |")
    else:
        for e in envs:
            lines.append(f"| `{e['name']}` | {e['purpose']} | `{e['example']}` |")
    lines.append("")
    return "\n".join(lines)
