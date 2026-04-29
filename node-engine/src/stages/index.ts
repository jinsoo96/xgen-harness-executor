/**
 * 13 stage registry. Python `xgen_harness.stages` 와 동일 stage_id / order.
 */

import { S01Input } from "./s01_input";
import { S02History } from "./s02_history";
import { S03Prompt } from "./s03_prompt";
import { S04Tool } from "./s04_tool";
import { S05Policy } from "./s05_policy";
import { S05Strategy } from "./s05_strategy";
import { S06Context } from "./s06_context";
import { S07Act } from "./s07_act";
import { S08Judge } from "./s08_judge";
import { S09Decide } from "./s09_decide";
import { S10Save } from "./s10_save";
import { S11Finalize } from "./s11_finalize";
import type { Stage } from "../pipeline/stage";

/**
 * order 순. s00_harness 는 본문 LLM 호출 controller 라 별도 — pipeline 에서
 * loop phase 에 직접 호출.
 */
export function buildStageList(): Stage[] {
  return [
    new S01Input(),
    new S02History(),
    new S03Prompt(),
    new S04Tool(),
    new S05Policy(),
    new S05Strategy(),
    new S06Context(),
    new S07Act(),
    new S08Judge(),
    new S09Decide(),
    new S10Save(),
    new S11Finalize(),
  ];
}

export const REQUIRED_STAGES = new Set(["s09_decide", "s01_input", "s11_finalize"]);
