/**
 * OpenAI / vLLM provider — `openai` npm SDK + Qwen `<tool_call>` XML 파서.
 *
 * Python `xgen_harness.providers.openai` 의 1:1 포팅 — `_parse_native_tool_call`
 * (Hermes JSON / XML parameter) 두 형식 + chunk-경계 buffer 동일 알고리즘.
 *
 * vLLM 이 `--tool-call-parser hermes` 미활성 환경에서 Qwen 이 학습된 native
 * 형식 (`<tool_call>{json}</tool_call>` 또는 `<tool_call><function=name>...
 * <parameter=k>v</parameter></function></tool_call>`) 으로 응답할 때 text content
 * 를 후처리해 ProviderEvent.tool_use 로 변환.
 */

import OpenAI from "openai";
import { randomUUID } from "node:crypto";
import type { ChatRequest, LLMProvider } from "./base";
import type { ProviderEvent, Message, ContentBlock } from "../types";
import type { FrozenToolDefinition } from "../spec/schema";

const TOOL_CALL_OPEN = "<tool_call>";
const TOOL_CALL_CLOSE = "</tool_call>";
const TOOL_CALL_OPEN_LEN = TOOL_CALL_OPEN.length;

const FUNCTION_NAME_RE = /<function=([^>\s]+)\s*>/i;
const PARAMETER_RE = /<parameter=([^>\s]+)\s*>([\s\S]*?)<\/parameter\s*>/gi;

/**
 * Hermes JSON 또는 XML parameter 형식 파싱. 실패 시 null.
 *
 * Python `_parse_native_tool_call` 와 동일 로직:
 *   1) `{` 로 시작하면 JSON parse — `{name, arguments}` 추출
 *   2) `<function=NAME>...<parameter=KEY>VAL</parameter>...</function>` 패턴
 */
export function parseNativeToolCall(
  body: string,
): { name: string; arguments: Record<string, unknown> } | null {
  const s = (body || "").trim();
  if (!s) return null;

  // 1) JSON
  if (s.startsWith("{")) {
    try {
      const obj = JSON.parse(s);
      if (obj && typeof obj === "object" && obj.name) {
        let args = obj.arguments;
        if (typeof args === "string") {
          try {
            args = JSON.parse(args);
          } catch {
            args = { raw: args };
          }
        }
        if (!args || typeof args !== "object") {
          args = args == null ? {} : { value: args };
        }
        return { name: String(obj.name), arguments: args as Record<string, unknown> };
      }
    } catch {
      // fallthrough to XML
    }
  }

  // 2) XML parameter
  const fn = s.match(FUNCTION_NAME_RE);
  if (fn) {
    const name = fn[1].trim();
    const params: Record<string, unknown> = {};
    PARAMETER_RE.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = PARAMETER_RE.exec(s)) !== null) {
      params[m[1].trim()] = m[2].trim();
    }
    return { name, arguments: params };
  }

  return null;
}

export class OpenAIProvider implements LLMProvider {
  readonly providerName: string;
  readonly modelName: string;

  private client: OpenAI;

  constructor(opts: { apiKey: string; model: string; baseUrl?: string; providerOverride?: string }) {
    this.providerName = opts.providerOverride || "openai";
    this.modelName = opts.model || "gpt-4o-mini";
    this.client = new OpenAI({
      apiKey: opts.apiKey || "sk-not-needed",
      baseURL: opts.baseUrl,
    });
  }

  supportsToolUse(): boolean {
    return true;
  }

  supportsThinking(): boolean {
    return false;
  }

