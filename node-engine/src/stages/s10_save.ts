import { BaseStage, type StrategyInfo } from "../pipeline/stage";
import type { PipelineState } from "../types";

export class S10Save extends BaseStage {
  readonly stage_id = "s10_save";
  readonly display_name = "Save";
  readonly display_name_ko = "저장";
  readonly phase = "egress" as const;
  readonly order = 10;

  shouldBypass(state: PipelineState): boolean {
    const strategy = this.resolveStrategyName(state, "default");
    return strategy === "noop";
  }

  async execute(state: PipelineState): Promise<Record<string, unknown>> {
    // npm runner 환경엔 DB 가 없을 수 있음 — metadata 에 stamp 만.
    // 외부 환경 (Claude Desktop 등) 에서 npx 실행 시 DB 의존성 0.
    const id = `exec_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    state.metadata.execution_id = id;
    return { saved: false, execution_id: id, table_name: null };
  }

  listStrategies(): StrategyInfo[] {
    return [
      { name: "default", description: "DB write hook (외부 환경엔 noop).", is_default: true },
      { name: "noop", description: "저장 비활성." },
    ];
  }
}
