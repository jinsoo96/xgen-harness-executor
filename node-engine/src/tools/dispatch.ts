/**
 * Tool dispatch — frozen tool 의 call_kind 별 실행.
 *
 * call_kind 매트릭스:
 *   - http        : fetch(url, ...) — Tavily/Brave/Naver 등 직접 외부 API
 *   - mcp_session : station_url + session_id 로 stdio MCP proxy 호출
 *   - rag         : rag_endpoint 로 검색 (spec.metadata.rag_endpoint)
 *   - noop        : 미구현 — content="(noop)"
 *
 * publish 시 spec freeze — Python NodeClass / langchain Tool 의존성 0.
 */

import type { FrozenToolDefinition } from "../spec/schema";
import type { PipelineState } from "../types";
import {
  dispatchBuiltinSearchTools,
  dispatchBuiltinDiscoverTools,
} from "./builtins";

export interface ToolDispatchResult {
  content: string;
  is_error: boolean;
}

export async function dispatchToolCall(
  def: FrozenToolDefinition,
  args: Record<string, unknown>,
  state: PipelineState,
): Promise<ToolDispatchResult> {
  switch (def.call_kind) {
    case "http":
      return dispatchHttp(def, args);
    case "mcp_session":
      return dispatchMcpSession(def, args, state);
    case "rag":
      return dispatchRag(def, args, state);
    case "builtin:search_tools":
      return dispatchBuiltinSearchTools(args, state.tool_definitions || []);
    case "builtin:discover_tools":
      return dispatchBuiltinDiscoverTools(args, state.tool_definitions || []);
    case "noop":
    default:
      return { content: "(noop)", is_error: false };
  }
}

/**
 * 외부 자족 dispatch — call_spec.url 이 직접 외부 API URL.
 *
 * call_spec 구조 (v0.29+):
 *   - url           : 외부 API endpoint (cluster bridge 가 아닌 진짜 외부 URL)
 *   - method        : "GET" | "POST" | "PUT" | "DELETE" | "PATCH"
 *   - headers       : 정적 헤더 (Content-Type 등)
 *   - secrets_keys  : ENV 키 list — process.env[key] 를 동명 헤더로 inject
 *   - secret_header_map: { header_name: env_key } — ENV 값을 명시 헤더 이름으로 inject.
 *     예: {"X-Naver-Client-Id": "XGEN_TOOL__MCP_NAVER_NEWS_MCP__NAVER_CLIENT_ID"}.
 *     secrets_keys 와 달리 ENV 이름과 헤더 이름이 다를 때 사용.
 *   - query_template: { param_key: "{{arg_name}}" or 고정값 } — GET / query string.
 *     ``"{{name}}"`` 패턴은 args[name] 으로 치환. 치환값 없으면 항목 생략.
 *   - body_template : { field: "{{arg_name}}" or 고정값 } — POST / body.
 *     동일 ``{{name}}`` 치환. 미사용 args 도 body 에 spread (BC).
 */