  async *chat(req: ChatRequest): AsyncGenerator<ProviderEvent, void, unknown> {
    const messages = convertMessages(req.messages, req.system);
    const tools = (req.tools || []).length
      ? (req.tools || []).map(toOpenAITool)
      : undefined;

    const body: OpenAI.Chat.ChatCompletionCreateParamsStreaming = {
      model: this.modelName,
      messages,
      temperature: req.temperature ?? 0.7,
      max_tokens: req.max_tokens ?? 8192,
      stream: true,
      stream_options: { include_usage: true },
    };
    if (tools && tools.length) {
      (body as any).tools = tools;
      if (req.tool_choice) {
        if (
          typeof req.tool_choice === "string" &&
          ["auto", "required", "none"].includes(req.tool_choice)
        ) {
          (body as any).tool_choice = req.tool_choice;
        } else if (typeof req.tool_choice === "string") {
          (body as any).tool_choice = {
            type: "function",
            function: { name: req.tool_choice },
          };
        }
      }
    }

    const stream = (await this.client.chat.completions.create(body)) as any;

    // Native field tool_calls 누적 (vLLM hermes parser 활성 시 이 경로)
    const currentToolCalls = new Map<
      number,
      { id: string; name: string; arguments: string }
    >();

    // Native <tool_call> XML 텍스트 파서용 buffer (Python _stream_request 1:1)
    let textBuf = "";
    let inToolCall = false;
    let toolBuf = "";

    const flushTextSafe = (force: boolean): ProviderEvent | null => {
      if (!textBuf) return null;
      if (force) {
        const ev: ProviderEvent = { type: "text_delta", text: textBuf };
        textBuf = "";
        return ev;
      }
      if (textBuf.length <= TOOL_CALL_OPEN_LEN) return null;
      const emitChars = textBuf.slice(0, -TOOL_CALL_OPEN_LEN);
      textBuf = textBuf.slice(-TOOL_CALL_OPEN_LEN);
      return { type: "text_delta", text: emitChars };
    };

    for await (const chunk of stream) {
      const ck = chunk as OpenAI.Chat.Completions.ChatCompletionChunk;

      // usage (stream_options=include_usage 시 마지막 chunk)
      const usage = (ck as any).usage;
      if (usage) {
        yield {
          type: "usage",
          input_tokens: usage.prompt_tokens ?? 0,
          output_tokens: usage.completion_tokens ?? 0,
        };
      }

      const choices = ck.choices || [];
      if (!choices.length) continue;
      const choice = choices[0];
      const delta = choice.delta || {};
      const finishReason = choice.finish_reason;

      // text delta — native <tool_call> 감지 + 파싱
      const content = (delta as any).content as string | undefined;
      if (content) {
        if (inToolCall) {
          toolBuf += content;
          const ci = toolBuf.indexOf(TOOL_CALL_CLOSE);
          if (ci >= 0) {
            const inner = toolBuf.slice(0, ci);
            const rest = toolBuf.slice(ci + TOOL_CALL_CLOSE.length);
            const parsed = parseNativeToolCall(inner);
            if (parsed) {
              yield {
                type: "tool_use",
                tool_use_id: `native_${randomUUID().replace(/-/g, "").slice(0, 16)}`,
                tool_name: parsed.name,
                tool_input: parsed.arguments,
              };
            } else {
              yield {
                type: "text_delta",
                text: `${TOOL_CALL_OPEN}${inner}${TOOL_CALL_CLOSE}`,
              };
            }
            inToolCall = false;
            toolBuf = "";
            if (rest) textBuf += rest;
          }
          // close 미발견 — 계속 누적
        } else {
          textBuf += content;
          const oi = textBuf.indexOf(TOOL_CALL_OPEN);
          if (oi >= 0) {
            const before = textBuf.slice(0, oi);
            const after = textBuf.slice(oi + TOOL_CALL_OPEN_LEN);
            if (before) yield { type: "text_delta", text: before };
            inToolCall = true;
            toolBuf = after;
            textBuf = "";
            const ci2 = toolBuf.indexOf(TOOL_CALL_CLOSE);
            if (ci2 >= 0) {
              const inner = toolBuf.slice(0, ci2);
              const rest = toolBuf.slice(ci2 + TOOL_CALL_CLOSE.length);
              const parsed = parseNativeToolCall(inner);
              if (parsed) {
                yield {
                  type: "tool_use",
                  tool_use_id: `native_${randomUUID().replace(/-/g, "").slice(0, 16)}`,
                  tool_name: parsed.name,
                  tool_input: parsed.arguments,
                };
              } else {
                yield {
                  type: "text_delta",
                  text: `${TOOL_CALL_OPEN}${inner}${TOOL_CALL_CLOSE}`,
                };
              }
              inToolCall = false;
              toolBuf = "";
              if (rest) textBuf += rest;
            }
          } else {
            const ev = flushTextSafe(false);
            if (ev) yield ev;
          }
        }
      }

      // native field tool_calls (vLLM hermes 활성 환경)
      const tcs = (delta as any).tool_calls as Array<{
        index: number;
        id?: string;
        function?: { name?: string; arguments?: string };
      }> | undefined;
      if (tcs && tcs.length) {
        for (const tc of tcs) {
          const idx = tc.index ?? 0;
          if (!currentToolCalls.has(idx)) {
            currentToolCalls.set(idx, {
              id: tc.id || "",
              name: tc.function?.name || "",
              arguments: "",
            });
          } else {
            const cur = currentToolCalls.get(idx)!;
            cur.arguments += tc.function?.arguments || "";
          }
        }
      }

      if (finishReason) {
        // 미완 tool_call buffer flush
        if (inToolCall && toolBuf) {
          const parsed = parseNativeToolCall(toolBuf);
          if (parsed) {
            yield {
              type: "tool_use",
              tool_use_id: `native_${randomUUID().replace(/-/g, "").slice(0, 16)}`,
              tool_name: parsed.name,
              tool_input: parsed.arguments,
            };
          } else {
            yield {
              type: "text_delta",
              text: `${TOOL_CALL_OPEN}${toolBuf}`,
            };
          }
        } else if (textBuf) {
          yield { type: "text_delta", text: textBuf };
        }
        textBuf = "";
        toolBuf = "";
        inToolCall = false;

        // native field tool_calls emit
        for (const tc of currentToolCalls.values()) {
          let parsed: Record<string, unknown> = {};
          try {
            parsed = tc.arguments ? JSON.parse(tc.arguments) : {};
          } catch {
            parsed = { raw: tc.arguments };
          }
          yield {
            type: "tool_use",
            tool_use_id: tc.id || `oai_${randomUUID().replace(/-/g, "").slice(0, 16)}`,
            tool_name: tc.name,
            tool_input: parsed,
          };
        }
        currentToolCalls.clear();

        yield { type: "stop", stop_reason: finishReason };
      }
    }
  }
}

