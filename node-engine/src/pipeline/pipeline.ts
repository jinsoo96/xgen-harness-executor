/**
 * Pipeline — 13 stage 흐름 + s00_harness loop controller.
 *
 * Python `xgen_harness.core.pipeline` 와 동일 흐름:
 *   ingress (s01/s02/s03/s04/s05) → loop (s06→s00 main_call→s07→s08→s09)
 *                                  → egress (s10/s11)
 */

import type { HarnessSpec } from "../spec/schema";
import {
  PipelineState,
  type PipelineEvent,
  type ProviderEvent,
  type Message,
} from "../types";
import { buildStageList, REQUIRED_STAGES } from "../stages";
import type { Stage } from "./stage";
import { LOOP_COMPLETE, LOOP_RETRY, LOOP_ERROR } from "../stages/s09_decide";
import { createProvider, registerBuiltinProviders } from "../providers/base";

export interface RunResult {
  output: string;
  metrics: Record<string, unknown>;
  events: PipelineEvent[];
}

export interface RunOptions {
  emitter?: (ev: PipelineEvent) => void | Promise<void>;
  /** 외부 환경 (Claude Desktop) 에서 stream 출력. */
  collectEvents?: boolean;
}

let _builtinRegistered = false;

export async function runPipeline(
  spec: HarnessSpec,
  userInput: string,
  opts: RunOptions = {},
): Promise<RunResult> {
  if (!_builtinRegistered) {
    registerBuiltinProviders();
    _builtinRegistered = true;
  }

  const state = new PipelineState(spec.config);
  state.user_input = userInput;
  state.metadata.spec_tool_definitions = spec.tool_definitions;
  state.metadata.gallery_name = spec.gallery_name;
  state.metadata.gallery_version = spec.gallery_version;

  const events: PipelineEvent[] = [];
  const emit = async (ev: PipelineEvent) => {
    if (opts.collectEvents !== false) events.push(ev);
    if (opts.emitter) await opts.emitter(ev);
  };
  state.emitter = emit;

  const stages = buildStageList();
  const total = stages.length + 1; // +1 for s00 main_call slot

  // disabled_stages 검사 — REQUIRED_STAGES 는 비활성화 불가
  const disabled = new Set(spec.config.disabled_stages || []);
  const isActive = (s: Stage) => {
    if (REQUIRED_STAGES.has(s.stage_id)) return true;
    return !disabled.has(s.stage_id);
  };

  // ─── ingress (order 1~5) ────────────────────────────────────
  for (const stage of stages.filter((s) => s.phase === "ingress" && isActive(s))) {
    await runStage(stage, state, emit);
  }

  // ─── loop ──────────────────────────────────────────────────
  const maxIter = Number(spec.config.max_iterations ?? 10);
  for (let iter = 0; iter < maxIter; iter++) {
    state.iteration = iter + 1;

    // s06 context (pre)
    const s06 = stages.find((s) => s.stage_id === "s06_context");
    if (s06 && isActive(s06)) await runStage(s06, state, emit);

    // s00 main_call — provider chat
    await runMainCall(state, emit);

    // s07 act
    const s07 = stages.find((s) => s.stage_id === "s07_act");
    if (s07 && isActive(s07)) await runStage(s07, state, emit);

    // s08 judge
    const s08 = stages.find((s) => s.stage_id === "s08_judge");
    if (s08 && isActive(s08)) await runStage(s08, state, emit);

    // s09 decide
    const s09 = stages.find((s) => s.stage_id === "s09_decide");
    if (s09) await runStage(s09, state, emit);

    if (state.loop_decision === LOOP_COMPLETE) break;
    if (state.loop_decision === LOOP_ERROR) break;
    // LOOP_RETRY / LOOP_CONTINUE → 다음 iter
  }

  // ─── egress (order 10~11) ────────────────────────────────────
  for (const stage of stages.filter((s) => s.phase === "egress" && isActive(s))) {
    await runStage(stage, state, emit);
  }

  return {
    output: state.last_assistant_text,
    metrics: {
      input_tokens: state.input_tokens,
      output_tokens: state.output_tokens,
      llm_calls: state.llm_calls,
      iterations: state.iteration,
      tools_executed: state.tools_executed_count,
      cost_usd: state.cost_usd,
    },
    events,
  };
}

