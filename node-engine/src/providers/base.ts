/**
 * Provider base — Python `xgen_harness.providers.base.LLMProvider` 와 1:1.
 */

import type { Message, ProviderEvent } from "../types";
import type { FrozenToolDefinition } from "../spec/schema";

export interface ChatRequest {
  messages: Message[];
  system?: string;
  tools?: FrozenToolDefinition[];
  temperature?: number;
  max_tokens?: number;
  stream?: boolean;
  thinking?: { enabled: boolean; budget_tokens?: number };
  tool_choice?: "auto" | "required" | "none" | string;
}

export interface LLMProvider {
  readonly providerName: string;
  readonly modelName: string;
  supportsToolUse(): boolean;
  supportsThinking(): boolean;
  chat(req: ChatRequest): AsyncGenerator<ProviderEvent, void, unknown>;
}

/** Provider 등록 — 외부 플러그인이 추가 가능 (Python register_provider 와 동등). */
export type ProviderFactory = (opts: {
  apiKey: string;
  model: string;
  baseUrl?: string;
}) => LLMProvider;

const REGISTRY = new Map<string, ProviderFactory>();

export function registerProvider(name: string, factory: ProviderFactory): void {
  REGISTRY.set(name, factory);
}

export function createProvider(opts: {
  provider: string;
  apiKey: string;
  model: string;
  baseUrl?: string;
}): LLMProvider {
  const factory = REGISTRY.get(opts.provider);
  if (!factory) {
    throw new Error(
      `[harness-engine] provider not registered: ${opts.provider}. ` +
        `Available: ${[...REGISTRY.keys()].join(", ")}`,
    );
  }
  return factory(opts);
}

/** Anthropic / OpenAI / vLLM 기본 등록. */
export function registerBuiltinProviders(): void {
  // 순환 import 방지 — lazy.
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const { AnthropicProvider } = require("./anthropic");
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const { OpenAIProvider } = require("./openai");
  registerProvider("anthropic", (o) => new AnthropicProvider(o));
  registerProvider("openai", (o) => new OpenAIProvider(o));
  registerProvider("vllm", (o) => new OpenAIProvider(o)); // OpenAI 호환
}
