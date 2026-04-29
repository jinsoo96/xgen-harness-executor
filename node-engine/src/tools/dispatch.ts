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
    case "noop":
    default:
      return { content: "(noop)", is_error: false };
  }
}

async function dispatchHttp(
  def: FrozenToolDefinition,
  args: Record<string, unknown>,
): Promise<ToolDispatchResult> {
  const spec = def.call_spec || {};
  const url = (spec.url as string) || "";
  const method = ((spec.method as string) || "POST").toUpperCase();
  const headers: Record<string, string> = { ...(spec.headers as Record<string, string> || {}) };
  // secret env injection
  const secrets = (spec.secrets_keys as string[]) || [];
  for (const key of secrets) {
    const val = process.env[key];
    if (val) headers[key] = val;
  }
  // body — args 그대로 또는 body_template merge
  const tmpl = (spec.body_template as Record<string, unknown>) || {};
  const body = { ...tmpl, ...args };

  if (!url) return { content: "http call_spec.url 누락", is_error: true };

  try {
    const init: RequestInit = {
      method,
      headers: { "content-type": "application/json", ...headers },
    };
    if (method !== "GET") init.body = JSON.stringify(body);
    const resp = await fetch(url, init);
    const text = await resp.text();
    if (!resp.ok) {
      return { content: `${resp.status} ${text.slice(0, 500)}`, is_error: true };
    }
    return { content: text.slice(0, 50_000), is_error: false };
  } catch (e) {
    return { content: (e as Error).message, is_error: true };
  }
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