function toOpenAITool(
  t: FrozenToolDefinition,
): OpenAI.Chat.Completions.ChatCompletionTool {
  return {
    type: "function",
    function: {
      name: t.name,
      description: t.description || undefined,
      parameters: t.input_schema as any,
    },
  };
}

function convertMessages(msgs: Message[], system?: string): OpenAI.Chat.ChatCompletionMessageParam[] {
  // Python provider _convert_messages 1:1 — Anthropic content blocks → OpenAI tool_calls.
  const out: OpenAI.Chat.ChatCompletionMessageParam[] = [];
  if (system) out.push({ role: "system", content: system });

  for (const m of msgs) {
    if (m.role === "user") {
      if (typeof m.content === "string") {
        out.push({ role: "user", content: m.content });
      } else {
        // tool_result blocks → 별도 'tool' role 메시지로 분리
        const texts: string[] = [];
        const toolResults: ContentBlock[] = [];
        for (const b of m.content) {
          if ((b as any).type === "tool_result") toolResults.push(b);
          else if ((b as any).type === "text") texts.push((b as any).text);
        }
        if (texts.length) out.push({ role: "user", content: texts.join("\n") });
        for (const tr of toolResults) {
          out.push({
            role: "tool",
            tool_call_id: (tr as any).tool_use_id,
            content: typeof (tr as any).content === "string"
              ? (tr as any).content
              : JSON.stringify((tr as any).content),
          });
        }
      }
    } else if (m.role === "assistant") {
      if (typeof m.content === "string") {
        out.push({ role: "assistant", content: m.content });
      } else {
        const texts: string[] = [];
        const toolCalls: any[] = [];
        for (const b of m.content) {
          if ((b as any).type === "text") texts.push((b as any).text);
          else if ((b as any).type === "tool_use") {
            toolCalls.push({
              id: (b as any).id,
              type: "function",
              function: {
                name: (b as any).name,
                arguments: JSON.stringify((b as any).input || {}),
              },
            });
          }
        }
        const msg: any = { role: "assistant", content: texts.join("\n") || "" };
        if (toolCalls.length) msg.tool_calls = toolCalls;
        out.push(msg);
      }
    } else if (m.role === "system" && typeof m.content === "string") {
      out.push({ role: "system", content: m.content });
    }
  }
  return out;
}
