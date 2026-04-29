/**
 * Anthropic provider — `@anthropic-ai/sdk` streaming.
 *
 * Python `xgen_harness.providers.anthropic` 와 동일 흐름:
 *   - tool_use 는 first-class
 *   - thinking 지원
 *   - stream chunk 마다 ProviderEvent emit
 */

import Anthropic from "@anthropic-ai/sdk";
import type { ChatRequest, LLMProvider } from "./base";
import type { ProviderEvent, Message } from "../types";
import type { FrozenToolDefinition } from "../spec/schema";

export class AnthropicProvider implements LLMProvider {
  readonly providerName = "anthropic";
  readonly modelName: string;

  private client: Anthropic;

  constructor(opts: { apiKey: string; model: string; baseUrl?: string }) {
    this.modelName = opts.model || "claude-sonnet-4-6";
    this.client = new Anthropic({
      apiKey: opts.apiKey,
      baseURL: opts.baseUrl,
    });
  }

  supportsToolUse(): boolean {
    return true;
  }

  supportsThinking(): boolean {
    return true;
  }

  async *chat(req: ChatRequest): AsyncGenerator<ProviderEvent, void, unknown> {
    const tools = (req.tools || []).map(toAnthropicTool);
    const body: Anthropic.MessageCreateParamsStreaming = {
      model: this.modelName,
      max_tokens: req.max_tokens ?? 8192,
      temperature: req.temperature ?? 0.7,
      system: req.system || undefined,
      messages: convertMessages(req.messages),
      stream: true,
    };
    if (tools.length) (body as any).tools = tools;
    if (req.tool_choice && tools.length) {
      (body as any).tool_choice =
        typeof req.tool_choice === "string" &&
        ["auto", "any", "none"].includes(req.tool_choice)
          ? { type: req.tool_choice === "required" ? "any" : (req.tool_choice as any) }
          : { type: "tool", name: req.tool_choice };
    }
    if (req.thinking?.enabled) {
      (body as any).thinking = {
        type: "enabled",
        budget_tokens: req.thinking.budget_tokens ?? 10_000,
      };
    }

    const stream = (this.client.messages as any).stream(body);

    let currentToolUse: { id: string; name: string; inputBuf: string } | null = null;

    for await (const ev of stream) {
      const t = (ev as any).type as string;
      if (t === "content_block_start") {
        const block = (ev as any).content_block;
        if (block?.type === "tool_use") {
          currentToolUse = { id: block.id, name: block.name, inputBuf: "" };
        }
      } else if (t === "content_block_delta") {
        const d = (ev as any).delta;
        if (d?.type === "text_delta") {
          yield { type: "text_delta", text: d.text || "" };
        } else if (d?.type === "input_json_delta" && currentToolUse) {
          currentToolUse.inputBuf += d.partial_json || "";
        }
      } else if (t === "content_block_stop") {
        if (currentToolUse) {
          let parsed: Record<string, unknown> = {};
          try {
            parsed = currentToolUse.inputBuf ? JSON.parse(currentToolUse.inputBuf) : {};
          } catch {
            parsed = { raw: currentToolUse.inputBuf };
          }
          yield {
            type: "tool_use",
            tool_use_id: currentToolUse.id,
            tool_name: currentToolUse.name,
            tool_input: parsed,
          };
          currentToolUse = null;
        }
      } else if (t === "message_delta") {
        const usage = (ev as any).usage;
        if (usage) {
          yield {
            type: "usage",
            input_tokens: usage.input_tokens ?? 0,
            output_tokens: usage.output_tokens ?? 0,
            cache_creation_tokens: usage.cache_creation_input_tokens ?? 0,
            cache_read_tokens: usage.cache_read_input_tokens ?? 0,
          };
        }
        const stop_reason = (ev as any).delta?.stop_reason;
        if (stop_reason) {
          yield { type: "stop", stop_reason };
        }
      } else if (t === "message_stop") {
        // 종료. usage 은 message_delta 에서 이미 emit.
      }
    }
  }
}

function toAnthropicTool(t: FrozenToolDefinition): Anthropic.Tool {
  return {
    name: t.name,
    description: t.description,
    input_schema: t.input_schema as any,
  } as Anthropic.Tool;
}

function convertMessages(msgs: Message[]): Anthropic.MessageParam[] {
  // Python provider 와 동일 — Anthropic 은 우리 표준 그대로 사용.
  return msgs
    .filter((m) => m.role === "user" || m.role === "assistant")
    .map((m) => {
      if (typeof m.content === "string") {
        return { role: m.role as "user" | "assistant", content: m.content };
      }
      return {
        role: m.role as "user" | "assistant",
        content: m.content as any,
      };
    });
}
