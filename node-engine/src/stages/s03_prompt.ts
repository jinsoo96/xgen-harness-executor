import { BaseStage, type StrategyInfo } from "../pipeline/stage";
import type { PipelineState } from "../types";
import type { FrozenToolDefinition } from "../spec/schema";
import {
  STAGE_TOPOLOGY,
  STAGE_TAG_GROUPS,
  DEFAULT_RULES_RESTRICTIONS,
} from "./_topology";

/**
 * S03 Prompt — system_prompt 조립.
 *
 * Python `xgen_harness.stages.s03_prompt.stage.SystemPromptStage` 와 동일한
 * 환경 노출 영역을 LLM 에 박는다. PD 자율성이 cluster 와 외부 산출물에서
 * 동일하게 작동하도록 같은 fact 를 같은 형식으로 노출.
 *
 * 우선순위 순서 (위에서부터):
 *   1. <identity>            — 사용자 system_prompt (spec.config.system_prompt)
 *   2. <rules>               — DEFAULT_RULES_RESTRICTIONS (사용자 검증 패턴)
 *   3. <active_resources>    — spec.config 의 자원 (RAG / MCP / DB / Ontology / files)
 *   4. <harness_stages>      — stage 토폴로지 (단일 진리원본)
 *   5. <meta_tools_by_stage> — 도구 tags/category → stage 자동 그룹
 *
 * 행동 강제 톤 (MUST / 합성 강제) X. 환경 fact 만.
 */
export class S03Prompt extends BaseStage {
  readonly stage_id = "s03_prompt";
  readonly display_name = "Prompt";
  readonly display_name_ko = "프롬프트";
  readonly phase = "ingress" as const;
  readonly order = 3;

  async execute(state: PipelineState): Promise<Record<string, unknown>> {
    const sections: Array<[number, string, string]> = [];

    // 1. <identity> — 사용자 system_prompt
    const userSp = (state.config.system_prompt || "").trim();
    if (userSp) {
      sections.push([1, "identity", userSp]);
    }

    // 2. <rules> — RESTRICTIONS_ONLY 사용자 검증 패턴
    sections.push([2, "rules", DEFAULT_RULES_RESTRICTIONS]);

    // 3. <active_resources>
    const activeResources = this.buildActiveResources(state);
    if (activeResources) sections.push([2.5, "active_resources", activeResources]);

    // 4. <harness_stages>
    sections.push([2.7, "harness_stages", this.buildHarnessStages()]);

    // 5. <meta_tools_by_stage>
    const metaTools = this.buildMetaToolsByStage(state);
    if (metaTools) sections.push([2.8, "meta_tools_by_stage", metaTools]);

    sections.sort((a, b) => a[0] - b[0]);
    const assembled = sections.map(([, , content]) => content).join("\n\n");
    state.metadata.system_prompt = assembled;

    return {
      prompt_chars: assembled.length,
      sections: sections.map(([, name]) => name),
    };
  }

