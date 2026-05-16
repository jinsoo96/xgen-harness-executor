/**
 * Harness Stage Topology — 단일 진리원본.
 *
 * Python `xgen_harness.stages.s03_prompt.stage.STAGE_TOPOLOGY` 와 동기 (수동).
 * 두 곳 (Python cluster runtime + node-engine 외부 산출물) 이 LLM 에게 동일한
 * 환경 토폴로지 fact 를 노출해야 PD 자율성이 양쪽에서 동일하게 작동.
 *
 * 행동 강제 X. fact 만.
 */

export interface StageTopologyEntry {
  id: string;
  label: string;
  desc: string;
}

export const STAGE_TOPOLOGY: StageTopologyEntry[] = [
  { id: "s00_harness", label: "Harness", desc: "하네스 진입/종료. Planner orchestrator role." },
  { id: "s01_input", label: "Input", desc: "사용자 입력 + external_inputs 결합." },
  { id: "s02_history", label: "History", desc: "대화 이력 + memory_collection." },
  { id: "s03_prompt", label: "Prompt", desc: "system_prompt 조립 (이 stage)." },
  { id: "s04_tool", label: "Tool", desc: "도구 카탈로그 indexing + capability binding + PD builtin 합류." },
  { id: "s05_policy", label: "Policy", desc: "policy_pack 적용." },
  { id: "s06_context", label: "Context", desc: "맥락 관리 — RAG/Ontology 자동 search 폐기, 도구로 위임." },
  { id: "s07_act", label: "Act", desc: "도구 디스패치 (sequential / parallel_read / strict_no_error)." },
  { id: "s08_decide", label: "Decide", desc: "loop 결정 (judge)." },
  { id: "s09_judge", label: "Judge", desc: "응답 평가 / loop 종료 판단." },
  { id: "s10_finalize", label: "Finalize", desc: "egress + done event." },
];

/**
 * 도구 tag / category → stage 매핑. 도구 자체 메타 기반 자동 그룹화.
 * 도구가 여러 stage 에 묶이면 첫 매칭. 매핑 안 되면 "기타".
 */
export const STAGE_TAG_GROUPS: Array<[string, Set<string>]> = [
  ["s04_tool", new Set(["builtin", "pd", "system"])],
  ["s06_context", new Set(["rag", "ontology"])],
  ["s07_act", new Set(["mcp", "api", "search", "synthesis", "web", "http", "tools", "skill"])],
  ["s09_judge", new Set(["judge"])],
];

/**
 * v1.8.0 RESTRICTIONS_ONLY 정합 — 사용자 검증 패턴 (Qwen +31% / Claude 식 short directive).
 * Python `DEFAULT_RULES["default"]` 와 동기.
 */
export const DEFAULT_RULES_RESTRICTIONS = [
  "<rules>",
  "If <active_resources> lists ANY resource → TRY the matching tool BEFORE saying \"no tools available\". Don't claim absence without trying.",
  "Don't call the same tool with the same args twice.",
  "Don't repeat a search query that returned 0 results — change keywords or stop.",
  "Don't call discover_collection on the same collection name twice.",
  "Don't keep trying after all attached collections returned empty — STOP and tell the user.",
  "Don't speculate when tools return no data — say \"no relevant data found\" and stop.",
  "Don't fetch_pd the same id twice — it's idempotent.",
  "Don't add filler. Lead with the answer, not the reasoning.",
  "Trust tool results — don't second-guess them.",
  "Cite source when using reference documents.",
  "Use the same language as the user.",
  "If a tool fails, try an alternative ONCE — don't keep retrying.",
  "If exhausted, report briefly and STOP. Don't loop.",
  "</rules>",
].join("\n");
