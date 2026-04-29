import { BaseStage, type StrategyInfo } from "../pipeline/stage";
import type { PipelineState } from "../types";

export class S03Prompt extends BaseStage {
  readonly stage_id = "s03_prompt";
  readonly display_name = "Prompt";
  readonly display_name_ko = "프롬프트";
  readonly phase = "ingress" as const;
  readonly order = 3;

  async execute(state: PipelineState): Promise<Record<string, unknown>> {
    // Python s03 — system_prompt 를 messages 의 system 으로 활용 (provider 가
    // ChatRequest.system 으로 분리해 처리하므로 여기선 metadata 만 박아둠).
    const sp = state.config.system_prompt || "";
    state.metadata.system_prompt = sp;
    return {
      prompt_chars: sp.length,
      sections: ["identity", "rules"],
    };
  }

  listStrategies(): StrategyInfo[] {
    return [
      { name: "section_priority", description: "identity → rules → memory 순.", is_default: true },
    ];
  }
}