async function runStage(
  stage: Stage,
  state: PipelineState,
  emit: (ev: PipelineEvent) => Promise<void>,
): Promise<void> {
  const bypassed = stage.shouldBypass(state);
  await emit({
    kind: "stage_enter",
    stage_id: stage.stage_id,
    stage_name: stage.display_name_ko,
    phase: stage.phase,
    bypassed,
  });
  if (bypassed) {
    await emit({
      kind: "stage_exit",
      stage_id: stage.stage_id,
      bypassed: true,
      output: { bypassed: true, reason: "조건 미충족" },
    });
    return;
  }
  try {
    const out = await stage.execute(state);
    await emit({ kind: "stage_exit", stage_id: stage.stage_id, output: out });
  } catch (e) {
    await emit({
      kind: "stage_exit",
      stage_id: stage.stage_id,
      level: "ERROR",
      message: (e as Error).message,
    });
    throw e;
  }
}

/**
 * s00_harness main_call — 본문 LLM 호출. Python `s00_harness/strategies/streaming`
 * 와 동일 흐름 (text + tool_use 누적, pending_tool_calls 채움).
 */
async function runMainCall(
  state: PipelineState,
  emit: (ev: PipelineEvent) => Promise<void>,
): Promise<void> {
  await emit({
    kind: "stage_enter",
    stage_id: "s00_harness",
    stage_name: "Auto",
    phase: "loop",
  });

  const provider = createProvider({
    provider: state.config.provider || "anthropic",
    apiKey: pickApiKey(state.config.provider || "anthropic"),
    model: state.config.model || "",
    baseUrl: pickBaseUrl(state.config.provider || "anthropic"),
  });

  const sysPrompt = (state.metadata.system_prompt as string) || state.config.system_prompt || "";

  // tool_use accumulation
  let assistantText = "";
  const pending: Array<{
    tool_use_id: string;
    tool_name: string;
    tool_input: Record<string, unknown>;
  }> = [];

  state.llm_calls++;
  const stream = provider.chat({
    messages: state.messages,
    system: sysPrompt,
    tools: state.tool_definitions,
    temperature: state.config.temperature ?? 0.7,
    max_tokens: state.config.max_tokens ?? 8192,
    stream: true,
    thinking: state.config.thinking_enabled
      ? { enabled: true, budget_tokens: state.config.thinking_budget_tokens }
      : undefined,
  });

  for await (const ev of stream) {
    if (ev.type === "text_delta" && ev.text) {
      assistantText += ev.text;
      await emit({ kind: "data", text: ev.text });
    } else if (ev.type === "tool_use" && ev.tool_name) {
      pending.push({
        tool_use_id: ev.tool_use_id || "",
        tool_name: ev.tool_name,
        tool_input: ev.tool_input || {},
      });
    } else if (ev.type === "usage") {
      state.input_tokens += ev.input_tokens || 0;
      state.output_tokens += ev.output_tokens || 0;
    } else if (ev.type === "stop") {
      await emit({
        kind: "stage_substep",
        stage_id: "s00_harness",
        meta: {
          substep: "llm_response_complete",
          has_tool_calls: pending.length > 0,
          text_length: assistantText.length,
          stop_reason: ev.stop_reason,
        },
      });
    }
  }

  state.last_assistant_text = assistantText;

  // assistant 메시지 추가
  if (pending.length > 0) {
    const blocks: any[] = [];
    if (assistantText) blocks.push({ type: "text", text: assistantText });
    for (const p of pending) {
      blocks.push({
        type: "tool_use",
        id: p.tool_use_id,
        name: p.tool_name,
        input: p.tool_input,
      });
    }
    state.messages.push({ role: "assistant", content: blocks });
    state.pending_tool_calls = pending.map((p) => ({
      tool_use_id: p.tool_use_id,
      tool_name: p.tool_name,
      tool_input: p.tool_input,
    }));
  } else if (assistantText) {
    state.messages.push({ role: "assistant", content: assistantText });
    state.pending_tool_calls = [];
  } else {
    state.pending_tool_calls = [];
  }

  await emit({ kind: "stage_exit", stage_id: "s00_harness" });
}

function pickApiKey(provider: string): string {
  switch (provider) {
    case "anthropic":
      return process.env.ANTHROPIC_API_KEY || "";
    case "openai":
      return process.env.OPENAI_API_KEY || "";
    case "vllm":
      return process.env.VLLM_API_KEY || "EMPTY";
    default:
      return process.env[`${provider.toUpperCase()}_API_KEY`] || "";
  }
}

function pickBaseUrl(provider: string): string | undefined {
  switch (provider) {
    case "openai":
      return process.env.OPENAI_API_BASE_URL || undefined;
    case "anthropic":
      return process.env.ANTHROPIC_API_BASE_URL || undefined;
    case "vllm":
      return process.env.VLLM_API_BASE_URL || process.env.VLLM_BASE_URL;
    default:
      return process.env[`${provider.toUpperCase()}_API_BASE_URL`];
  }
}