  private buildActiveResources(state: PipelineState): string {
    const cfg = state.config as Record<string, unknown>;
    const lines: string[] = [];

    const rag = (cfg.rag_collections as unknown[]) || [];
    const ontology = (cfg.ontology_collections as unknown[]) || [];
    const mcp = (cfg.mcp_sessions as unknown[]) || [];
    const db = (cfg.db_connections as unknown[]) || [];
    const files = (cfg.files as unknown[]) || [];
    const folders = (cfg.folders as unknown[]) || [];

    if (rag.length === 0 && ontology.length === 0 && mcp.length === 0 &&
        db.length === 0 && files.length === 0 && folders.length === 0) {
      return "";
    }

    lines.push("<active_resources>");
    lines.push("These resources are attached to the workflow. Each item below pairs with → the tool that operates on it.");

    if (rag.length > 0) {
      lines.push("- 문서 (의미적 유사도 검색 → rag_search(query, collection_name)):");
      for (const col of rag) {
        if (typeof col === "string") lines.push(`  · ${col}`);
        else if (col && typeof col === "object") {
          const c = col as Record<string, unknown>;
          const name = (c.make_name as string) || (c.name as string) || (c.collection_name as string) || "";
          const desc = (c.description as string) || "";
          const total = (c.total_documents as number) || 0;
          let line = `  · ${name}`;
          if (desc) line += `: ${desc}`;
          if (total) line += ` (${total.toLocaleString()} docs)`;
          lines.push(line);
        }
      }
    }

    if (ontology.length > 0) {
      lines.push("- 지식 그래프 (관계·계층 검색 → query_graph(question, collection)):");
      for (const col of ontology) {
        lines.push(`  · ${typeof col === "string" ? col : JSON.stringify(col)}`);
      }
    }

    if (mcp.length > 0) {
      lines.push("- MCP 세션 (→ 각 세션의 도구로 노출):");
      for (const s of mcp) {
        const sid = typeof s === "string" ? s : (s as Record<string, unknown>)?.session_id || JSON.stringify(s);
        lines.push(`  · ${sid}`);
      }
    }

    if (db.length > 0) {
      lines.push("- 데이터베이스 연결 (→ 매칭 DB 도구):");
      for (const c of db) {
        lines.push(`  · ${typeof c === "string" ? c : JSON.stringify(c)}`);
      }
    }

    if (files.length > 0) {
      lines.push(`- 파일 ${files.length}개 (→ 파일/문서 도구)`);
    }
    if (folders.length > 0) {
      lines.push(`- 폴더 ${folders.length}개 (→ 파일/문서 도구)`);
    }

    lines.push("</active_resources>");
    return lines.join("\n");
  }

  private buildHarnessStages(): string {
    const lines = ["<harness_stages>"];
    for (const st of STAGE_TOPOLOGY) {
      lines.push(`- ${st.id} (${st.label}): ${st.desc}`);
    }
    lines.push("</harness_stages>");
    return lines.join("\n");
  }

  private buildMetaToolsByStage(state: PipelineState): string {
    const defs: FrozenToolDefinition[] = state.tool_definitions || [];
    if (defs.length === 0) return "";

    const groups: Map<string, string[]> = new Map();
    const unmapped: string[] = [];

    for (const td of defs) {
      const name = td.name;
      if (!name) continue;
      const tagsList = td.tags || [];
      const haystack = new Set<string>(tagsList.map((t) => String(t).toLowerCase()));
      // FrozenToolDefinition 에는 category 필드 없음 — call_kind 기반 보강.
      if (td.call_kind && typeof td.call_kind === "string") {
        if (td.call_kind.startsWith("builtin:")) haystack.add("builtin");
        if (td.call_kind === "rag") haystack.add("rag");
        if (td.call_kind === "mcp_session") haystack.add("mcp");
      }

      let matched: string | null = null;
      for (const [stageId, tagSet] of STAGE_TAG_GROUPS) {
        for (const t of haystack) {
          if (tagSet.has(t)) {
            matched = stageId;
            break;
          }
        }
        if (matched) break;
      }
      if (matched === null) unmapped.push(name);
      else {
        if (!groups.has(matched)) groups.set(matched, []);
        groups.get(matched)!.push(name);
      }
    }

    if (groups.size === 0 && unmapped.length === 0) return "";

    const lines = ["<meta_tools_by_stage>"];
    for (const [stageId] of STAGE_TAG_GROUPS) {
      const tools = groups.get(stageId);
      if (!tools || tools.length === 0) continue;
      lines.push(`- ${stageId}: ${tools.join(", ")}`);
    }
    if (unmapped.length > 0) {
      lines.push(`- 기타: ${unmapped.join(", ")}`);
    }
    lines.push("</meta_tools_by_stage>");
    return lines.join("\n");
  }

  listStrategies(): StrategyInfo[] {
    return [
      {
        name: "section_priority",
        description: "identity → rules → active_resources → harness_stages → meta_tools_by_stage 순.",
        is_default: true,
      },
    ];
  }
}