async function dispatchHttp(
  def: FrozenToolDefinition,
  args: Record<string, unknown>,
): Promise<ToolDispatchResult> {
  const spec = def.call_spec || {};
  const url = (spec.url as string) || "";
  const method = ((spec.method as string) || "POST").toUpperCase();
  const headers: Record<string, string> = { ...(spec.headers as Record<string, string> || {}) };

  // 시크릿 ENV → 헤더 inject (두 패턴).
  //   1) secrets_keys: ENV 이름 = 헤더 이름 동일 (단순 패턴)
  //   2) secret_header_map: { header_name: env_key } 명시 매핑 (Naver/Tavily 등 외부 API 의
  //      고유 헤더 이름 — X-Naver-Client-Id 등)
  const secrets = (spec.secrets_keys as string[]) || [];
  for (const key of secrets) {
    const val = process.env[key];
    if (val) headers[key] = val;
  }
  const secretHeaderMap = (spec.secret_header_map as Record<string, string>) || {};
  for (const [headerName, envKey] of Object.entries(secretHeaderMap)) {
    const val = process.env[envKey];
    if (val) headers[headerName] = val;
  }

  // secret_body_map — body 의 ``__secret_<name>`` placeholder 를 ENV 값으로 치환.
  // Tavily 처럼 body 안 api_key 박는 API 패턴 지원. 헤더 인증과 별개.
  const secretBodyMap = (spec.secret_body_map as Record<string, string>) || {};
  const secretArgs: Record<string, unknown> = {};
  for (const [placeholder, envKey] of Object.entries(secretBodyMap)) {
    const val = process.env[envKey];
    if (val) secretArgs[placeholder] = val;
  }

  // query / body 템플릿 치환. secretArgs 를 args 에 합쳐서 placeholder 매칭 가능.
  const queryTmpl = (spec.query_template as Record<string, unknown>) || {};
  const bodyTmpl = (spec.body_template as Record<string, unknown>) || {};
  const renderArgs = { ...args, ...secretArgs };
  const queryParams = renderTemplate(queryTmpl, renderArgs);
  const bodyMerged: Record<string, unknown> = {
    ...renderTemplate(bodyTmpl, renderArgs),
    ...args,
  };

  if (!url) return { content: "http call_spec.url 누락", is_error: true };

  // URL path substitution — `https://api.github.com/repos/{owner}/{repo}/issues` 의
  // `{owner}` / `{repo}` 같은 placeholder 를 args 값으로 치환.
  // `path_params` 명시되면 그 키들만 path 로 (나머지는 body / query). 명시 안 되면
  // URL 안 `{name}` 모두 args 에서 매칭 시도. path 로 소비된 키는 body/query 에서 제외.
  const pathParams = (spec.path_params as string[]) || [];
  const consumedAsPath: Set<string> = new Set();
  let resolvedUrl = url;
  const pathPlaceholderRe = /\{(\w+)\}/g;
  resolvedUrl = resolvedUrl.replace(pathPlaceholderRe, (match, name) => {
    const v = (renderArgs as Record<string, unknown>)[name];
    if (v === undefined || v === null) return match;
    consumedAsPath.add(name);
    return encodeURIComponent(String(v));
  });
  if (pathParams.length > 0) {
    for (const p of pathParams) consumedAsPath.add(p);
  }
  // path 로 소비된 키는 query/body 에서 제외.
  for (const k of consumedAsPath) {
    delete queryParams[k];
    delete bodyMerged[k];
  }

  // 최종 URL — GET / DELETE / 또는 query_template 명시되면 query string 추가.
  let finalUrl = resolvedUrl;
  const hasQuery = Object.keys(queryParams).length > 0;
  if (hasQuery || method === "GET" || method === "DELETE") {
    // GET / DELETE 시 args 도 query 로 (body 안 쓰는 method) — 단 query_template 명시면 그 것만.
    let qpSource: Record<string, unknown>;
    if (hasQuery) qpSource = queryParams;
    else if (method === "GET" || method === "DELETE") {
      // path 로 소비된 키 제외한 args
      qpSource = Object.fromEntries(
        Object.entries(args).filter(([k]) => !consumedAsPath.has(k))
      );
    } else {
      qpSource = {};
    }
    const qs = buildQueryString(qpSource);
    if (qs) finalUrl = resolvedUrl + (resolvedUrl.includes("?") ? "&" : "?") + qs;
  }

  try {
    const init: RequestInit = {
      method,
      headers: { "content-type": "application/json", ...headers },
    };
    if (method !== "GET" && method !== "DELETE") {
      init.body = JSON.stringify(bodyMerged);
    }
    const resp = await fetch(finalUrl, init);
    const text = await resp.text();
    if (!resp.ok) {
      return { content: `${resp.status} ${text.slice(0, 500)}`, is_error: true };
    }
    return { content: text.slice(0, 50_000), is_error: false };
  } catch (e) {
    return { content: (e as Error).message, is_error: true };
  }
}

