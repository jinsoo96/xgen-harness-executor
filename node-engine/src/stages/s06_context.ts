/**
 * S06 Context — RAG 검색 + 컨텍스트 정리 strategies.
 *
 * Python `xgen_harness.stages.s06_context` 1:1 포팅:
 *   - token_budget: messages 토큰 합 budget 초과 시 oldest user/assistant pair 제거
 *   - sliding_window: 최근 N 메시지만 유지 (system 제외)
 *   - microcompact: 오래된 messages 합을 LLM 으로 1줄 요약
 *   - context_collapse_overlay: pair-wise 압축 (역행)
 *   - autocompact_llm: LLM 이 어떤 chunk 압축할지 결정
 *   - cascade: 위 strategies 를 순차 적용
 *
 * RAG dispatch: spec.tool_definitions 의 call_kind=rag 항목을 자동 호출 →
 * 결과를 system 메시지에 주입.
 */

import { BaseStage, type StrategyInfo } from "../pipeline/stage";
import type { PipelineState, Message } from "../types";
import { dispatchToolCall } from "../tools/dispatch";

const TOKEN_BUDGET_DEFAULT = 100_000;
const SLIDING_WINDOW_DEFAULT = 20;
const MICROCOMPACT_THRESHOLD = 50_000;
const RAG_CONTEXT_HEADER = "\n\n## Retrieved Context\n";

export class S06Context extends BaseStage {
  readonly stage_id = "s06_context";
  readonly display_name = "Context";
  readonly display_name_ko = "컨텍스트";
  readonly phase = "loop" as const;
  readonly order = 6;

  shouldBypass(state: PipelineState): boolean {
    const collections = this.getParam<unknown[]>("rag_collections", state, []);
    const hasRag = collections && collections.length > 0;
    const messageTokens = estimateMessageTokens(state.messages);
    const budget = Number(this.getParam("token_budget", state, TOKEN_BUDGET_DEFAULT));
    // RAG 도 없고 budget 넘지도 않으면 skip
    return !hasRag && messageTokens < budget;
  }

  async execute(state: PipelineState): Promise<Record<string, unknown>> {
    const strategy = this.resolveStrategyName(state, "token_budget");

    // 1) RAG dispatch — frozen tool 의 call_kind=rag 자동 호출
    let ragChunks = 0;
    let ragText = "";
    const ragCollections = this.getParam<string[]>("rag_collections", state, []);
    if (ragCollections.length > 0) {
      const ragTools = state.tool_definitions.filter((t) => t.call_kind === "rag");
      for (const t of ragTools) {
        try {
          const r = await dispatchToolCall(t, { query: state.user_input }, state);
          if (!r.is_error && r.content) {
            ragText += r.content + "\n\n";
            ragChunks++;
          }
        } catch {
          // skip
        }
      }
      if (ragText) {
        // system 메시지에 컨텍스트 주입 (또는 metadata 에 박아 s03_prompt 가 합치게)
        const sys = (state.metadata.system_prompt as string) || "";
        state.metadata.system_prompt = sys + RAG_CONTEXT_HEADER + ragText;
      }
    }

    // 2) 컨텍스트 정리 strategy
    const budget = Number(this.getParam("token_budget", state, TOKEN_BUDGET_DEFAULT));
    let beforeTokens = estimateMessageTokens(state.messages);
    let compacted = false;

    switch (strategy) {
      case "token_budget":
        compacted = applyTokenBudget(state, budget);
        break;
      case "sliding_window":
        compacted = applySlidingWindow(
          state,
          Number(this.getParam("window", state, SLIDING_WINDOW_DEFAULT)),
        );
        break;
      case "microcompact":
        compacted = await applyMicrocompact(state, budget);
        break;
      case "context_collapse_overlay":
        compacted = applyCollapseOverlay(state, budget);
        break;
      case "autocompact_llm":
        compacted = await applyAutocompactLlm(state, budget);
        break;
      case "cascade":
        compacted =
          (await applyMicrocompact(state, budget)) ||
          applyTokenBudget(state, budget);
        break;
      default:
        // 알 수 없는 strategy — token_budget 로 폴백
        compacted = applyTokenBudget(state, budget);
    }

    const afterTokens = estimateMessageTokens(state.messages);
    return {
      strategy,
      rag_chunks: ragChunks,
      rag_collections: ragCollections.length,
      compacted,
      tokens_before: beforeTokens,
      tokens_after: afterTokens,
      estimated_tokens: afterTokens,
      budget_used: budget > 0 ? afterTokens / budget : 0,
    };
  }

  listStrategies(): StrategyInfo[] {
    return [
      { name: "token_budget", description: "토큰 budget 초과 시 oldest 제거.", is_default: true },
      { name: "sliding_window", description: "최근 N 메시지만 유지." },
      { name: "microcompact", description: "오래된 messages 를 LLM 1줄 요약." },
      { name: "context_collapse_overlay", description: "Pair-wise 압축." },
      { name: "autocompact_llm", description: "LLM 이 압축 대상 결정." },
      { name: "cascade", description: "여러 strategy 순차 적용." },
    ];
  }
}

// ─── 토큰 추정 (tiktoken 없이 chars/4 휴리스틱) ────────────────────

