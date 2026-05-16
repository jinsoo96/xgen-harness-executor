/**
 * PD (Progressive Disclosure) builtin tools — TypeScript 이식.
 *
 * Python `xgen_harness/tools/builtin.py` 의 SearchToolsTool / DiscoverToolsTool 과
 * 동등 동작. spec.tool_definitions 가 커도 LLM 이 키워드 기반 자율 탐색 가능.
 *
 * call_kind 컨벤션:
 *   - "builtin:search_tools"     — 키워드 검색 + scoring + 카테고리 hint
 *   - "builtin:discover_tools"   — 메타 list / 상세 schema
 *
 * dispatch.ts 가 이 prefix 를 감지하면 spec.call_spec 무시하고 자체 실행.
 * state.tool_definitions 전체 카탈로그를 참조 — 다른 도구 메타 탐색.
 */

import type { FrozenToolDefinition } from "../spec/schema";

export const BUILTIN_SEARCH_TOOLS_NAME = "search_tools";
export const BUILTIN_DISCOVER_TOOLS_NAME = "discover_tools";

/** spec.tool_definitions 에 자동 합류시킬 PD builtin 정의 2 개. */
export function buildPdBuiltinDefinitions(): FrozenToolDefinition[] {
  return [
    {
      name: BUILTIN_SEARCH_TOOLS_NAME,
      description:
        "Search the tool catalog by keyword (full-text scoring on name/description).",
      input_schema: {
        type: "object",
        properties: {
          query: {
            type: "string",
            description: "Keyword(s) to search in tool name/description.",
          },
          limit: {
            type: "integer",
            description: "Max results (default 8).",
            default: 8,
          },
          category: {
            type: "string",
            description: "Optional category filter (substring match on tag).",
          },
        },
        required: ["query"],
      },
      call_kind: "builtin:search_tools",
      call_spec: {},
      annotations: { read_only_hint: true, idempotent_hint: true },
      tags: ["builtin", "pd"],
    },
    {
      name: BUILTIN_DISCOVER_TOOLS_NAME,
      description:
        "List all tools (names + short descriptions) or inspect one tool's full " +
        "input_schema (tool_name='X'). Omit tool_name for catalog listing.",
      input_schema: {
        type: "object",
        properties: {
          tool_name: {
            type: "string",
            description: "Name of the tool to get details for. Omit to list all.",
          },
        },
      },
      call_kind: "builtin:discover_tools",
      call_spec: {},
      annotations: { read_only_hint: true, idempotent_hint: true },
      tags: ["builtin", "pd"],
    },
  ];
}

export interface BuiltinDispatchResult {
  content: string;
  is_error: boolean;
}

export async function dispatchBuiltinSearchTools(
  args: Record<string, unknown>,
  catalog: FrozenToolDefinition[],
): Promise<BuiltinDispatchResult> {
  const q = String(args.query || "").trim().toLowerCase();
  const limit = Number(args.limit || 8);
  const categoryFilter = String(args.category || "").trim().toLowerCase();
  if (!q) {
    return { content: "'query' is required.", is_error: true };
  }
  const terms = q.split(/\s+/).filter((t) => t);
  const scored: Array<[number, FrozenToolDefinition]> = [];
  for (const td of catalog) {
    if (td.name === BUILTIN_SEARCH_TOOLS_NAME || td.name === BUILTIN_DISCOVER_TOOLS_NAME) {
      continue; // builtin 자기 자신은 결과에서 제외
    }
    const name = (td.name || "").toLowerCase();
    const desc = (td.description || "").toLowerCase();
    const tagStr = (td.tags || []).join(" ").toLowerCase();
    if (categoryFilter && !tagStr.includes(categoryFilter)) continue;
    let score = 0;
    for (const t of terms) {
      if (name.includes(t)) score += 3;
      if (desc.includes(t)) score += 1;
      if (name === t) score += 5;
    }
    if (score > 0) scored.push([score, td]);
  }
  scored.sort((a, b) => b[0] - a[0]);
  const top = scored.slice(0, limit);
  if (top.length === 0) {
    return {
      content:
        `No match for '${q}'. ` +
        `Try discover_tools() for full catalog of ${catalog.length - 2} tools.`,
      is_error: false,
    };
  }
  const lines: string[] = [`Matched ${top.length} of ${scored.length} tools:`];
  for (const [s, td] of top) {
    const d = (td.description || "").slice(0, 120);
    lines.push(`- ${td.name} (score=${s}): ${d}`);
  }
  // v1.11.4 — PD 정신: 결과 자체가 환경 노출. "직접 호출하라 / 먼저 inspect 하라"
  // 다음 행동 안내 폐기. 활용 방식은 LLM 자율.
  return { content: lines.join("\n"), is_error: false };
}

export async function dispatchBuiltinDiscoverTools(
  args: Record<string, unknown>,
  catalog: FrozenToolDefinition[],
): Promise<BuiltinDispatchResult> {
  const toolName = String(args.tool_name || "").trim();
  if (!toolName) {
    const lines: string[] = [];
    for (const td of catalog) {
      if (td.name === BUILTIN_SEARCH_TOOLS_NAME || td.name === BUILTIN_DISCOVER_TOOLS_NAME) continue;
      const d = (td.description || "").slice(0, 100);
      lines.push(`- ${td.name}: ${d}`);
    }
    return {
      content: lines.length ? lines.join("\n") : "No tools available.",
      is_error: false,
    };
  }
  const td = catalog.find((t) => t.name === toolName);
  if (!td) {
    return { content: `Tool '${toolName}' not found.`, is_error: true };
  }
  return {
    content: JSON.stringify(td, null, 2),
    is_error: false,
  };
}
