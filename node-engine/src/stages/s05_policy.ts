/**
 * S05 Policy Gate — Guard chain.
 *
 * Python `xgen_harness.stages.s05_policy` 1:1 포팅:
 *   - cost_cap   : 누적 cost_usd 가 max_usd 초과 시 block
 *   - max_loop   : iteration 이 max 초과 시 block
 *   - pii_block  : assistant_text / user_input 에 PII 감지 시 block
 *   - domain_allow: tool_call URL 또는 tool name 이 allowed 에 없으면 block
 *
 * 외부 Guard 추가는 `registerGuard()` — 엔진 내부 레지스트리. (Python 의
 * entry_points 와 동등한 확장점.)
 */

import { BaseStage, type StrategyInfo } from "../pipeline/stage";
import type { PipelineState } from "../types";

export interface GuardSpec {
  /** Guard 식별자. spec.config.stage_params.s05_policy.guards[].name 와 매칭. */
  name: string;
  /** 호출자가 박은 정책 파라미터 (max_usd / max_loop / patterns 등). */
  params?: Record<string, unknown>;
}

export interface GuardEvalResult {
  blocked: boolean;
  reason?: string;
  meta?: Record<string, unknown>;
}

export type GuardImpl = (
  state: PipelineState,
  params: Record<string, unknown>,
) => GuardEvalResult | Promise<GuardEvalResult>;

const REGISTRY = new Map<string, GuardImpl>();

export function registerGuard(name: string, impl: GuardImpl): void {
  REGISTRY.set(name, impl);
}

// ─── 빌트인 Guard 4종 ───────────────────────────────────────────

const guardCostCap: GuardImpl = (state, params) => {
  const max = Number(params.max_usd ?? params.usd ?? 0);
  if (max <= 0) return { blocked: false };
  if (state.cost_usd >= max) {
    return {
      blocked: true,
      reason: `cost_cap: $${state.cost_usd.toFixed(4)} >= max=$${max}`,
      meta: { cost_usd: state.cost_usd, max_usd: max },
    };
  }
  return { blocked: false };
};

const guardMaxLoop: GuardImpl = (state, params) => {
  const max = Number(params.max ?? params.max_iterations ?? 0);
  if (max <= 0) return { blocked: false };
  if (state.iteration >= max) {
    return {
      blocked: true,
      reason: `max_loop: iteration=${state.iteration} >= max=${max}`,
      meta: { iteration: state.iteration, max },
    };
  }
  return { blocked: false };
};

const PII_PATTERNS_DEFAULT: Array<[string, RegExp]> = [
  // 한국 주민등록번호 (앞6-뒤7)
  ["rrn_kr", /\b\d{6}[-\s]?\d{7}\b/],
  // 카드번호 (4-4-4-4)
  ["card_4x4", /\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b/],
  // 이메일
  ["email", /\b[\w.+-]+@[\w-]+\.[\w.-]+\b/],
  // 한국 전화번호 (010-xxxx-xxxx 등)
  ["phone_kr", /\b01[016789][-\s]?\d{3,4}[-\s]?\d{4}\b/],
];

const guardPiiBlock: GuardImpl = (state, params) => {
  const target = String(params.target ?? "both"); // 'input' | 'output' | 'both'
  const customPatterns = (params.patterns as Array<{ name: string; regex: string }>) || [];
  const patterns: Array<[string, RegExp]> = [...PII_PATTERNS_DEFAULT];
  for (const p of customPatterns) {
    try {
      patterns.push([p.name, new RegExp(p.regex)]);
    } catch {
      // skip invalid
    }
  }
  const haystacks: Array<[string, string]> = [];
  if (target === "input" || target === "both") {
    haystacks.push(["user_input", state.user_input]);
  }
  if (target === "output" || target === "both") {
    haystacks.push(["assistant", state.last_assistant_text]);
  }
  for (const [where, text] of haystacks) {
    for (const [pname, pre] of patterns) {
      if (pre.test(text || "")) {
        return {
          blocked: true,
          reason: `pii_block: ${pname} detected in ${where}`,
          meta: { pattern: pname, where },
        };
      }
    }
  }
  return { blocked: false };
};

const guardDomainAllow: GuardImpl = (state, params) => {
  const allowed = Array.isArray(params.allowed_domains)
    ? (params.allowed_domains as string[])
    : [];
  const allowedTools = Array.isArray(params.allowed_tools)
    ? (params.allowed_tools as string[])
    : [];
  if (allowed.length === 0 && allowedTools.length === 0) {
    return { blocked: false };
  }
  // pending tool calls 검사 — 다음 dispatch 전에 차단 가능.
  for (const tc of state.pending_tool_calls) {
    if (allowedTools.length && !allowedTools.includes(tc.tool_name)) {
      return {
        blocked: true,
        reason: `domain_allow: tool '${tc.tool_name}' 허용 목록 외`,
        meta: { tool: tc.tool_name, allowed_tools: allowedTools },
      };
    }
    if (allowed.length) {
      const def = state.tool_definitions.find((t) => t.name === tc.tool_name);
      const url = (def?.call_spec.url as string) || "";
      if (url) {
        const host = (() => {
          try {
            return new URL(url).host;
          } catch {
            return "";
          }
        })();
        const ok = allowed.some((d) => host === d || host.endsWith("." + d));
        if (!ok) {
          return {
            blocked: true,
            reason: `domain_allow: ${host} 허용 도메인 외`,
            meta: { host, allowed_domains: allowed },
          };
        }
      }
    }
  }
  return { blocked: false };
};

// 빌트인 Guard 등록
registerGuard("cost_cap", guardCostCap);
registerGuard("max_loop", guardMaxLoop);
registerGuard("pii_block", guardPiiBlock);
registerGuard("domain_allow", guardDomainAllow);

// ─── Stage 본문 ──────────────────────────────────────────────────

export class S05Policy extends BaseStage {
  readonly stage_id = "s05_policy";
  readonly display_name = "Policy Gate";
  readonly display_name_ko = "정책 게이트";
  readonly phase = "ingress" as const;
  readonly order = 5;

  shouldBypass(state: PipelineState): boolean {
    const guards = this.getParam<GuardSpec[]>("guards", state, []);
    return !guards || guards.length === 0;
  }

  async execute(state: PipelineState): Promise<Record<string, unknown>> {
    const guards = this.getParam<GuardSpec[]>("guards", state, []);
    const evaluated: Array<{ name: string; blocked: boolean; reason?: string }> = [];
    for (const g of guards) {
      const impl = REGISTRY.get(g.name);
      if (!impl) {
        evaluated.push({ name: g.name, blocked: false, reason: "guard not registered (skipped)" });
        continue;
      }
      try {
        const r = await impl(state, g.params || {});
        evaluated.push({ name: g.name, blocked: r.blocked, reason: r.reason });
        if (r.blocked) {
          state.policy_block_reason = r.reason || g.name;
          return {
            blocked: true,
            blocking_guard: g.name,
            reason: r.reason,
            evaluated,
          };
        }
      } catch (e) {
        evaluated.push({
          name: g.name,
          blocked: false,
          reason: `guard exception (treated as pass): ${(e as Error).message}`,
        });
      }
    }
    return { blocked: false, evaluated };
  }

  listStrategies(): StrategyInfo[] {
    return [];
  }
}
