import { BaseStage, type StrategyInfo } from "../pipeline/stage";
import type { PipelineState } from "../types";

export class S02History extends BaseStage {
  readonly stage_id = "s02_history";
  readonly display_name = "History";
  readonly display_name_ko = "이력";
  readonly phase = "ingress" as const;
  readonly order = 2;

  shouldBypass(state: PipelineState): boolean {
    const strategy = this.resolveStrategyName(state, "default");
    if (strategy === "none") return true;
    if (strategy === "embedding_search") return !state.user_input;
    return state.previous_results.length === 0 && state.conversation_history.length === 0;
  }

  async execute(state: PipelineState): Promise<Record<string, unknown>> {
    const strategy = this.resolveStrategyName(state, "default");

    if (strategy === "none") {
      return { injected: 0, previous_results: 0, strategy: "none" };
    }

    const maxHistory = Number(this.getParam("max_history", state, 10));
    let injected = 0;
    if (state.conversation_history.length) {
      const history = state.conversation_history.slice(-maxHistory);
      // Python: insert before last (last = current user). 동일 동작.
      const insertAt = Math.max(0, state.messages.length - 1);
      for (let i = 0; i < history.length; i++) {
        const h = history[i];
        state.messages.splice(insertAt + i, 0, h);
        injected++;
      }
    }
    return {
      injected,
      previous_results: state.previous_results.length,
      strategy,
    };
  }

  listStrategies(): StrategyInfo[] {
    return [
      {
        name: "default",
        description: "이전 실행 결과 + 대화 이력 로드 (멀티턴 기억 유지)",
        is_default: true,
      },
      {
        name: "embedding_search",
        description: "임베딩 기반 관련 기억만 골라 주입 (긴 대화에서 비용 절감)",
      },
      {
        name: "none",
        description: "이력 무시 — 매 turn 독립 실행 (단발 질의용, 가장 빠름)",
      },
    ];
  }
}
