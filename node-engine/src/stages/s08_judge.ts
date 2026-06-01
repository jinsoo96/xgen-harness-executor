/**
 * S08 Judge — 응답 품질 평가.
 *
 * Python `xgen_harness.stages.s08_judge` 1:1 포팅:
 *   - none (default): 평가 비활성, 비용 보호
 *   - llm_judge: 별도 provider 호출, 4 기준 (relevance/completeness/accuracy/clarity)
 *                 가중평균. score < threshold 이면 retry 신호 (state.validation_score).
 *   - rule_based: LLM 비호출. 길이/금칙어 매칭.
 */

import { BaseStage, type StrategyInfo } from "../pipeline/stage";
import type { PipelineState } from "../types";
import { createProvider } from "../providers/base";

const ALL_CRITERIA: Record<string, { description: string; weight: number }> = {
  relevance: {
    description: "Does the response address the user's question?",
    weight: 0.3,
  },
  completeness: {
    description: "Is the response thorough and complete?",
    weight: 0.3,
  },
  accuracy: {
    description: "Is the information accurate and well-supported?",
    weight: 0.2,
  },
  clarity: {
    description: "Is the response clear and well-organized?",
    weight: 0.2,
  },
};

export class S08Judge extends BaseStage {
  readonly stage_id = "s08_judge";
  readonly display_name = "Judge";
  readonly display_name_ko = "판정";
  readonly phase = "loop" as const;
  readonly order = 8;

  shouldBypass(state: PipelineState): boolean {
    return !state.last_assistant_text;
  }

  async execute(state: PipelineState): Promise<Record<string, unknown>> {
    const strategy = this.resolveStrategyName(state, "none");
    if (strategy === "none") {
      return { bypassed: true, reason: "judge=none" };
    }
    if (strategy === "rule_based") {
      return this.executeRuleBased(state);
    }
    if (strategy === "llm_judge") {
      return await this.executeLlmJudge(state);
    }
    return { strategy, deferred: true };
  }

  private executeRuleBased(state: PipelineState): Record<string, unknown> {
    const text = state.last_assistant_text;
    let score = 1.0;
    const feedback: string[] = [];
    if (text.length < 20) {
      score *= 0.6;
      feedback.push("응답이 너무 짧음 (<20자)");
    }
    const banned = this.getParam<string[]>("banned_keywords", state, []);
    for (const k of banned) {
      if (text.includes(k)) {
        score *= 0.3;
        feedback.push(`금칙어 감지: ${k}`);
      }
    }
    state.validation_score = score;
    state.validation_feedback = feedback.join("; ");
    return {
      strategy: "rule_based",
      score,
      verdict: score >= 0.7 ? "pass" : "fail",
      feedback: state.validation_feedback,
    };
  }

  private async executeLlmJudge(state: PipelineState): Promise<Record<string, unknown>> {
    const userInput = state.user_input.slice(0, 500);
    const assistantResp = state.last_assistant_text.slice(0, 2000);
    // v1.17.0 — criteria_defs(config self-contained 정의)로 외부 산출물에서도 평가축 유지.
    // 컴파일된 npm 산출물은 register 호출이 없어 ALL_CRITERIA 에 사용자 축(criterion_N)이
    // 없으므로, config 에 실린 정의를 직접 읽어 effective 맵을 만든다.
    const { names: activeCriteria, defs: effectiveCriteria } = this.resolveCriteria(state);
    if (activeCriteria.length === 0) {
      return { strategy: "llm_judge", error: "no valid criteria" };
    }

    const prompt = this.buildEvaluationPrompt(
      userInput,
      assistantResp,
      activeCriteria,
      effectiveCriteria,
    );

    let provider;
    try {
      provider = createProvider({
        provider: state.config.provider || "anthropic",
        apiKey:
          process.env[`${(state.config.provider || "anthropic").toUpperCase()}_API_KEY`] || "",
        model: state.config.model || "claude-haiku-4-5-20251001",
      });
    } catch (e) {
      return {
        strategy: "llm_judge",
        error: `provider init failed: ${(e as Error).message}`,
        score: 0.7,
        verdict: "pass",
      };
    }

    let raw = "";
    try {
      const stream = provider.chat({
        messages: [{ role: "user", content: prompt }],
        max_tokens: Number(state.config.aux_max_tokens || 500),
        temperature: 0.0,
        stream: true,
      });
      for await (const ev of stream) {
        if (ev.type === "text_delta" && ev.text) raw += ev.text;
        if (ev.type === "stop") break;
      }
    } catch (e) {
      return {
        strategy: "llm_judge",
        error: `judge call failed: ${(e as Error).message}`,
        score: 0.7,
        verdict: "pass",
      };
    }

    const parsed = this.parseEvaluation(raw, activeCriteria, effectiveCriteria);
    state.validation_score = parsed.score;
    state.validation_feedback = parsed.feedback;
    return {
      strategy: "llm_judge",
      score: parsed.score,
      verdict: parsed.verdict,
      criteria_scores: parsed.scores,
      feedback: parsed.feedback,
      raw_response: raw.slice(0, 500),
    };
  }

