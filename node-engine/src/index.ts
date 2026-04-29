/**
 * xgen-harness-engine-node — public API.
 *
 * 컴파일된 npm 패키지 (`xgen-harness-{name}`) 의 bin/cli.js 가 require 한 뒤
 * `serveMcp(spec)` 또는 `runOnce(spec, input)` 호출.
 *
 * ## fully equivalent 약속
 *
 * Python `xgen_harness` 의 13 stage / 31 strategy / 4 provider / 모든
 * stage_params 를 spec.json 으로 박아 1:1 재현. minimal pipeline / 임시방편 X.
 *
 * 1차 알파 (0.28.0-alpha.1) 미구현 영역:
 *   - s05_strategy 의 cot_planner / react / capability 본문 (marker 만 stamp)
 *   - s08_judge 의 llm_judge (rule_based / none 만)
 *   - s06_context 의 RAG 동적 검색 (frozen spec 만 사용)
 *   - capability binding (spec.config.capabilities 그대로 metadata 에 stamp)
 *
 * Phase 2 (0.28.0-beta) 추가 예정.
 */

import { loadSpec, type HarnessSpec } from "./spec/schema";
import { runPipeline, type RunResult } from "./pipeline/pipeline";
import { serveMcp as serveMcpImpl } from "./cli/serve_mcp";
import { registerBuiltinProviders } from "./providers/base";

export { HarnessSpec, loadSpec };
export {
  parseSpec,
  HarnessSpecSchema,
  HarnessConfigSchema,
  FrozenToolDefinitionSchema,
  SPEC_VERSION,
} from "./spec/schema";
export type { FrozenToolDefinition, HarnessConfigData } from "./spec/schema";
export { runPipeline };
export type { RunResult };
export { registerProvider, registerBuiltinProviders, createProvider } from "./providers/base";
export type { LLMProvider, ChatRequest, ProviderFactory } from "./providers/base";
export { parseNativeToolCall } from "./providers/openai";

/** MCP stdio server. spec 받아 long-running. */
export async function serveMcp(specOrPath: HarnessSpec | string | object): Promise<void> {
  registerBuiltinProviders();
  const spec = typeof specOrPath === "string" || !("config" in (specOrPath as any))
    ? loadSpec(specOrPath as any)
    : (specOrPath as HarnessSpec);
  await serveMcpImpl(spec);
}

/** 단발 실행 — input 한 번 처리해 결과 반환. test/script 용. */
export async function runOnce(
  specOrPath: HarnessSpec | string | object,
  input: string,
): Promise<RunResult> {
  registerBuiltinProviders();
  const spec =
    typeof specOrPath === "string" || !("config" in (specOrPath as any))
      ? loadSpec(specOrPath as any)
      : (specOrPath as HarnessSpec);
  return runPipeline(spec, input);
}
