/**
 * S04 Tool Index — spec.tool_definitions (frozen) + selected_tools 화이트리스트.
 *
 * Python s04 와의 차이: 외부 ToolSource 동적 발견 X — publish 시점에 spec 에
 * 박힌 도구만 사용 (fully equivalent — publish 시점 도구 카탈로그 freeze).
 */

import { BaseStage, type StrategyInfo } from "../pipeline/stage";
import type { PipelineState } from "../types";
import type { FrozenToolDefinition } from "../spec/schema";
import {
  buildPdBuiltinDefinitions,
  BUILTIN_SEARCH_TOOLS_NAME,
  BUILTIN_DISCOVER_TOOLS_NAME,
} from "../tools/builtins";

export class S04Tool extends BaseStage {
  readonly stage_id = "s04_tool";
  readonly display_name = "Tool";
  readonly display_name_ko = "도구";
  readonly phase = "ingress" as const;
  readonly order = 4;

  shouldBypass(state: PipelineState): boolean {
    const strategy = this.resolveStrategyName(state, "progressive_3level");
    if (strategy === "none") return true;
    return false;
  }

  async execute(state: PipelineState): Promise<Record<string, unknown>> {
    const strategy = this.resolveStrategyName(state, "progressive_3level");
    if (strategy === "none") {
      return { strategy: "none", tools_indexed: 0 };
    }

    // selected_tools — list (글로벌) 또는 dict ({source_id: [name]}) 양쪽 허용.
    const selRaw = this.getParam<unknown>("selected_tools", state, {});
    let globalAllow: Set<string> | null = null;
    let perSource: Record<string, string[]> = {};
    if (Array.isArray(selRaw)) {
      globalAllow = new Set((selRaw as unknown[]).map((x) => String(x)));
    } else if (selRaw && typeof selRaw === "object") {
      perSource = selRaw as Record<string, string[]>;
    }

    // spec.tool_definitions — publish 시 freeze 된 카탈로그.
    const all = (state.metadata.spec_tool_definitions as FrozenToolDefinition[]) || [];

    const out: FrozenToolDefinition[] = [];
    for (const t of all) {
      if (globalAllow && !globalAllow.has(t.name)) continue;
      // perSource: source_id 키가 frozen tool 의 call_kind 또는 tag 와 매칭되는 케이스
      if (Object.keys(perSource).length) {
        const tags = new Set(t.tags || []);
        const matches = Object.entries(perSource).some(([sid, names]) => {
          if (!names || names.length === 0) return false;
          if (!names.includes(t.name)) return false;
          // sid 가 정확한 tag 면 제한, 아니면 전체 통과
          return tags.size === 0 || tags.has(sid);
        });
        if (!matches) continue;
      }
      out.push(t);
    }

    // ─── capability binding ──────────────────────────────────────
    // spec.config.capabilities 에 박힌 capability 이름이 도구 tag 와 매칭되면
    // 해당 도구를 추가로 합류 (selected_tools 화이트리스트와 별개 채널).
    // capability_params 의 요청 스키마를 metadata 에 stamp.
    const caps = state.config.capabilities || [];
    let capabilityBound = 0;
    if (caps.length > 0) {
      const capSet = new Set(caps);
      const existingNames = new Set(out.map((t) => t.name));
      for (const t of all) {
        if (existingNames.has(t.name)) continue;
        const tagMatch = (t.tags || []).some((tg) => capSet.has(tg));
        // capability:{name} prefix 도 허용 (예: tags=["capability:search"])
        const prefixMatch = (t.tags || []).some(
          (tg) => tg.startsWith("capability:") && capSet.has(tg.slice("capability:".length)),
        );
        if (tagMatch || prefixMatch) {
          out.push(t);
          capabilityBound++;
          existingNames.add(t.name);
        }
      }
      state.metadata.capabilities_bound = capabilityBound;
      state.metadata.capabilities_active = caps;
    }

    // ─── PD builtin 자동 합류 ─────────────────────────────────────
    // strategy 가 "progressive_3level" (기본) 이고 frozen 도구가 있으면 LLM 이
    // 카탈로그를 자율적으로 탐색할 수 있도록 search_tools / discover_tools 두
    // 빌트인을 카탈로그에 함께 노출. Python cluster runtime 의 builtin 등록과 동등.
    let pdBuiltinAdded = 0;
    if (strategy !== "eager_load" && out.length > 0) {
      const existing = new Set(out.map((t) => t.name));
      for (const td of buildPdBuiltinDefinitions()) {
        if (existing.has(td.name)) continue;
        // builtin 도 globalAllow 가 있으면 거기에 들어있을 때만 합류.
        if (globalAllow && !globalAllow.has(td.name)) continue;
        out.push(td);
        pdBuiltinAdded++;
      }
    }

    state.tool_definitions = out;
    // annotations payload 분리 (Python state.tool.annotations)
    for (const t of out) {
      if (t.annotations && Object.keys(t.annotations).length) {
        state.tool.annotations[t.name] = t.annotations;
      }
    }
    return {
      strategy,
      tools_indexed: out.length,
      definitions_bound: out.length,
      capabilities_bound: capabilityBound,
      pd_builtin_added: pdBuiltinAdded,
    };
  }

  listStrategies(): StrategyInfo[] {
    return [
      {
        name: "progressive_3level",
        description: "Names+desc 1차 노출 후 LLM 이 필요한 도구만 상세 조회 (토큰 절감).",
        is_default: true,
      },
      { name: "eager_load", description: "전체 스키마 일괄 노출." },
      { name: "none", description: "도구 비활성." },
    ];
  }
}
