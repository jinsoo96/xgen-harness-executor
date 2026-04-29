import { BaseStage, type StrategyInfo } from "../pipeline/stage";
import type { PipelineState } from "../types";

export const LOOP_CONTINUE = "continue";
export const LOOP_COMPLETE = "complete";
export const LOOP_RETRY = "retry";
export const LOOP_ERROR = "error";

export class S09Decide extends BaseStage {
  readonly stage_id = "s09_decide";
  readonly display_name = "Decide";
  readonly display_name_ko = "결정";
  readonly phase = "loop" as const;
  readonly order = 9;

  async execute(state: PipelineState): Promise<Record<string, unknown>> {
    // s07_act strict_no_error 의 stop 신호 (Python 과 동일)
    if (state.metadata.s07_strict_failed) {
      const failures = (state.metadata.s07_strict_failures as unknown[]) || [];
      state.loop_decision = LOOP_COMPLETE;
      return {
        decision: LOOP_COMPLETE,
        reason: `strict_no_error: ${failures.length} 실패 — 후속 LLM 합성 차단`,
        strict_failures: failures,
      };
    }

    const strategy = this.resolveStrategyName(state, "threshold");
    if (strategy === "always_pass") {
      state.loop_decision = LOOP_COMPLETE;
      return { decision: LOOP_COMPLETE, reason: "always_pass" };
    }

    // threshold — pending tool calls 가 남아있으면 continue, validation 실패 시 retry
    if (state.pending_tool_calls.length > 0) {
      state.loop_decision = LOOP_CONTINUE;
      return { decision: LOOP_CONTINUE, reason: "pending tool calls" };
    }
    const threshold = this.getParam<number>("validation_threshold", state, 0.7);
    if (state.validation_score < threshold) {
      state.retry_count++;
      const maxRetries = Number(state.config.max_retries ?? state.config.max_iterations ?? 3);
      if (state.retry_count >= maxRetries) {
        state.loop_decision = LOOP_COMPLETE;
        return { decision: LOOP_COMPLETE, reason: "max retries reached" };
      }
      state.loop_decision = LOOP_RETRY;
      return { decision: LOOP_RETRY, reason: "validation score below threshold" };
    }
    state.loop_decision = LOOP_COMPLETE;
    return { decision: LOOP_COMPLETE, reason: "ok" };
  }

  listStrategies(): StrategyInfo[] {
    return [
      { name: "threshold", description: "Guard chain + 점수 기반.", is_default: true },
      { name: "always_pass", description: "항상 완료." },
    ];
  }
}
