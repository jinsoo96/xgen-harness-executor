import { BaseStage, type StrategyInfo } from "../pipeline/stage";
import type { PipelineState } from "../types";

export class S01Input extends BaseStage {
  readonly stage_id = "s01_input";
  readonly display_name = "Input";
  readonly display_name_ko = "입력";
  readonly phase = "ingress" as const;
  readonly order = 1;

  shouldBypass(state: PipelineState): boolean {
    return !state.user_input;
  }

  async execute(state: PipelineState): Promise<Record<string, unknown>> {
    // Python s01 과 동일 — input 길이 체크 + messages 에 user 추가 (이미 있으면 skip)
    const last = state.messages[state.messages.length - 1];
    const hasUserMsg =
      last && last.role === "user" &&
      (typeof last.content === "string"
        ? last.content === state.user_input
        : false);
    if (!hasUserMsg) {
      state.messages.push({ role: "user", content: state.user_input });
    }
    return {
      input_length: state.user_input.length,
      files_count: state.files.length,
    };
  }

  listStrategies(): StrategyInfo[] {
    return [
      { name: "default", description: "Plain user input.", is_default: true },
      { name: "with_classification", description: "Classify input intent." },
    ];
  }
}
