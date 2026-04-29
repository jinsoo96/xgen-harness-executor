import { BaseStage, type StrategyInfo } from "../pipeline/stage";
import type { PipelineState } from "../types";

export class S11Finalize extends BaseStage {
  readonly stage_id = "s11_finalize";
  readonly display_name = "Finalize";
  readonly display_name_ko = "마무리";
  readonly phase = "egress" as const;
  readonly order = 11;

  async execute(state: PipelineState): Promise<Record<string, unknown>> {
    const strategy = this.resolveStrategyName(state, "default");
    let output = state.last_assistant_text;
    if (strategy === "format_json") {
      try {
        const parsed = JSON.parse(output);
        output = JSON.stringify(parsed, null, 2);
      } catch {
        // 그대로
      }
    }
    return {
      output_length: output.length,
      usage: {
        input_tokens: state.input_tokens,
        output_tokens: state.output_tokens,
      },
      total_tokens: state.input_tokens + state.output_tokens,
      llm_calls: state.llm_calls,
      tools_executed: state.tools_executed_count,
      iterations: state.iteration,
      cost_usd: state.cost_usd,
      model: state.config.model || "",
      output_text: output,
    };
  }

  listStrategies(): StrategyInfo[] {
    return [
      { name: "default", description: "Plain output.", is_default: true },
      { name: "format_json", description: "JSON pretty-print." },
    ];
  }
}
