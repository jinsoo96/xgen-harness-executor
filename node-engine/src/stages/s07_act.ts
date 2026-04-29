/**
 * S07 Act — 도구 실행. Frozen tool 의 call_kind 별로 dispatch.
 *
 * Python s07 은 외부 ToolSource 의 call_tool 위임이지만 Node engine 은 spec
 * 안에 freeze 된 call_spec 으로 직접 호출 (xgen-nodes 의존성 0).
 */

import { BaseStage, type StrategyInfo } from "../pipeline/stage";
import type { PipelineState, ToolResult, ToolCall } from "../types";
import { dispatchToolCall } from "../tools/dispatch";

const TOOL_TIMEOUT_DEFAULT_MS = 60_000;
const RESULT_BUDGET_DEFAULT = 50_000;

export class S07Act extends BaseStage {
  readonly stage_id = "s07_act";
  readonly display_name = "Act";
  readonly display_name_ko = "실행";
  readonly phase = "loop" as const;
  readonly order = 7;

  shouldBypass(state: PipelineState): boolean {
    return state.pending_tool_calls.length === 0;
  }

  async execute(state: PipelineState): Promise<Record<string, unknown>> {
    const calls = state.pending_tool_calls;
    if (!calls.length) return { tools_executed: 0, bypassed: true };

    const strategy = this.resolveStrategyName(state, "default");
    const timeoutMs = this.getParam<number>(
      "timeout",
      state,
      TOOL_TIMEOUT_DEFAULT_MS,
    );
    const budget = this.getParam<number>("result_budget", state, RESULT_BUDGET_DEFAULT);

    let results: ToolResult[];
    if (strategy === "parallel_read") {
      results = await this.executeParallelRead(calls, state, budget, timeoutMs);
    } else {
      results = await this.executeSequential(calls, state, budget, timeoutMs);
    }

    if (strategy === "strict_no_error") {
      const failed = results.filter((r) => !r.success);
      if (failed.length) {
        state.metadata.s07_strict_failed = true;
        state.metadata.s07_strict_failures = failed.map((r) => ({
          tool: r.tool_name,
          error: r.error,
        }));
      }
    }

    state.tool_results = results;
    state.flushToolResults();
    state.tools_executed_count += results.length;

    const successCount = results.filter((r) => r.success).length;
    const errorCount = results.length - successCount;
    return {
      tools_executed: results.length,
      success_count: successCount,
      error_count: errorCount,
      total_chars: results.reduce((s, r) => s + r.chars, 0),
      strategy,
    };
  }

  private async executeSequential(
    calls: ToolCall[],
    state: PipelineState,
    budget: number,
    timeoutMs: number,
  ): Promise<ToolResult[]> {
    const out: ToolResult[] = [];
    let total = 0;
    for (const tc of calls) {
      const r = await this.executeOne(tc, state, budget - total, timeoutMs);
      out.push(r);
      total += r.chars;
    }
    return out;
  }

  private async executeParallelRead(
    calls: ToolCall[],
    state: PipelineState,
    budget: number,
    timeoutMs: number,
  ): Promise<ToolResult[]> {
    const reads: ToolCall[] = [];
    const writes: ToolCall[] = [];
    for (const tc of calls) {
      const annot = state.tool.annotations[tc.tool_name] || {};
      const readOnly = annot.readOnlyHint === true || annot.read_only_hint === true;
      (readOnly ? reads : writes).push(tc);
    }
    const results: ToolResult[] = [];
    if (reads.length) {
      const settled = await Promise.allSettled(
        reads.map((tc) => this.executeOne(tc, state, budget, timeoutMs)),
      );
      for (const s of settled) {
        if (s.status === "fulfilled") results.push(s.value);
        else
          results.push({
            tool_use_id: "",
            tool_name: "unknown",
            content: String(s.reason),
            is_error: true,
            chars: String(s.reason).length,
            success: false,
            error: String(s.reason),
          });
      }
    }
    let totalChars = results.reduce((s, r) => s + r.chars, 0);
    for (const tc of writes) {
      const r = await this.executeOne(tc, state, budget - totalChars, timeoutMs);
      results.push(r);
      totalChars += r.chars;
    }
    return results;
  }

  private async executeOne(
    tc: ToolCall,
    state: PipelineState,
    remainingBudget: number,
    timeoutMs: number,
  ): Promise<ToolResult> {
    const def = state.tool_definitions.find((t) => t.name === tc.tool_name);
    if (!def) {
      const msg = `tool not found in spec: ${tc.tool_name}`;
      return {
        tool_use_id: tc.tool_use_id,
        tool_name: tc.tool_name,
        content: msg,
        is_error: true,
        chars: msg.length,
        success: false,
        error: msg,
      };
    }
    try {
      const r = await Promise.race([
        dispatchToolCall(def, tc.tool_input, state),
        new Promise<never>((_, reject) =>
          setTimeout(() => reject(new Error(`timeout ${timeoutMs}ms`)), timeoutMs),
        ),
      ]);
      let content = String(r.content ?? "");
      if (remainingBudget > 0 && content.length > remainingBudget) {
        content = content.slice(0, remainingBudget) + "\n…(truncated)";
      }
      return {
        tool_use_id: tc.tool_use_id,
        tool_name: tc.tool_name,
        content,
        is_error: !!r.is_error,
        chars: content.length,
        success: !r.is_error,
        error: r.is_error ? content : undefined,
      };
    } catch (e) {
      const msg = (e as Error).message || String(e);
      return {
        tool_use_id: tc.tool_use_id,
        tool_name: tc.tool_name,
        content: msg,
        is_error: true,
        chars: msg.length,
        success: false,
        error: msg,
      };
    }
  }

  listStrategies(): StrategyInfo[] {
    return [
      { name: "default", description: "순차 실행 + 에러 허용.", is_default: true },
      { name: "parallel_read", description: "읽기 도구 병렬 + 쓰기 직렬." },
      { name: "strict_no_error", description: "1개라도 실패 시 즉시 중단." },
    ];
  }
}
