/**
 * S05 Strategy — 응답 전략 수립.
 *
 * Python `xgen_harness.stages.s05_strategy` 1:1 포팅:
 *   - cot_planner: LLM 호출로 단계 plan 생성 → state.metadata.plan + system_prompt 보강
 *   - react: 관찰-행동 루프 — system_prompt 에 ReAct 패턴 가이드 주입
 *   - capability: capability_params 에 박힌 capability 매칭 도구 합류
 *   - none: 비활성
 */

import { BaseStage, type StrategyInfo } from "../pipeline/stage";
import type { PipelineState } from "../types";
import { createProvider } from "../providers/base";

export class S05Strategy extends BaseStage {
  readonly stage_id = "s05_strategy";
  readonly display_name = "Strategy";
  readonly display_name_ko = "전략";
  readonly phase = "ingress" as const;
  readonly order = 5;

  shouldBypass(state: PipelineState): boolean {
    const strategy = this.resolveStrategyName(state, "none");
    return strategy === "none";
  }

  async execute(state: PipelineState): Promise<Record<string, unknown>> {
    const strategy = this.resolveStrategyName(state, "none");
    state.metadata.strategy = strategy;

    if (strategy === "cot_planner") {
      return await this.executeCotPlanner(state);
    }
    if (strategy === "react") {
      return this.executeReact(state);
    }
    if (strategy === "capability") {
      return this.executeCapability(state);
    }
    return { strategy };
  }

  // ─── cot_planner — 별도 LLM 호출로 단계 plan ────────────────────

  private async executeCotPlanner(state: PipelineState): Promise<Record<string, unknown>> {
    let provider;
    try {
      provider = createProvider({
        provider: state.config.provider || "anthropic",
        apiKey:
          process.env[`${(state.config.provider || "anthropic").toUpperCase()}_API_KEY`] || "",
        model: state.config.model || "claude-haiku-4-5-20251001",
      });
    } catch (e) {
      return { strategy: "cot_planner", error: `provider init failed: ${(e as Error).message}` };
    }

    const toolsList = state.tool_definitions
      .map((t) => `- ${t.name}: ${(t.description || "").slice(0, 80)}`)
      .join("\n");
    const prompt = [
      "You are a planning assistant. Break down the user's task into 3-7 concrete steps.",
      "Available tools:",
      toolsList || "(none)",
      "",
      "User task:",
      state.user_input,
      "",
      "Return JSON only:",
      '{"steps": ["step1", "step2", ...], "summary": "<one-line plan summary>"}',
    ].join("\n");

    let raw = "";
    try {
      const stream = provider.chat({
        messages: [{ role: "user", content: prompt }],
        max_tokens: Number(state.config.aux_max_tokens || 800),
        temperature: 0.3,
        stream: true,
      });
      for await (const ev of stream) {
        if (ev.type === "text_delta" && ev.text) raw += ev.text;
        if (ev.type === "stop") break;
      }
    } catch (e) {
      return {
        strategy: "cot_planner",
        error: `planner call failed: ${(e as Error).message}`,
      };
    }

    let parsed: { steps?: string[]; summary?: string } = {};
    try {
      const start = raw.indexOf("{");
      const end = raw.lastIndexOf("}");
      if (start >= 0 && end > start) parsed = JSON.parse(raw.slice(start, end + 1));
    } catch {
      // ignore
    }
    const steps = Array.isArray(parsed.steps) ? parsed.steps.map(String) : [];
    const summary = String(parsed.summary || "");

    state.metadata.plan = {
      strategy: "cot_planner",
      steps,
      summary,
      tools_referenced: state.tool_definitions.map((t) => t.name),
    };

    // system_prompt 에 plan 주입
    const sys = (state.metadata.system_prompt as string) || state.config.system_prompt || "";
    const planText =
      "\n\n## Execution Plan (cot_planner)\n" +
      (summary ? summary + "\n" : "") +
      steps.map((s, i) => `${i + 1}. ${s}`).join("\n");
    state.metadata.system_prompt = sys + planText;

    return {
      strategy: "cot_planner",
      step_count: steps.length,
      summary,
    };
  }

  // ─── react — system_prompt 에 ReAct 가이드 주입 ────────────────

  private executeReact(state: PipelineState): Record<string, unknown> {
    const sys = (state.metadata.system_prompt as string) || state.config.system_prompt || "";
    const reactGuide = [
      "",
      "## ReAct Pattern",
      "Use the following loop for complex tasks:",
      "  Thought: <what to do next>",
      "  Action: <tool call>",
      "  Observation: <tool result>",
      "  ... (repeat) ...",
      "  Final Answer: <synthesized answer>",
      "",
      "Always verify observations before drawing conclusions.",
    ].join("\n");
    state.metadata.system_prompt = sys + reactGuide;
    return { strategy: "react", guide_chars: reactGuide.length };
  }

  // ─── capability — capability_params 매칭 도구 metadata stamp ──

  private executeCapability(state: PipelineState): Record<string, unknown> {
    const caps = state.config.capabilities || [];
    const params = state.config.capability_params || {};
    state.metadata.capability_strategy_active = true;
    state.metadata.capabilities_active = caps;
    state.metadata.capability_params = params;
    return {
      strategy: "capability",
      capabilities: caps,
      param_keys: Object.keys(params),
    };
  }

  listStrategies(): StrategyInfo[] {
    return [
      { name: "cot_planner", description: "Chain-of-Thought planning — 별도 LLM 호출로 plan 생성." },
      { name: "react", description: "Observe-Action 루프 — system_prompt 에 가이드 주입." },
      {
        name: "capability",
        description: "Capability 매칭 — capability_params 의 도구를 자동 합류.",
      },
      { name: "none", description: "Strategy 비활성.", is_default: true },
    ];
  }
}
