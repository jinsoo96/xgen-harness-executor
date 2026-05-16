/**
 * Harness Spec — fully equivalent JSON schema.
 *
 * Python `xgen_harness.compile.npm_spec.HarnessSpec` 와 1:1 동등. publish 시
 * Python 측이 만든 spec.json 을 이 schema 로 검증 + 로드 → engine 이 13 stage
 * pipeline 을 그대로 재현.
 *
 * 어떤 필드도 임의 default 로 채우지 않음 — 사용자가 저장한 모든 stage 설정은
 * spec.json 에 박혀있고, engine 은 그걸 정확히 따름.
 */

import { z } from "zod";

export const SPEC_VERSION = "1.0";

// ─── Tool Definition (frozen) ─────────────────────────────────────

export const FrozenToolCallKind = z.enum([
  "http",
  "mcp_session",
  "rag",
  "noop",
  // PD builtin — engine-node 자체에서 spec.tool_definitions 카탈로그 메타 탐색.
  "builtin:search_tools",
  "builtin:discover_tools",
]);
export type FrozenToolCallKind = z.infer<typeof FrozenToolCallKind>;

export const FrozenToolDefinitionSchema = z.object({
  name: z.string(),
  description: z.string().default(""),
  input_schema: z.record(z.any()).default({ type: "object" }),
  call_kind: FrozenToolCallKind.default("noop"),
  call_spec: z.record(z.any()).default({}),
  annotations: z.record(z.any()).default({}),
  tags: z.array(z.string()).default([]),
});
export type FrozenToolDefinition = z.infer<typeof FrozenToolDefinitionSchema>;

// ─── External Inputs (runtime placeholder values) ──────────────────

export const ExternalInputSchema = z.object({
  type: z.string().default("string"),
  required: z.boolean().default(false),
  default: z.unknown().optional(),
  description: z.string().optional(),
});
export type ExternalInput = z.infer<typeof ExternalInputSchema>;

// ─── Harness Config (1:1 with Python HarnessConfig.to_dict) ────────

/**
 * `xgen_harness.core.config.HarnessConfig.to_dict()` 와 1:1.
 *
 * 모든 필드 optional — 사용자 워크플로우에 따라 일부만 박혀있음. engine 은
 * 누락된 필드는 stage 별 default 로 처리 (Python 과 동일 default).
 */
export const HarnessConfigSchema = z
  .object({
    // identity
    provider: z.string().optional(),
    model: z.string().optional(),
    openai_model: z.string().optional(),
    anthropic_model: z.string().optional(),
    harness_mode: z.string().optional(),
    preset: z.string().optional(),

    // generation
    temperature: z.number().optional(),
    max_tokens: z.number().int().optional(),
    aux_max_tokens: z.number().int().optional(),
    max_iterations: z.number().int().optional(),
    max_retries: z.number().int().optional(),
    validation_threshold: z.number().optional(),
    context_window: z.number().int().optional(),

    // prompt
    system_prompt: z.string().optional(),

    // pipeline shaping
    disabled_stages: z.array(z.string()).default([]),
    artifacts: z.record(z.string()).default({}),
    stage_params: z.record(z.record(z.any())).default({}),
    active_strategies: z.record(z.string()).default({}),
    strategy_variants: z.record(z.any()).default({}),

    // capabilities
    capabilities: z.array(z.string()).default([]),
    capability_params: z.record(z.any()).default({}),

    // thinking / planner
    thinking_enabled: z.boolean().default(false),
    thinking_budget_tokens: z.number().int().optional(),
    use_planner: z.boolean().default(false),

    // misc
    external_inputs: z.record(ExternalInputSchema).default({}),
  })
  .passthrough(); // 미래 확장 — 알 수 없는 키도 그대로 전달
export type HarnessConfigData = z.infer<typeof HarnessConfigSchema>;

// ─── Top-level Spec ────────────────────────────────────────────────

export const HarnessSpecSchema = z.object({
  spec_version: z.string().default(SPEC_VERSION),
  harness_version: z.string().default(">=0.28.0,<0.29"),
  gallery_name: z.string(),
  gallery_version: z.string().default("0.1.0"),
  compiled_at: z.string().default(""),
  config: HarnessConfigSchema,
  tool_definitions: z.array(FrozenToolDefinitionSchema).default([]),
  external_inputs: z.record(ExternalInputSchema).default({}),
  metadata: z.record(z.any()).default({}),
});
export type HarnessSpec = z.infer<typeof HarnessSpecSchema>;

// ─── Loaders ───────────────────────────────────────────────────────

/** 임의 input 을 `HarnessSpec` 으로 검증/정규화. 검증 실패 시 throw. */
export function parseSpec(raw: unknown): HarnessSpec {
  const parsed = HarnessSpecSchema.parse(raw);
  if (parsed.spec_version !== SPEC_VERSION) {
    // 다른 spec version 은 호환 가능한 경우만 허용 — 현재는 1.0 만.
    throw new Error(
      `[harness-engine] unsupported spec_version=${parsed.spec_version} ` +
        `(this engine supports ${SPEC_VERSION})`,
    );
  }
  return parsed;
}

/** path 또는 JSON 문자열에서 spec 로드. */
export function loadSpec(input: string | object): HarnessSpec {
  if (typeof input === "string") {
    // path 인지 raw JSON 인지 휴리스틱 — `{` 시작이면 JSON
    if (input.trim().startsWith("{")) {
      return parseSpec(JSON.parse(input));
    }
    // path
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const fs = require("fs");
    const text = fs.readFileSync(input, "utf-8");
    return parseSpec(JSON.parse(text));
  }
  return parseSpec(input);
}
