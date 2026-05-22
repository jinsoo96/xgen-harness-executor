"""FrozenToolSource — 컴파일 산출물(python wheel)의 standalone 도구 실행기.

node-engine 의 ``src/tools/dispatch.ts`` 와 1:1 패리티. 같은 ``spec.tool_definitions``
(call_kind / call_spec) 를 받아 동일하게 dispatch 한다. cluster 의존 0 — 모든 외부
자원(외부 API / Qdrant / MCP 서버)을 frozen 메타 + 외부 env 만으로 직접 호출.

call_kind 매트릭스 (dispatch.ts 와 동일):
  - http        : httpx 직접 외부 API. secrets_keys / secret_header_map / secret_body_map /
                  query_template / body_template / path_params 지원.
  - rag         : QDRANT_URL env + call_spec.embedder 메타 둘 다 있으면 Qdrant + 임베더
                  직접 호출. 하나라도 없으면 metadata.rag_endpoint / HARNESS_RAG_ENDPOINT shim 폴백.
  - mcp_session : call_spec.spawn(server_command) 있으면 stdio 직접 spawn(mcp SDK).
                  없으면 station_url / MCP_STATION_BASE_URL 프록시 폴백.
  - noop        : "(noop)".

도구 호출 시점에 필요한 env 가 미설정이면 ``content`` 에 어떤 env 를 설정해야 하는지
명시 안내(``_env_missing_msg``) 를 담아 반환 — 외부 실행자가 무엇을 wire 해야 하는지 즉시 파악.

이 소스는 ``register_tool_source()`` 로 등록되며 ``s07_act`` 가 ``call_tool`` 로 dispatch.
``metadata`` 는 컴파일된 spec.metadata (rag_endpoint/station_url 등 — BYO 시 비어있음).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional
from urllib.parse import quote, urlencode

import httpx

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_MAX_CONTENT = 50_000


def _env_missing_msg(tool_name: str, missing: list[str], how: str = "") -> str:
    """도구 호출에 필요한 env 미설정 안내 — 외부 실행자가 무엇을 설정해야 하는지 명시."""
    names = ", ".join(missing)
    msg = f"도구 '{tool_name}' 실행에 필요한 환경변수가 설정되지 않았습니다: {names}."
    if how:
        msg += f" {how}"
    msg += " 외부 실행 환경(또는 MCP 클라이언트 설정의 env 항목)에 이 값을 지정한 뒤 다시 시도하세요."
    return msg


def _render_template(tmpl: dict, args: dict) -> dict:
    """``"{{name}}"`` 패턴을 args 값으로 치환. 미매치 항목은 생략(optional). dispatch.ts renderTemplate 동일."""
    out: dict = {}
    for k, v in tmpl.items():
        if isinstance(v, str):
            if len(v) >= 5 and v.startswith("{{") and v.endswith("}}"):
                arg_name = v[2:-2]
                if arg_name in args and args[arg_name] is not None:
                    out[k] = args[arg_name]
                # 미매치는 생략
                continue
            out[k] = v
        elif isinstance(v, list):
            out[k] = v
        elif isinstance(v, dict):
            out[k] = _render_template(v, args)
        else:
            out[k] = v
    return out


def _build_query_string(params: dict) -> str:
    pairs: list[tuple[str, str]] = []
    for k, v in params.items():
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            for item in v:
                pairs.append((k, str(item)))
        else:
            pairs.append((k, str(v)))
    return urlencode(pairs)


class FrozenToolSource:
    """spec.tool_definitions 를 받아 call_kind 별로 직접 dispatch 하는 ToolSource.

    ``ToolSource`` Protocol (list_tools / call_tool / has_tool) 구현.
    """

    def __init__(
        self,
        tool_definitions: list[dict],
        *,
        metadata: Optional[dict] = None,
        source_id: str = "frozen",
    ) -> None:
        self._defs: dict[str, dict] = {}
        for td in tool_definitions or []:
            if isinstance(td, dict) and td.get("name"):
                self._defs[str(td["name"])] = td
        self._metadata = dict(metadata or {})
        self.source_id = source_id
        self.display_name = "Compiled tools"

    # ── ToolSource Protocol ──────────────────────────────────────────
    async def list_tools(self, filters: Optional[dict] = None) -> list[dict]:
        out: list[dict] = []
        for name, td in self._defs.items():
            out.append({
                "name": name,
                "description": td.get("description", ""),
                "input_schema": td.get("input_schema") or {"type": "object"},
            })
        return out

    def has_tool(self, name: str) -> bool:
        return name in self._defs

    async def call_tool(self, name: str, args: dict) -> dict:
        td = self._defs.get(name)
        if td is None:
            return {"content": f"도구 '{name}' 미정의", "is_error": True}
        kind = str(td.get("call_kind") or "noop")
        args = args or {}
        try:
            if kind == "http":
                return await self._dispatch_http(td, args)
            if kind == "rag":
                return await self._dispatch_rag(td, args)
            if kind == "mcp_session":
                return await self._dispatch_mcp(td, args)
            return {"content": "(noop)", "is_error": False}
        except Exception as e:  # 방어 — 어떤 dispatch 도 파이프라인을 죽이지 않음
            logger.warning("[frozen] tool=%s dispatch 예외: %s", name, e)
            return {"content": f"{name} 실행 오류: {e}", "is_error": True}

    # ── http ─────────────────────────────────────────────────────────
    async def _dispatch_http(self, td: dict, args: dict) -> dict:
        spec = td.get("call_spec") or {}
        name = td.get("name", "")
        url = str(spec.get("url") or "")
        if not url:
            return {"content": "http call_spec.url 누락", "is_error": True}
        method = str(spec.get("method") or "POST").upper()
        headers: dict[str, str] = dict(spec.get("headers") or {})

        # 시크릿 env → 헤더/바디 inject. 미설정 env 를 모아 두었다가 호출 실패 시 안내.
        missing: list[str] = []
        for key in (spec.get("secrets_keys") or []):
            val = os.environ.get(str(key))
            if val:
                headers[str(key)] = val
            else:
                missing.append(str(key))
        for header_name, env_key in (spec.get("secret_header_map") or {}).items():
            val = os.environ.get(str(env_key))
            if val:
                headers[str(header_name)] = val
            else:
                missing.append(str(env_key))
        secret_args: dict[str, Any] = {}
        for placeholder, env_key in (spec.get("secret_body_map") or {}).items():
            val = os.environ.get(str(env_key))
            if val:
                secret_args[str(placeholder)] = val
            else:
                missing.append(str(env_key))

        render_args = {**args, **secret_args}
        query_params = _render_template(dict(spec.get("query_template") or {}), render_args)
        body_merged = {**_render_template(dict(spec.get("body_template") or {}), render_args), **args}

        # URL path placeholder 치환 ({owner}/{repo} 등). 소비된 키는 query/body 에서 제외.
        consumed: set[str] = set()

        def _sub(m):
            n = m.group(1)
            v = render_args.get(n)
            if v is None:
                return m.group(0)
            consumed.add(n)
            return quote(str(v), safe="")

        import re as _re
        resolved_url = _re.sub(r"\{(\w+)\}", _sub, url)
        for p in (spec.get("path_params") or []):
            consumed.add(str(p))
        for k in consumed:
            query_params.pop(k, None)
            body_merged.pop(k, None)

        final_url = resolved_url
        has_query = len(query_params) > 0
        if has_query or method in ("GET", "DELETE"):
            if has_query:
                qp_source = query_params
            elif method in ("GET", "DELETE"):
                qp_source = {k: v for k, v in args.items() if k not in consumed}
            else:
                qp_source = {}
            qs = _build_query_string(qp_source)
            if qs:
                final_url = resolved_url + ("&" if "?" in resolved_url else "?") + qs

        req_headers = {"content-type": "application/json", **headers}
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                if method in ("GET", "DELETE"):
                    resp = await client.request(method, final_url, headers=req_headers)
                else:
                    resp = await client.request(
                        method, final_url, headers=req_headers, content=json.dumps(body_merged),
                    )
            text = resp.text
            if resp.status_code >= 400:
                hint = ""
                if missing:
                    hint = " — " + _env_missing_msg(name, missing)
                return {"content": f"{resp.status_code} {text[:500]}{hint}", "is_error": True}
            return {"content": text[:_MAX_CONTENT], "is_error": False}
        except Exception as e:
            return {"content": str(e), "is_error": True}

    # ── rag ──────────────────────────────────────────────────────────
    async def _dispatch_rag(self, td: dict, args: dict) -> dict:
        spec = td.get("call_spec") or {}
        name = td.get("name", "")
        collection = str(spec.get("collection_name") or "")
        query = str(args.get("query") or "")
        top_k = int(spec.get("top_k") or 4)
        score_threshold = float(spec.get("score_threshold") or 0.0)

        qdrant_url_env = str(spec.get("qdrant_url_env") or "QDRANT_URL")
        qdrant_url = (os.environ.get(qdrant_url_env) or "").strip()
        embedder_meta = spec.get("embedder")

        # 1) 외부 자족 — QDRANT_URL + embedder 메타 둘 다 있으면 Qdrant 직접
        if qdrant_url and isinstance(embedder_meta, dict):
            try:
                from ..adapters.embedders import build_embedder
                embed = build_embedder(embedder_meta)
                vector = await embed(query)
                qdrant_api_key_env = str(spec.get("qdrant_api_key_env") or "QDRANT_API_KEY")
                qdrant_api_key = (os.environ.get(qdrant_api_key_env) or "").strip()
                result = await self._qdrant_search(
                    qdrant_url, qdrant_api_key, collection, vector, top_k, score_threshold,
                )
                return {"content": json.dumps(result, ensure_ascii=False)[:_MAX_CONTENT], "is_error": False}
            except Exception as e:
                direct_err = str(e)
                fb = await self._rag_via_shim(spec, query, name)
                if not fb["is_error"]:
                    return fb
                return {
                    "content": f"rag direct failed ({direct_err}); shim fallback failed ({fb['content']})",
                    "is_error": True,
                }

        # 2) shim 폴백
        return await self._rag_via_shim(spec, query, name)

    async def _rag_via_shim(self, spec: dict, query: str, tool_name: str) -> dict:
        endpoint = (
            str(self._metadata.get("rag_endpoint") or "")
            or os.environ.get("HARNESS_RAG_ENDPOINT")
            or ""
        )
        if not endpoint:
            qdrant_url_env = str(spec.get("qdrant_url_env") or "QDRANT_URL")
            return {
                "content": _env_missing_msg(
                    tool_name, [qdrant_url_env, "HARNESS_RAG_ENDPOINT"],
                    how="RAG 검색은 (a) 외부 Qdrant 직접 호출용 "
                        f"{qdrant_url_env}(+임베더 키) 또는 (b) RAG 검색 endpoint "
                        "HARNESS_RAG_ENDPOINT 중 하나가 필요합니다.",
                ),
                "is_error": True,
            }
        body = {
            "collection_name": spec.get("collection_name"),
            "query": query,
            "top_k": spec.get("top_k", 4),
            "score_threshold": spec.get("score_threshold", 0.0),
        }
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.post(
                    endpoint, headers={"content-type": "application/json"}, content=json.dumps(body),
                )
            text = resp.text
            if resp.status_code >= 400:
                return {"content": f"rag {resp.status_code}: {text[:500]}", "is_error": True}
            return {"content": text[:_MAX_CONTENT], "is_error": False}
        except Exception as e:
            return {"content": str(e), "is_error": True}

    async def _qdrant_search(
        self, qdrant_url: str, qdrant_api_key: str, collection: str,
        vector: list[float], top_k: int, score_threshold: float,
    ) -> list[dict]:
        url = f"{qdrant_url.rstrip('/')}/collections/{quote(collection, safe='')}/points/search"
        headers = {"content-type": "application/json"}
        if qdrant_api_key:
            headers["api-key"] = qdrant_api_key
        body: dict[str, Any] = {"vector": vector, "limit": top_k, "with_payload": True}
        if score_threshold > 0:
            body["score_threshold"] = score_threshold
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            resp = await client.post(url, headers=headers, content=json.dumps(body))
        if resp.status_code >= 400:
            raise RuntimeError(f"qdrant search {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        return [
            {"id": r.get("id"), "score": r.get("score"), "payload": r.get("payload") or {}}
            for r in (data.get("result") or [])
        ]

    # ── mcp_session ──────────────────────────────────────────────────
    async def _dispatch_mcp(self, td: dict, args: dict) -> dict:
        spec = td.get("call_spec") or {}
        name = td.get("name", "")
        sid = str(spec.get("session_id") or "")
        if not sid:
            return {"content": "session_id 누락", "is_error": True}

        # 1) 외부 자족 — spawn.server_command 있으면 stdio 직접 spawn
        spawn = spec.get("spawn")
        if isinstance(spawn, dict) and spawn.get("server_command"):
            # env_keys 미설정 선검사 → 명시 안내
            missing = [
                str(k) for k in (spawn.get("env_keys") or [])
                if not os.environ.get(str(k))
            ]
            if missing:
                return {
                    "content": _env_missing_msg(
                        name, missing,
                        how=f"MCP 서버 '{spawn.get('server_command')}' 구동에 필요한 값입니다.",
                    ),
                    "is_error": True,
                }
            try:
                return await self._mcp_via_spawn(name, args, spawn)
            except Exception as e:
                direct_err = str(e)
                station_url = (
                    str(self._metadata.get("station_url") or "")
                    or os.environ.get("MCP_STATION_BASE_URL") or ""
                )
                if station_url:
                    fb = await self._mcp_via_station(name, sid, args, station_url)
                    if not fb["is_error"]:
                        return fb
                    return {"content": f"mcp spawn failed ({direct_err}); station fallback failed ({fb['content']})", "is_error": True}
                return {"content": f"mcp spawn failed: {direct_err}", "is_error": True}

        # 2) station proxy 폴백
        station_url = (
            str(self._metadata.get("station_url") or "")
            or os.environ.get("MCP_STATION_BASE_URL") or ""
        )
        if not station_url:
            return {
                "content": _env_missing_msg(
                    name, ["MCP_STATION_BASE_URL"],
                    how="MCP 도구는 (a) freeze 시 박힌 stdio spawn 메타 또는 "
                        "(b) MCP Station 프록시 주소 MCP_STATION_BASE_URL 가 필요합니다.",
                ),
                "is_error": True,
            }
        return await self._mcp_via_station(name, sid, args, station_url)

    async def _mcp_via_station(self, tool_name: str, sid: str, args: dict, station_url: str) -> dict:
        url = f"{station_url.rstrip('/')}/api/mcp/mcp-request"
        payload = {"session_id": sid, "method": "tools/call", "params": {"name": tool_name, "arguments": args}}
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.post(url, headers={"content-type": "application/json"}, content=json.dumps(payload))
            text = resp.text
            if resp.status_code >= 400:
                return {"content": f"station {resp.status_code}: {text[:500]}", "is_error": True}
            try:
                data = json.loads(text)
                result = data.get("data") or data.get("result") or {}
                return {"content": _flatten_mcp_content(result), "is_error": False}
            except Exception:
                return {"content": text[:_MAX_CONTENT], "is_error": False}
        except Exception as e:
            return {"content": str(e), "is_error": True}

    async def _mcp_via_spawn(self, tool_name: str, args: dict, spawn: dict) -> dict:
        """mcp python SDK (stdio) 로 직접 서버 구동 + 도구 호출.

        호출 단위 spawn(open→call→close) — 단순/정확. 다회 호출 성능 최적화는 후속.
        """
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        command = str(spawn.get("server_command") or "")
        server_args = [str(a) for a in (spawn.get("server_args") or [])]
        # env_keys 만 child 에 전달 + PATH 류 기본 환경 보존
        child_env = {k: v for k, v in os.environ.items() if isinstance(v, str)}
        params = StdioServerParameters(
            command=command, args=server_args, env=child_env,
            cwd=str(spawn["working_dir"]) if spawn.get("working_dir") else None,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments=args)
        content = getattr(result, "content", None)
        is_error = bool(getattr(result, "isError", False))
        return {"content": _flatten_mcp_content({"content": content}), "is_error": is_error}


def _flatten_mcp_content(result: dict) -> str:
    """MCP tools/call 결과의 content 블록 배열 → 평문. dispatch.ts 동일 로직."""
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            text = getattr(block, "text", None)
            if text is not None:
                parts.append(str(text))
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        if parts:
            return "\n".join(parts)
    try:
        return json.dumps(result, ensure_ascii=False, default=str)[:_MAX_CONTENT]
    except Exception:
        return str(result)[:_MAX_CONTENT]