  /**
   * v1.17.0 — config 의 criteria(이름) + criteria_defs(self-contained 정의)를 해소해
   * effective 평가축 맵을 만든다. 우선순위 inline 정의 > ALL_CRITERIA 레지스트리.
   * 컴파일된 산출물은 레지스트리가 비어있어도 criteria_defs 로 사용자 축이 유지된다.
   */
  private resolveCriteria(state: PipelineState): {
    names: string[];
    defs: Record<string, { description: string; weight: number }>;
  } {
    const criteria = this.getParam<unknown[]>(
      "criteria",
      state,
      ["relevance", "completeness", "accuracy", "clarity"],
    );
    const criteriaDefs = this.getParam<unknown[]>("criteria_defs", state, []);

    const inline: Record<string, { description: string; weight: number }> = {};
    if (Array.isArray(criteriaDefs)) {
      for (const d of criteriaDefs) {
        if (!d || typeof d !== "object") continue;
        const o = d as Record<string, unknown>;
        const nm = String(o.name ?? "").trim();
        if (!nm) continue;
        const w = Number(o.weight);
        inline[nm] = {
          description: String(o.description ?? ""),
          weight: isFinite(w) ? w : 0.1,
        };
      }
    }

    const defs: Record<string, { description: string; weight: number }> = {};
    const names: string[] = [];
    const items = Array.isArray(criteria) ? criteria : [];
    for (const item of items) {
      if (item && typeof item === "object") {
        const o = item as Record<string, unknown>;
        const nm = String(o.name ?? "").trim();
        if (!nm) continue;
        const w = Number(o.weight);
        defs[nm] = { description: String(o.description ?? ""), weight: isFinite(w) ? w : 0.1 };
        names.push(nm);
      } else {
        const nm = String(item).trim();
        if (!nm) continue;
        if (inline[nm]) {
          defs[nm] = inline[nm];
          names.push(nm);
        } else if (ALL_CRITERIA[nm]) {
          defs[nm] = ALL_CRITERIA[nm];
          names.push(nm);
        }
      }
    }
    if (names.length === 0) {
      // 아무것도 해소 못함 — inline 전체 또는 ALL_CRITERIA 폴백.
      const src = Object.keys(inline).length > 0 ? inline : ALL_CRITERIA;
      for (const [nm, m] of Object.entries(src)) {
        defs[nm] = m;
        names.push(nm);
      }
    }
    return { names, defs };
  }

  private buildEvaluationPrompt(
    userInput: string,
    assistantResp: string,
    criteria: string[],
    defs: Record<string, { description: string; weight: number }>,
  ): string {
    const lines = [
      "Evaluate the assistant's response on the following criteria. Return JSON only.",
      "",
      "User question:",
      userInput,
      "",
      "Assistant response:",
      assistantResp,
      "",
      "Criteria:",
    ];
    for (const c of criteria) {
      const m = defs[c] ?? ALL_CRITERIA[c] ?? { description: "", weight: 0.1 };
      lines.push(`- ${c} (weight ${m.weight}): ${m.description}`);
    }
    lines.push("");
    lines.push("Output JSON in this exact shape:");
    lines.push(
      '{"scores": {"' +
        criteria.join('": <0..1>, "') +
        '": <0..1>}, "feedback": "<one-sentence summary>"}',
    );
    return lines.join("\n");
  }

  private parseEvaluation(
    raw: string,
    criteria: string[],
    defs: Record<string, { description: string; weight: number }>,
  ): {
    score: number;
    verdict: "pass" | "fail";
    feedback: string;
    scores: Record<string, number>;
  } {
    let parsed: any = null;
    // JSON 추출 — 응답에 markdown fence 가 있을 수도 있어 대안 매칭.
    try {
      const start = raw.indexOf("{");
      const end = raw.lastIndexOf("}");
      if (start >= 0 && end > start) {
        parsed = JSON.parse(raw.slice(start, end + 1));
      }
    } catch {
      parsed = null;
    }
    if (!parsed || typeof parsed !== "object") {
      return {
        score: 0.7,
        verdict: "pass",
        feedback: "Evaluation parsing failed, assuming acceptable",
        scores: {},
      };
    }
    const scores: Record<string, number> = {};
    let weighted = 0;
    let totalWeight = 0;
    for (const c of criteria) {
      const m = defs[c] ?? ALL_CRITERIA[c] ?? { description: "", weight: 0.1 };
      const v = Number(parsed.scores?.[c] ?? 0.7);
      const clamped = Math.max(0, Math.min(1, isFinite(v) ? v : 0.7));
      scores[c] = clamped;
      weighted += clamped * m.weight;
      totalWeight += m.weight;
    }
    const score = totalWeight > 0 ? weighted / totalWeight : 0.7;
    return {
      score,
      verdict: score >= 0.7 ? "pass" : "fail",
      feedback: String(parsed.feedback || "").slice(0, 500),
      scores,
    };
  }

  listStrategies(): StrategyInfo[] {
    return [
      { name: "none", description: "검증 비활성화 (기본).", is_default: true },
      { name: "llm_judge", description: "독립 LLM 으로 4 기준 평가." },
      { name: "rule_based", description: "규칙 기반 (길이/금칙어)." },
    ];
  }
}
