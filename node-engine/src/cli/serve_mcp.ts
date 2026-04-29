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

function sanitize(name: string): string {
  return (name || "harness").toLowerCase().replace(/[^a-z0-9_]+/g, "_");
}
