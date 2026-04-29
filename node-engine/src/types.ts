/**
 * Common types — Python `xgen_harness.providers.base` + `core.state` 와 1:1 동등.
 *
 * 모든 stage / strategy / provider 가 같은 타입 사용해 fully equivalent 보장.
 */

import type { HarnessConfigData, FrozenToolDefinition } from "./spec/schema";

export type Role = "user" | "assistant" | "system" | "tool";

export interface Message {
  role: Role;
  content: string | ContentBlock[];
}

export type ContentBlock =
  | { type: "text"; text: string }
  | { type: "tool_use"; id: string; name: string; input: Record<string, unknown> }
  | { type: "tool_result"; tool_use_id: string; content: string; is_error?: boolean };

export type ProviderEventType =
  | "text_delta"
  | "tool_use"
  | "stop"
  | "usage"
  | "error";

export interface ProviderEvent {
  type: ProviderEventType;
  text?: string;
  tool_use_id?: string;
  tool_name?: string;
  tool_input?: Record<string, unknown>;
  input_tokens?: number;
  output_tokens?: number;
  cache_creation_tokens?: number;
  cache_read_tokens?: number;
  stop_reason?: string;
  raw?: Record<string, unknown>;
}

export interface ToolCall {
  tool_use_id: string;
  tool_name: string;
  tool_input: Record<string, unknown>;
}

export interface ToolResult {
  tool_use_id: string;
  tool_name: string;
  content: string;
  is_error: boolean;
  chars: number;
  success: boolean;
  error?: string;
}

/** Python PipelineState 의 TS 등가. 모든 stage 가 공유하는 상태. */
export class PipelineState {
  config: HarnessConfigData;

  // input
  user_input: string = "";
  files: unknown[] = [];

  // pipeline
  messages: Message[] = [];
  tool_definitions: FrozenToolDefinition[] = [];
  conversation_history: Message[] = [];
  previous_results: Record<string, unknown>[] = [];

  // tool execution
  pending_tool_calls: ToolCall[] = [];
  tool_results: ToolResult[] = [];
  tools_executed_count: number = 0;

  // llm
  last_assistant_text: string = "";
  validation_score: number = 1.0;
  validation_feedback: string = "";

  // loop
  retry_count: number = 0;
  iteration: number = 0;
  loop_decision: string = "continue";

  // policy
  policy_block_reason: string | null = null;

  // metadata (annotations / strict_failed / source_of / 등)
  metadata: Record<string, unknown> = {};

  // tool annotations payload (Python state.tool.annotations 등가)
  tool: { annotations: Record<string, Record<string, unknown>> } = { annotations: {} };

  // metrics
  input_tokens: number = 0;
  output_tokens: number = 0;
  llm_calls: number = 0;
  cost_usd: number = 0;

  // events emitter (CLI/MCP server 가 wiring)
  emitter: ((ev: PipelineEvent) => void | Promise<void>) | null = null;

  constructor(config: HarnessConfigData) {
    this.config = config;
  }

  async emit(ev: PipelineEvent): Promise<void> {
    if (this.emitter) await this.emitter(ev);
  }

  /**
   * Python `state.flush_tool_results()` — pending → messages user 메시지로.
   */
  flushToolResults(): void {
    if (!this.tool_results.length) return;
    const blocks: ContentBlock[] = this.tool_results.map((r) => ({
      type: "tool_result",
      tool_use_id: r.tool_use_id,
      content: r.content,
      is_error: r.is_error || undefined,
    }));
    this.messages.push({ role: "user", content: blocks });
    this.tool_results = [];
    this.pending_tool_calls = [];
  }
}

export type PipelineEventKind =
  | "stage_enter"
  | "stage_exit"
  | "stage_substep"
  | "tool_call_start"
  | "tool_call_complete"
  | "metrics"
  | "log"
  | "data";

export interface PipelineEvent {
  kind: PipelineEventKind;
  stage_id?: string;
  stage_name?: string;
  phase?: "ingress" | "loop" | "egress";
  step?: number;
  total?: number;
  bypassed?: boolean;
  output?: Record<string, unknown>;
  meta?: Record<string, unknown>;
  message?: string;
  level?: "DEBUG" | "INFO" | "WARNING" | "ERROR";
  text?: string;
}