function estimateMessageTokens(messages: Message[]): number {
  let total = 0;
  for (const m of messages) {
    if (typeof m.content === "string") {
      total += Math.ceil(m.content.length / 4);
    } else {
      for (const block of m.content) {
        if ((block as any).type === "text") {
          total += Math.ceil(((block as any).text || "").length / 4);
        } else if ((block as any).type === "tool_result") {
          const c = (block as any).content;
          total += Math.ceil(((typeof c === "string" ? c : JSON.stringify(c)) || "").length / 4);
        } else if ((block as any).type === "tool_use") {
          total += Math.ceil(JSON.stringify((block as any).input || {}).length / 4);
        }
      }
    }
  }
  return total;
}

// ─── token_budget — oldest 부터 제거 ────────────────────────────

function applyTokenBudget(state: PipelineState, budget: number): boolean {
  if (budget <= 0) return false;
  let tokens = estimateMessageTokens(state.messages);
  if (tokens <= budget) return false;
  // system 메시지는 보존 (있다면 첫 번째). user/assistant pair 단위로 제거.
  const start = state.messages[0]?.role === "system" ? 1 : 0;
  let removed = 0;
  while (tokens > budget && state.messages.length - start > 2) {
    state.messages.splice(start, 1);
    tokens = estimateMessageTokens(state.messages);
    removed++;
    if (removed > 100) break;
  }
  return removed > 0;
}

// ─── sliding_window — 최근 N 메시지 ──────────────────────────────

function applySlidingWindow(state: PipelineState, window: number): boolean {
  const start = state.messages[0]?.role === "system" ? 1 : 0;
  const total = state.messages.length - start;
  if (total <= window) return false;
  state.messages.splice(start, total - window);
  return true;
}

// ─── microcompact — 오래된 messages 를 LLM 으로 1줄 요약 ───────────

async function applyMicrocompact(state: PipelineState, budget: number): Promise<boolean> {
  const tokens = estimateMessageTokens(state.messages);
  if (tokens <= budget) return false;
  if (tokens < MICROCOMPACT_THRESHOLD) {
    return applyTokenBudget(state, budget);
  }
  // budget 의 절반까지 줄이기 위해 가장 오래된 절반 messages 합 → 요약 LLM 호출
  const start = state.messages[0]?.role === "system" ? 1 : 0;
  const half = Math.floor((state.messages.length - start) / 2);
  if (half < 2) return applyTokenBudget(state, budget);
  const oldChunk = state.messages.slice(start, start + half);
  const oldText = oldChunk
    .map((m) =>
      `[${m.role}] ${typeof m.content === "string" ? m.content : JSON.stringify(m.content).slice(0, 500)}`,
    )
    .join("\n");

  let summary = oldText.slice(0, 1000) + " ...(truncated)";
  // LLM 요약 시도 — provider 등록되어 있으면.
  try {
    const { createProvider } = await import("../providers/base");
    const config = state.config;
    const provider = createProvider({
      provider: config.provider || "anthropic",
      apiKey: process.env[`${(config.provider || "anthropic").toUpperCase()}_API_KEY`] || "",
      model: config.model || "claude-haiku-4-5-20251001",
    });
    let acc = "";
    const stream = provider.chat({
      messages: [
        {
          role: "user",
          content:
            "다음 대화 이력을 한국어 한 문단으로 핵심만 요약해. 사실/숫자/이름은 보존:\n\n" +
            oldText.slice(0, 8000),
        },
      ],
      max_tokens: 500,
      temperature: 0.3,
      stream: true,
    });
    for await (const ev of stream) {
      if (ev.type === "text_delta" && ev.text) acc += ev.text;
      if (ev.type === "stop") break;
    }
    if (acc.trim()) summary = acc.trim();
  } catch {
    // LLM 실패 — 단순 truncate.
  }

  // 오래된 청크 제거 + summary 1개 메시지 (system 다음에)
  state.messages.splice(start, half, {
    role: "system",
    content: `[Compacted history] ${summary}`,
  });
  // 그래도 budget 초과면 token_budget 추가 적용
  if (estimateMessageTokens(state.messages) > budget) {
    applyTokenBudget(state, budget);
  }
  return true;
}

// ─── context_collapse_overlay — pair-wise 압축 ──────────────────────

function applyCollapseOverlay(state: PipelineState, budget: number): boolean {
  // user-assistant pair 의 assistant 응답을 "[summary: ...]" 로 단순 축약
  let changed = false;
  let tokens = estimateMessageTokens(state.messages);
  for (let i = 0; i < state.messages.length; i++) {
    if (tokens <= budget) break;
    const m = state.messages[i];
    if (m.role !== "assistant") continue;
    if (typeof m.content === "string" && m.content.length > 500) {
      m.content = `[collapsed assistant: ${m.content.slice(0, 200)}…]`;
      changed = true;
      tokens = estimateMessageTokens(state.messages);
    }
  }
  return changed;
}

// ─── autocompact_llm — LLM 이 어떤 부분 압축할지 결정 ─────────────

async function applyAutocompactLlm(state: PipelineState, budget: number): Promise<boolean> {
  // 1차 — autocompact_llm 은 microcompact 로 폴백 (LLM 통신 비용은 같음).
  return applyMicrocompact(state, budget);
}
