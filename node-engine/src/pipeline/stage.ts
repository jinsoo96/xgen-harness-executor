/**
 * Stage interface — Python `xgen_harness.core.stage.Stage` 와 1:1.
 */

import type { PipelineState, PipelineEvent } from "../types";

export type StagePhase = "ingress" | "loop" | "egress";

export interface StrategyInfo {
  name: string;
  description: string;
  is_default?: boolean;
}

export interface Stage {
  readonly stage_id: string;
  readonly display_name: string;
  readonly display_name_ko: string;
  readonly phase: StagePhase;
  readonly order: number;

  shouldBypass(state: PipelineState): boolean;
  execute(state: PipelineState): Promise<Record<string, unknown>>;
  listStrategies(): StrategyInfo[];

  /** stage_params 또는 active_strategies 에서 파라미터 조회. */
  getParam<T>(name: string, state: PipelineState, defaultValue: T): T;
  resolveStrategyName(state: PipelineState, defaultName: string): string;
}

export abstract class BaseStage implements Stage {
  abstract readonly stage_id: string;
  abstract readonly display_name: string;
  abstract readonly display_name_ko: string;
  abstract readonly phase: StagePhase;
  abstract readonly order: number;

  abstract execute(state: PipelineState): Promise<Record<string, unknown>>;

  shouldBypass(_state: PipelineState): boolean {
    return false;
  }

  listStrategies(): StrategyInfo[] {
    return [];
  }

  getParam<T>(name: string, state: PipelineState, defaultValue: T): T {
    const sp = state.config.stage_params?.[this.stage_id];
    if (sp && Object.prototype.hasOwnProperty.call(sp, name)) {
      return sp[name] as T;
    }
    // root config fallback (max_iterations / temperature 등 일부 필드)
    if (Object.prototype.hasOwnProperty.call(state.config, name)) {
      return (state.config as any)[name] as T;
    }
    return defaultValue;
  }

  resolveStrategyName(state: PipelineState, defaultName: string): string {
    return state.config.active_strategies?.[this.stage_id] || defaultName;
  }
}