/**
 * "{{name}}" 패턴 치환 — args 에 매칭되는 값 있으면 그 값, 없으면 항목 제거.
 * 고정값 (string/number/bool) 은 그대로. nested dict 도 재귀.
 */
function renderTemplate(
  tmpl: Record<string, unknown>,
  args: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(tmpl)) {
    if (typeof v === "string") {
      const m = v.match(/^\{\{(\w+)\}\}$/);
      if (m) {
        const argName = m[1];
        if (argName in args && args[argName] !== undefined && args[argName] !== null) {
          out[k] = args[argName];
        }
        // 미매치는 항목 생략 (optional argument 패턴)
        continue;
      }
      out[k] = v;
    } else if (Array.isArray(v)) {
      out[k] = v;
    } else if (v && typeof v === "object") {
      out[k] = renderTemplate(v as Record<string, unknown>, args);
    } else {
      out[k] = v;
    }
  }
  return out;
}

function buildQueryString(params: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null) continue;
    if (Array.isArray(v)) {
      for (const item of v) {
        parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(item))}`);
      }
    } else {
      parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
    }
  }
  return parts.join("&");
}

async function dispatchMcpSession(
  def: FrozenToolDefinition,
  args: Record<string, unknown>,
  state: PipelineState,
): Promise<ToolDispatchResult> {
  // station URL 은 spec.metadata.station_url (publish 시점 박힘) 또는 env 로만.
  // 일반 엔진 코드에 xgen 도메인 default 박지 않음 — 외부 환경 (Claude Desktop 등)
  // 에서도 동작 가능해야 함.
  const stationUrl =
    (state.metadata.station_url as string) || process.env.MCP_STATION_BASE_URL || "";
  if (!stationUrl) {
    return {
      content:
        "MCP_STATION_BASE_URL 미설정 — spec.metadata.station_url 또는 env 필요. " +
        `tool='${def.name}' session_id='${def.call_spec.session_id}'`,
      is_error: true,
    };
  }
  const sid = (def.call_spec.session_id as string) || "";
  if (!sid) return { content: "session_id 누락", is_error: true };

  const url = `${stationUrl}/api/mcp/mcp-request`;
  const payload = {
    session_id: sid,
    method: "tools/call",
    params: { name: def.name, arguments: args },
  };
  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    const text = await resp.text();
    if (!resp.ok) {
      return { content: `station ${resp.status}: ${text.slice(0, 500)}`, is_error: true };
    }
    try {
      const data = JSON.parse(text);
      const result = data.data || data.result || {};
      const content = result.content;
      if (Array.isArray(content)) {
        const parts: string[] = [];
        for (const block of content) {
          if (block && typeof block === "object" && (block as any).type === "text") {
            parts.push((block as any).text || "");
          } else if (typeof block === "string") {
            parts.push(block);
          }
        }
        return { content: parts.join("\n") || JSON.stringify(result), is_error: false };
      }
      return { content: JSON.stringify(result).slice(0, 50_000), is_error: false };
    } catch {
      return { content: text.slice(0, 50_000), is_error: false };
    }
  } catch (e) {
    return { content: (e as Error).message, is_error: true };
  }
}

async function dispatchRag(
  def: FrozenToolDefinition,
  args: Record<string, unknown>,
  state: PipelineState,
): Promise<ToolDispatchResult> {
  const endpoint =
    (state.metadata.rag_endpoint as string) ||
    process.env.HARNESS_RAG_ENDPOINT ||
    "";
  if (!endpoint) return { content: "RAG endpoint 미설정", is_error: true };
  const spec = def.call_spec || {};
  const body = {
    collection_name: spec.collection_name,
    query: args.query,
    top_k: spec.top_k ?? 4,
    score_threshold: spec.score_threshold ?? 0.0,
  };
  try {
    const resp = await fetch(endpoint, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const text = await resp.text();
    if (!resp.ok) {
      return { content: `rag ${resp.status}: ${text.slice(0, 500)}`, is_error: true };
    }
    return { content: text.slice(0, 50_000), is_error: false };
  } catch (e) {
    return { content: (e as Error).message, is_error: true };
  }
}
