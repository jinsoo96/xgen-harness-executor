/**
 * MCP stdio server — `@modelcontextprotocol/sdk` 사용.
 *
 * spec.gallery_name 을 도구 이름으로, run_workflow 가 본문 입력 받아 pipeline
 * 실행. 외부 MCP 클라이언트 (Claude Desktop, mcp-station, Cursor) 가 stdio 로
 * 호출.
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { runPipeline } from "../pipeline/pipeline";
import type { HarnessSpec } from "../spec/schema";

export async function serveMcp(spec: HarnessSpec): Promise<void> {
  const galleryName = spec.gallery_name;
  // 노출 도구: 1개 — 이 워크플로우 자체를 실행하는 메인 도구.
  // 추가 도구 (해석된 spec.tool_definitions) 는 외부에서 직접 호출하기보다
  // 이 메인 도구의 input 으로 처리되도록 함 — fully equivalent capsule 패턴.
  const toolName = `run_${sanitize(galleryName)}`;
  const toolDescription =
    `Run the harness workflow "${galleryName}". ` +
    `It uses the workflow's stored stage settings (system_prompt, ` +
    `selected_tools, strategies, etc.) as-is — input/output capsule.`;

  const server = new Server(
    {
      name: `xgen-harness-${galleryName}`,
      version: spec.gallery_version,
    },
    {
      capabilities: {
        tools: {},
      },
    },
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => {
    return {
      tools: [
        {
          name: toolName,
          description: toolDescription,
          inputSchema: {
            type: "object",
            properties: {
              input: {
                type: "string",
                description: "The user input / question to run through the harness.",
              },
            },
            required: ["input"],
          },
        },
      ],
    };
  });

  server.setRequestHandler(CallToolRequestSchema, async (req) => {
    const args = (req.params.arguments || {}) as { input?: string };
    const input = args.input || "";
    if (req.params.name !== toolName) {
      return {
        isError: true,
        content: [{ type: "text", text: `unknown tool: ${req.params.name}` }],
      };
    }
    // cluster_url 박혀있으면 cluster API proxy 모드 — 자체 Pipeline 실행 X.
    // 사용자 결정 (2026-05-12): "node-engine = cluster API proxy 로 채움".
    // cluster 가 환경 100% 재현 (Python Pipeline) 한 결과를 그대로 forward.
    // env XGEN_CLUSTER_URL override 가능 (외부 사용자가 자기 cluster URL 박을 때).
    const clusterMeta = (spec.metadata as Record<string, unknown> | undefined) || {};
    const clusterUrl =
      (process.env.XGEN_CLUSTER_URL || "").trim() ||
      (typeof clusterMeta.cluster_url === "string" ? clusterMeta.cluster_url.trim() : "");
    if (clusterUrl) {
      try {
        const result = await proxyToCluster(clusterUrl, toolName, input);
        return result;
      } catch (e) {
        return {
          isError: true,
          content: [
            { type: "text", text: `cluster proxy error: ${(e as Error).message}` },
          ],
        };
      }
    }
    // cluster_url 없음 — 자체 node-engine Pipeline (일부 stage 미구현).
    // 외부 standalone 시나리오 (cluster 안 띄운 환경).
    try {
      const result = await runPipeline(spec, input);
      return {
        content: [
          {
            type: "text",
            text: result.output || JSON.stringify(result.metrics, null, 2),
          },
        ],
      };
    } catch (e) {
      return {
        isError: true,
        content: [{ type: "text", text: `harness error: ${(e as Error).message}` }],
      };
    }
  });

  const transport = new StdioServerTransport();
  await server.connect(transport);
}

/**
 * proxyToCluster — node-engine 이 자체 Pipeline 실행 안 하고, cluster 의 Harness
 * MCP gateway 로 forward. 환경 100% 재현 (Python Pipeline) + 자료 (RAG/MCP/DB) 가
 * cluster 안에 그대로 있어 외부 환경 의존 0.
 *
 * 호출:
 *   POST {clusterUrl}/api/agentflow/harness/mcp
 *   JSON-RPC tools/call run_<workflow>(input=...)
 *   요구 헤더: X-User-ID / X-User-Name (env XGEN_USER_ID / XGEN_USER_NAME)
 *     또는 Authorization (Bearer 토큰 — env XGEN_AUTH_TOKEN)
 */
async function proxyToCluster(
  clusterUrl: string,
  toolName: string,
  input: string,
): Promise<{ content: { type: string; text: string }[]; isError?: boolean }> {
  // clusterUrl 이 base 또는 full endpoint 둘 다 허용.
  // base = http://host:port (자동으로 /api/agentflow/harness/mcp 박음)
  // full = http://host:port/api/agentflow/harness/mcp (그대로)
  let endpoint = clusterUrl;
  if (!/\/api\/agentflow\/harness\/mcp\/?$/.test(endpoint)) {
    endpoint = endpoint.replace(/\/$/, "") + "/api/agentflow/harness/mcp";
  }

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "application/json",
  };
  const userId = (process.env.XGEN_USER_ID || "").trim();
  const userName = (process.env.XGEN_USER_NAME || "").trim();
  const authToken = (process.env.XGEN_AUTH_TOKEN || "").trim();
  if (userId) headers["X-User-ID"] = userId;
  if (userName) headers["X-User-Name"] = userName;
  if (authToken) headers["Authorization"] = `Bearer ${authToken}`;

  const body = {
    jsonrpc: "2.0",
    id: 1,
    method: "tools/call",
    params: { name: toolName, arguments: { input } },
  };

  const resp = await fetch(endpoint, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`cluster ${resp.status}: ${text.slice(0, 200)}`);
  }
  const data = (await resp.json()) as {
    error?: { code: number; message: string };
    result?: {
      content: { type: string; text: string }[];
      isError?: boolean;
    };
  };
  if (data.error) {
    return {
      isError: true,
      content: [{ type: "text", text: `${data.error.code}: ${data.error.message}` }],
    };
  }
  if (data.result) {
    return data.result;
  }
  return {
    isError: true,
    content: [{ type: "text", text: "empty response from cluster" }],
  };
}

function sanitize(name: string): string {
  return (name || "harness").toLowerCase().replace(/[^a-z0-9_]+/g, "_");
}
