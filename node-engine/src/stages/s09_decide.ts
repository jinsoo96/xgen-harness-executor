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

    // 방금 turn 에 tool 호출 → s07 가 flushToolResults() 로 pending 을 비우면서
    // messages 에 tool_result block 을 user 메시지로 추가했음. 이 경우 LLM 이
    // 결과를 보고 final answer 를 생성해야 하므로 LOOP_CONTINUE.
    // 이전 회귀: pending=0 만 보고 즉시 COMPLETE 처리해 산출물 외부 실행 시
    // tool 결과 받았는데 답변 본문이 빈 채로 종료.
    const lastMsg = state.messages[state.messages.length - 1];
    const lastIsToolResult =
      lastMsg !== undefined &&
      lastMsg.role === "user" &&
      Array.isArray(lastMsg.content) &&
      (lastMsg.content as unknown[]).some(
        (b) => typeof b === "object" && b !== null && (b as { type?: string }).type === "tool_result",
      );
    if (lastIsToolResult) {
      state.loop_decision = LOOP_CONTINUE;
      return {
        decision: LOOP_CONTINUE,
        reason: "tool results just added — next LLM call to synthesize",
      };
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
