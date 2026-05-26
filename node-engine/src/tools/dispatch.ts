/**
 * Tool dispatch — frozen tool 의 call_kind 별 실행.
 *
 * call_kind 매트릭스:
 *   - http        : fetch(url, ...) — Tavily/Brave/Naver 등 직접 외부 API
 *   - mcp_session : station_url + session_id 로 stdio MCP proxy 호출
 *   - rag         : v0.31+ — call_spec.embedder + QDRANT_URL env 둘 다 있으면
 *                   Qdrant + 임베더 API 직접 호출 (cluster 0 의존). 둘 중 하나라도
 *                   없으면 spec.metadata.rag_endpoint (cluster shim) 폴백.
 *   - noop        : 미구현 — content="(noop)"
 *
 * publish 시 spec freeze — Python NodeClass / langchain Tool 의존성 0.
 */

import type { FrozenToolDefinition } from "../spec/schema";
import type { PipelineState } from "../types";
import {
  dispatchBuiltinSearchTools,
  dispatchBuiltinDiscoverTools,
} from "./builtins";

export interface ToolDispatchResult {
  content: string;
  is_error: boolean;
}

// 중첩 subpipeline 재귀 깊이 가드 — 워크플로우 A 가 B 를, B 가 다시 A 를 부르는
// 순환/폭주 방지. 단일 process 의 도구 호출은 보통 순차라 모듈 카운터로 충분.
// (Python FrozenToolSource._SUBPIPELINE_DEPTH ContextVar 와 동등 의도.)
let _subpipelineDepth = 0;
const MAX_SUBPIPELINE_DEPTH = 4;

/**
 * 도구 호출에 필요한 env 미설정 안내 — 외부 실행자가 무엇을 wire 해야 하는지 명시.
 * python FrozenToolSource._env_missing_msg 와 동일 문구 (패리티).
 */
function envMissingMsg(toolName: string, missing: string[], how = ""): string {
  const names = missing.join(", ");
  let msg = `도구 '${toolName}' 실행에 필요한 환경변수가 설정되지 않았습니다: ${names}.`;
  if (how) msg += ` ${how}`;
  msg +=
    " 외부 실행 환경(또는 MCP 클라이언트 설정의 env 항목)에 이 값을 지정한 뒤 다시 시도하세요.";
  return msg;
}

export async function dispatchToolCall(
  def: FrozenToolDefinition,
  args: Record<string, unknown>,
  state: PipelineState,
): Promise<ToolDispatchResult> {
  switch (def.call_kind) {
    case "http":
      return dispatchHttp(def, args);
    case "mcp_session":
      return dispatchMcpSession(def, args, state);
    case "rag":
      return dispatchRag(def, args, state);
    case "subpipeline":
      return dispatchSubpipeline(def, args);
    case "canvas":
      return dispatchCanvas(def, args, state);
    case "builtin:search_tools":
      return dispatchBuiltinSearchTools(args, state.tool_definitions || []);
    case "builtin:discover_tools":
      return dispatchBuiltinDiscoverTools(args, state.tool_definitions || []);
    case "noop":
    default:
      return { content: "(noop)", is_error: false };
  }
}

/**
 * 중첩 워크플로우 실행 — call_spec.{config, tool_definitions, metadata} 로 nested
 * Pipeline 을 in-process 실행 (cluster 0 / http 콜백 0 / stdio 0, env-only).
 * "워크플로우를 도구로 마는" 경우. Python FrozenToolSource._dispatch_subpipeline 패리티.
 */
async function dispatchSubpipeline(
  def: FrozenToolDefinition,
  args: Record<string, unknown>,
): Promise<ToolDispatchResult> {
  const spec = (def.call_spec || {}) as Record<string, unknown>;
  const config = spec.config as Record<string, unknown> | undefined;
  if (!config || typeof config !== "object") {
    return { content: `subpipeline '${def.name}': call_spec.config 누락`, is_error: true };
  }
  const toolDefs = Array.isArray(spec.tool_definitions) ? spec.tool_definitions : [];
  const meta = (spec.metadata as Record<string, unknown>) || {};

  // harness-agents 규약은 {"input": "..."} — query/user_input 도 관용 허용.
  const userInput = String(
    (args && (args.input ?? args.query ?? args.user_input)) ?? "",
  );

  if (_subpipelineDepth >= MAX_SUBPIPELINE_DEPTH) {
    return {
      content: `subpipeline 최대 재귀 깊이(${MAX_SUBPIPELINE_DEPTH}) 초과 — '${def.name}' 중단`,
      is_error: true,
    };
  }

  // 순환 import 회피 — pipeline 은 stages→s07→dispatch 를 거쳐 이 모듈을 import 한다.
  const { runPipeline } = await import("../pipeline/pipeline");

  const subSpec = {
    spec_version: "1.0",
    harness_version: "",
    gallery_name: String(def.name || "subpipeline"),
    gallery_version: "0.1.0",
    compiled_at: "",
    config,
    tool_definitions: toolDefs,
    external_inputs: {},
    metadata: meta,
  } as unknown as import("../spec/schema").HarnessSpec;

  _subpipelineDepth++;
  try {
    const result = await runPipeline(subSpec, userInput, { collectEvents: false });
    return { content: result.output || "", is_error: false };
  } catch (e) {
    return { content: `${def.name} subpipeline 실행 오류: ${(e as Error).message}`, is_error: true };
  } finally {
    _subpipelineDepth--;
  }
}

/**
 * 캔버스 그래프 인터프리터 — call_spec.graph(agentflow nodes/edges)를 다중포트 DAG 로
 * in-process 실행 (env-only). Python FrozenToolSource._run_canvas_graph 패리티.
 * 실행노드 kind: call(dispatchToolCall 재사용) / transform / foreach / router / passthrough.
 */
function cstringify(v: unknown): string {
  if (typeof v === "string") return v;
  if (v === null || v === undefined) return "";
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

function canvasOutPorts(node: Record<string, unknown>): string[] {
  const op = node.out_ports;
  if (Array.isArray(op) && op.length) return op.map(String);
  return ["result", "output", "*"];
}

function collectCanvasInput(
  nid: string,
  incoming: Record<string, Array<[string, string, string]>>,
  outputs: Record<string, Record<string, unknown>>,
  userInput: string,
): unknown {
  const inc = incoming[nid] || [];
  if (!inc.length) return userInput;
  const vals: Record<string, unknown> = {};
  for (const [dstPort, srcNid, srcPort] of inc) {
    const so = outputs[srcNid] || {};
    const v = srcPort in so ? so[srcPort] : so["*"];
    if (v !== undefined && v !== null) vals[dstPort] = v;
  }
  const keys = Object.keys(vals);
  if (!keys.length) return userInput;
  if (keys.length === 1) return vals[keys[0]];
  return vals;
}

function canvasTransform(cfg: Record<string, unknown>, inval: unknown): unknown {
  const op = String(cfg.op || "passthrough");
  if (op === "template") {
    const t = String(cfg.template || "");
    if (!t) return inval;
    const ctx: Record<string, unknown> =
      inval && typeof inval === "object" && !Array.isArray(inval)
        ? (inval as Record<string, unknown>)
        : { input: cstringify(inval) };
    let out = t;
    for (const [k, v] of Object.entries(ctx)) out = out.split("{{" + k + "}}").join(cstringify(v));
    return out;
  }
  // jmespath 등은 node 기본 의존성 없음 — passthrough (정직).
  return inval;
}

async function canvasForeach(
  cfg: Record<string, unknown>,
  inval: unknown,
  state: PipelineState,
): Promise<unknown> {
  const body = cfg.body as FrozenToolDefinition | undefined;
  let items: unknown = inval;
  if (inval && typeof inval === "object" && !Array.isArray(inval)) {
    const obj = inval as Record<string, unknown>;
    items = obj[String(cfg.items_port || "items")] ?? obj.items ?? [];
  }
  if (typeof items === "string") {
    try {
      items = JSON.parse(items);
    } catch {
      items = [items];
    }
  }
  if (!Array.isArray(items)) items = [items];
  if (!body || !body.name) return items;
  const results: string[] = [];
  for (const it of items as unknown[]) {
    const a =
      it && typeof it === "object"
        ? { ...(it as Record<string, unknown>), input: cstringify(it) }
        : { input: cstringify(it), query: cstringify(it) };
    const r = await dispatchToolCall(body, a, state);
    results.push(r?.content ?? "");
  }
  return results;
}

async function runCanvasNode(
  node: Record<string, unknown>,
  inval: unknown,
  state: PipelineState,
): Promise<unknown> {
  const kind = String(node.kind || "passthrough");
  const cfg = (node.config as Record<string, unknown>) || {};
  if (kind === "passthrough" || kind === "input" || kind === "output") return inval;
  if (kind === "call") {
    const tool = cfg.tool as FrozenToolDefinition | undefined;
    if (!tool || !tool.name) return cstringify(inval);
    let a: Record<string, unknown>;
    if (inval && typeof inval === "object" && !Array.isArray(inval)) {
      a = { ...(inval as Record<string, unknown>) };
      if (!("input" in a)) a.input = cstringify(inval);
    } else {
      a = { input: cstringify(inval), query: cstringify(inval) };
    }
    const res = await dispatchToolCall(tool, a, state);
    return res?.content ?? "";
  }
  if (kind === "transform") return canvasTransform(cfg, inval);
  if (kind === "foreach") return canvasForeach(cfg, inval, state);
  if (kind === "router") return inval; // v1: 입력 통과(ok 경로)
  return inval; // unsupported passthrough
}

async function runCanvasGraph(
  graph: Record<string, unknown>,
  _metadata: Record<string, unknown>,
  userInput: string,
  state: PipelineState,
): Promise<string> {
  const nodes: Record<string, Record<string, unknown>> = {};
  for (const n of (graph.nodes as Array<Record<string, unknown>>) || []) {
    if (n.id) nodes[String(n.id)] = n;
  }
  const edges = (graph.edges as Array<Record<string, unknown>>) || [];
  const incoming: Record<string, Array<[string, string, string]>> = {};
  const upstream: Record<string, Set<string>> = {};
  const hasOut = new Set<string>();
  for (const e of edges) {
    const s = (e.source as Record<string, unknown>) || {};
    const t = (e.target as Record<string, unknown>) || {};
    const sn = String(s.nodeId || ""), sp = String(s.portId || "");
    const tn = String(t.nodeId || ""), tp = String(t.portId || "");
    if (!sn || !tn || !(sn in nodes) || !(tn in nodes)) continue;
    (incoming[tn] = incoming[tn] || []).push([tp, sn, sp]);
    (upstream[tn] = upstream[tn] || new Set()).add(sn);
    hasOut.add(sn);
  }
  const outputs: Record<string, Record<string, unknown>> = {};
  const done = new Set<string>();
  let lastValue: unknown = userInput;
  const total = Object.keys(nodes).length;
  let guard = 0;
  const limit = total * 4 + 10;
  while (done.size < total && guard < limit) {
    guard++;
    let progressed = false;
    for (const [nid, node] of Object.entries(nodes)) {
      if (done.has(nid)) continue;
      const ups = upstream[nid] || new Set<string>();
      let ready = true;
      for (const u of ups) if (!done.has(u)) { ready = false; break; }
      if (!ready) continue;
      const inval = collectCanvasInput(nid, incoming, outputs, userInput);
      let val: unknown;
      try {
        val = await runCanvasNode(node, inval, state);
      } catch {
        val = inval;
      }
      const pm: Record<string, unknown> = { "*": val };
      for (const op of canvasOutPorts(node)) pm[op] = val;
      outputs[nid] = pm;
      lastValue = val;
      done.add(nid);
      progressed = true;
    }
    if (!progressed) break;
  }
  let sinkVal: unknown = null;
  for (const nid of Object.keys(nodes)) {
    if (nid in outputs && !hasOut.has(nid)) sinkVal = outputs[nid]["*"];
  }
  const out = sinkVal !== null ? sinkVal : lastValue;
  return typeof out === "string" ? out : cstringify(out);
}

async function dispatchCanvas(
  def: FrozenToolDefinition,
  args: Record<string, unknown>,
  state: PipelineState,
): Promise<ToolDispatchResult> {
  const spec = (def.call_spec || {}) as Record<string, unknown>;
  const graph = spec.graph as Record<string, unknown> | undefined;
  if (!graph || typeof graph !== "object" || !Array.isArray(graph.nodes) || !graph.nodes.length) {
    return { content: `canvas '${def.name}': call_spec.graph 누락/비어있음`, is_error: true };
  }
  const meta = (spec.metadata as Record<string, unknown>) || {};
  const userInput = String((args && (args.input ?? args.query ?? args.user_input)) ?? "");

  if (_subpipelineDepth >= MAX_SUBPIPELINE_DEPTH) {
    return {
      content: `canvas 최대 재귀 깊이(${MAX_SUBPIPELINE_DEPTH}) 초과 — '${def.name}' 중단`,
      is_error: true,
    };
  }
  _subpipelineDepth++;
  try {
    const out = await runCanvasGraph(graph, meta, userInput, state);
    return { content: out, is_error: false };
  } catch (e) {
    return { content: `${def.name} canvas 실행 오류: ${(e as Error).message}`, is_error: true };
  } finally {
    _subpipelineDepth--;
  }
}

/**
 * 외부 자족 dispatch — call_spec.url 이 직접 외부 API URL.
 *
 * call_spec 구조 (v0.29+):
 *   - url           : 외부 API endpoint (cluster bridge 가 아닌 진짜 외부 URL)
 *   - method        : "GET" | "POST" | "PUT" | "DELETE" | "PATCH"
 *   - headers       : 정적 헤더 (Content-Type 등)
 *   - secrets_keys  : ENV 키 list — process.env[key] 를 동명 헤더로 inject
 *   - secret_header_map: { header_name: env_key } — ENV 값을 명시 헤더 이름으로 inject.
 *     예: {"X-Naver-Client-Id": "XGEN_TOOL__MCP_NAVER_NEWS_MCP__NAVER_CLIENT_ID"}.
 *     secrets_keys 와 달리 ENV 이름과 헤더 이름이 다를 때 사용.
 *   - query_template: { param_key: "{{arg_name}}" or 고정값 } — GET / query string.
 *     ``"{{name}}"`` 패턴은 args[name] 으로 치환. 치환값 없으면 항목 생략.
 *   - body_template : { field: "{{arg_name}}" or 고정값 } — POST / body.
 *     동일 ``{{name}}`` 치환. 미사용 args 도 body 에 spread (BC).
 */
async function dispatchHttp(
  def: FrozenToolDefinition,
  args: Record<string, unknown>,
): Promise<ToolDispatchResult> {
  const spec = def.call_spec || {};
  const url = (spec.url as string) || "";
  const method = ((spec.method as string) || "POST").toUpperCase();
  const headers: Record<string, string> = { ...(spec.headers as Record<string, string> || {}) };

  // 시크릿 ENV → 헤더 inject (두 패턴).
  //   1) secrets_keys: ENV 이름 = 헤더 이름 동일 (단순 패턴)
  //   2) secret_header_map: { header_name: env_key } 명시 매핑 (Naver/Tavily 등 외부 API 의
  //      고유 헤더 이름 — X-Naver-Client-Id 등)
  // 미설정 시크릿 env 를 모아두었다가 호출 실패 시 안내 (어떤 env 를 wire 해야 하는지).
  const missingEnv: string[] = [];
  const secrets = (spec.secrets_keys as string[]) || [];
  for (const key of secrets) {
    const val = process.env[key];
    if (val) headers[key] = val;
    else missingEnv.push(key);
  }
  const secretHeaderMap = (spec.secret_header_map as Record<string, string>) || {};
  for (const [headerName, envKey] of Object.entries(secretHeaderMap)) {
    const val = process.env[envKey];
    if (val) headers[headerName] = val;
    else missingEnv.push(envKey);
  }

  // secret_body_map — body 의 ``__secret_<name>`` placeholder 를 ENV 값으로 치환.
  // Tavily 처럼 body 안 api_key 박는 API 패턴 지원. 헤더 인증과 별개.
  const secretBodyMap = (spec.secret_body_map as Record<string, string>) || {};
  const secretArgs: Record<string, unknown> = {};
  for (const [placeholder, envKey] of Object.entries(secretBodyMap)) {
    const val = process.env[envKey];
    if (val) secretArgs[placeholder] = val;
    else missingEnv.push(envKey);
  }

  // query / body 템플릿 치환. secretArgs 를 args 에 합쳐서 placeholder 매칭 가능.
  const queryTmpl = (spec.query_template as Record<string, unknown>) || {};
  const bodyTmpl = (spec.body_template as Record<string, unknown>) || {};
  const renderArgs = { ...args, ...secretArgs };
  const queryParams = renderTemplate(queryTmpl, renderArgs);
  const bodyMerged: Record<string, unknown> = {
    ...renderTemplate(bodyTmpl, renderArgs),
    ...args,
  };

  if (!url) return { content: "http call_spec.url 누락", is_error: true };

  // URL path substitution — `https://api.github.com/repos/{owner}/{repo}/issues` 의
  // `{owner}` / `{repo}` 같은 placeholder 를 args 값으로 치환.
  // `path_params` 명시되면 그 키들만 path 로 (나머지는 body / query). 명시 안 되면
  // URL 안 `{name}` 모두 args 에서 매칭 시도. path 로 소비된 키는 body/query 에서 제외.
  const pathParams = (spec.path_params as string[]) || [];
  const consumedAsPath: Set<string> = new Set();
  let resolvedUrl = url;
  const pathPlaceholderRe = /\{(\w+)\}/g;
  resolvedUrl = resolvedUrl.replace(pathPlaceholderRe, (match, name) => {
    const v = (renderArgs as Record<string, unknown>)[name];
    if (v === undefined || v === null) return match;
    consumedAsPath.add(name);
    return encodeURIComponent(String(v));
  });
  if (pathParams.length > 0) {
    for (const p of pathParams) consumedAsPath.add(p);
  }
  // path 로 소비된 키는 query/body 에서 제외.
  for (const k of consumedAsPath) {
    delete queryParams[k];
    delete bodyMerged[k];
  }

  // 최종 URL — GET / DELETE / 또는 query_template 명시되면 query string 추가.
  let finalUrl = resolvedUrl;
  const hasQuery = Object.keys(queryParams).length > 0;
  if (hasQuery || method === "GET" || method === "DELETE") {
    // GET / DELETE 시 args 도 query 로 (body 안 쓰는 method) — 단 query_template 명시면 그 것만.
    let qpSource: Record<string, unknown>;
    if (hasQuery) qpSource = queryParams;
    else if (method === "GET" || method === "DELETE") {
      // path 로 소비된 키 제외한 args
      qpSource = Object.fromEntries(
        Object.entries(args).filter(([k]) => !consumedAsPath.has(k))
      );
    } else {
      qpSource = {};
    }
    const qs = buildQueryString(qpSource);
    if (qs) finalUrl = resolvedUrl + (resolvedUrl.includes("?") ? "&" : "?") + qs;
  }

  try {
    const init: RequestInit = {
      method,
      headers: { "content-type": "application/json", ...headers },
    };
    if (method !== "GET" && method !== "DELETE") {
      init.body = JSON.stringify(bodyMerged);
    }
    const resp = await fetch(finalUrl, init);
    const text = await resp.text();
    if (!resp.ok) {
      const hint = missingEnv.length ? " — " + envMissingMsg(def.name, missingEnv) : "";
      return { content: `${resp.status} ${text.slice(0, 500)}${hint}`, is_error: true };
    }
    return { content: text.slice(0, 50_000), is_error: false };
  } catch (e) {
    return { content: (e as Error).message, is_error: true };
  }
}

/**
 * "{{name}}" 패턴 치환 — args 에 매칭되는 값 있으면 그 값, 없으면 항목 제거.
 * 고정값 (string/number/bool) 은 그대로. nested dict 도 재귀.
 */
function renderTemplate(
  tmpl: Record<string, unknown>,
  args: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(tmpl)) {
    if (typeof v === "string") {
      const m = v.match(/^\{\{(\w+)\}\}$/);
      if (m) {
        const argName = m[1];
        if (argName in args && args[argName] !== undefined && args[argName] !== null) {
          out[k] = args[argName];
        }
        // 미매치는 항목 생략 (optional argument 패턴)
        continue;
      }
      out[k] = v;
    } else if (Array.isArray(v)) {
      out[k] = v;
    } else if (v && typeof v === "object") {
      out[k] = renderTemplate(v as Record<string, unknown>, args);
    } else {
      out[k] = v;
    }
  }
  return out;
}

function buildQueryString(params: Record<string, unknown>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null) continue;
    if (Array.isArray(v)) {
      for (const item of v) {
        parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(item))}`);
      }
    } else {
      parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
    }
  }
  return parts.join("&");
}

async function dispatchMcpSession(
  def: FrozenToolDefinition,
  args: Record<string, unknown>,
  state: PipelineState,
): Promise<ToolDispatchResult> {
  const cspec = (def.call_spec || {}) as Record<string, unknown>;
  const sid = String(cspec.session_id || "");
  if (!sid) return { content: "session_id 누락", is_error: true };

  // ── 1) 외부 자족 분기 (v0.31+) — spec.call_spec.spawn 박혀있으면 직접 spawn ──
  // station_url 이 함께 있어도 spawn 메타 우선 (외부 환경에서 cluster URL 도달
  // 불가일 수 있음). 박힌 경우 한정 — 박혀있지 않으면 곧장 shim 분기로.
  const spawnMeta = cspec.spawn as Record<string, unknown> | undefined;
  if (spawnMeta && typeof spawnMeta === "object" && spawnMeta.server_command) {
    // env_keys 선검사 — 미설정이면 어떤 env 를 wire 해야 하는지 즉시 안내 (spawn 시도 전).
    const spawnEnvKeys = Array.isArray(spawnMeta.env_keys)
      ? (spawnMeta.env_keys as unknown[]).map(String)
      : [];
    const missingSpawnEnv = spawnEnvKeys.filter((k) => !process.env[k]);
    if (missingSpawnEnv.length > 0) {
      return {
        content: envMissingMsg(
          def.name,
          missingSpawnEnv,
          `MCP 서버 '${String(spawnMeta.server_command)}' 구동에 필요한 값입니다.`,
        ),
        is_error: true,
      };
    }
    try {
      return await dispatchMcpViaSpawn(def, args, sid, spawnMeta);
    } catch (e) {
      const directErr = (e as Error).message;
      // station_url 있으면 graceful fallback 시도
      const stationUrl =
        (state.metadata.station_url as string) || process.env.MCP_STATION_BASE_URL || "";
      if (stationUrl) {
        const fb = await dispatchMcpViaStation(def, args, sid, stationUrl);
        if (!fb.is_error) return fb;
        return {
          content: `mcp spawn failed (${directErr}); station fallback failed (${fb.content})`,
          is_error: true,
        };
      }
      return { content: `mcp spawn failed: ${directErr}`, is_error: true };
    }
  }

  // ── 2) Station proxy fallback (v0.29 기존 동작) ────────────────────
  const stationUrl =
    (state.metadata.station_url as string) || process.env.MCP_STATION_BASE_URL || "";
  if (!stationUrl) {
    return {
      content: envMissingMsg(
        def.name,
        ["MCP_STATION_BASE_URL"],
        "MCP 도구는 (a) freeze 시 박힌 stdio spawn 메타(server_command) 또는 " +
          "(b) MCP Station 프록시 주소 MCP_STATION_BASE_URL 가 필요합니다.",
      ),
      is_error: true,
    };
  }
  return dispatchMcpViaStation(def, args, sid, stationUrl);
}

async function dispatchMcpViaStation(
  def: FrozenToolDefinition,
  args: Record<string, unknown>,
  sid: string,
  stationUrl: string,
): Promise<ToolDispatchResult> {
  const url = `${stationUrl}/api/mcp/mcp-request`;
  const payload = {
    session_id: sid,
    method: "tools/call",
    params: { name: def.name, arguments: args },
  };
  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    const text = await resp.text();
    if (!resp.ok) {
      return { content: `station ${resp.status}: ${text.slice(0, 500)}`, is_error: true };
    }
    try {
      const data = JSON.parse(text);
      const result = data.data || data.result || {};
      const content = result.content;
      if (Array.isArray(content)) {
        const parts: string[] = [];
        for (const block of content) {
          if (block && typeof block === "object" && (block as any).type === "text") {
            parts.push((block as any).text || "");
          } else if (typeof block === "string") {
            parts.push(block);
          }
        }
        return { content: parts.join("\n") || JSON.stringify(result), is_error: false };
      }
      return { content: JSON.stringify(result).slice(0, 50_000), is_error: false };
    } catch {
      return { content: text.slice(0, 50_000), is_error: false };
    }
  } catch (e) {
    return { content: (e as Error).message, is_error: true };
  }
}

// ─── 외부 자족 MCP — stdio spawn 세션 핸들 (process 내 캐시) ──────────
//
// 같은 spec 의 여러 도구가 같은 session_id 면 한 process 공유 — 매 도구 호출마다
// spawn 비용 회피. process exit 시 자동 정리 (Node가 cleanup).

interface SpawnedMcpClient {
  client: any; // @modelcontextprotocol/sdk Client
  transport: any; // StdioClientTransport
  initializing: Promise<void> | null;
}

const _spawnedClients = new Map<string, SpawnedMcpClient>();

async function getOrSpawnMcpClient(
  sid: string,
  spawnMeta: Record<string, unknown>,
): Promise<any> {
  let entry = _spawnedClients.get(sid);
  if (entry) {
    if (entry.initializing) await entry.initializing;
    return entry.client;
  }

  // dynamic import — @modelcontextprotocol/sdk 의존성은 이미 npm 에 박혀있음
  const { Client } = await import("@modelcontextprotocol/sdk/client/index.js");
  const { StdioClientTransport } = await import(
    "@modelcontextprotocol/sdk/client/stdio.js"
  );

  const command = String(spawnMeta.server_command || "");
  if (!command) throw new Error("spawn.server_command 미박힘");
  const argsList = Array.isArray(spawnMeta.server_args)
    ? (spawnMeta.server_args as unknown[]).map(String)
    : [];

  // env_keys 가 박힘 → process.env 에서 해당 키만 추출해 child 에 전달.
  // env 값은 spec 에 박지 않음 (secret leak 방지) — 외부 환경 변수에서만.
  const env: Record<string, string> = {};
  // PATH 류 기본 환경은 보존해야 spawn 성공 (npx/uvx 등 PATH 의존)
  for (const k of Object.keys(process.env)) {
    const v = process.env[k];
    if (typeof v === "string") env[k] = v;
  }
  const envKeys = Array.isArray(spawnMeta.env_keys)
    ? (spawnMeta.env_keys as unknown[]).map(String)
    : [];
  // env_keys 가 process.env 에 박혀있지 않으면 경고 — child 에 빈 값 전달 X
  for (const k of envKeys) {
    if (!(k in env) || env[k] === "") {
      throw new Error(`mcp spawn: required env "${k}" 미설정`);
    }
  }

  const cwd = spawnMeta.working_dir ? String(spawnMeta.working_dir) : undefined;

  const transport = new StdioClientTransport({
    command,
    args: argsList,
    env,
    ...(cwd ? { cwd } : {}),
  });

  const client = new Client(
    { name: "xgen-harness-engine-node", version: "0.31.0" },
    { capabilities: {} },
  );

  // initializing 락 — 동시 호출 시 한 번만 connect
  const initPromise = (async () => {
    await client.connect(transport);
  })();
  entry = { client, transport, initializing: initPromise };
  _spawnedClients.set(sid, entry);
  try {
    await initPromise;
  } catch (e) {
    _spawnedClients.delete(sid);
    throw e;
  }
  entry.initializing = null;
  return client;
}

async function dispatchMcpViaSpawn(
  def: FrozenToolDefinition,
  args: Record<string, unknown>,
  sid: string,
  spawnMeta: Record<string, unknown>,
): Promise<ToolDispatchResult> {
  const client = await getOrSpawnMcpClient(sid, spawnMeta);
  // MCP tool name 은 spec freeze 시점 그대로 외부 서버의 도구 이름과 일치한다고 가정.
  // (cluster MCPStationToolSource.list_tools 가 station 응답을 그대로 노출하므로 정합)
  const result: any = await client.callTool({
    name: def.name,
    arguments: args,
  });
  const content = result?.content;
  if (Array.isArray(content)) {
    const parts: string[] = [];
    for (const block of content) {
      if (block && typeof block === "object" && (block as any).type === "text") {
        parts.push((block as any).text || "");
      } else if (typeof block === "string") {
        parts.push(block);
      }
    }
    return {
      content: parts.join("\n") || JSON.stringify(result),
      is_error: Boolean(result?.isError),
    };
  }
  return {
    content: JSON.stringify(result).slice(0, 50_000),
    is_error: Boolean(result?.isError),
  };
}

async function dispatchRag(
  def: FrozenToolDefinition,
  args: Record<string, unknown>,
  state: PipelineState,
): Promise<ToolDispatchResult> {
  const spec = (def.call_spec || {}) as Record<string, unknown>;
  const collectionName = String(spec.collection_name || "");
  const query = String(args.query ?? "");
  const topK = Number(spec.top_k ?? 4);
  const scoreThreshold = Number(spec.score_threshold ?? 0.0);

  // ── 1) 외부 자족 분기 (v0.31+) — Qdrant + embedder 직접 ──────────────
  // 사용자 환경에 QDRANT_URL 박혀있고 freeze 시 embedder 메타가 박혔으면
  // cluster 0 의존으로 직접 검색. 어느 한쪽이라도 빠지면 shim 폴백.
  const qdrantUrlEnv = String(spec.qdrant_url_env || "QDRANT_URL");
  const qdrantUrl = (process.env[qdrantUrlEnv] || "").trim();
  const embedderMeta = spec.embedder as Record<string, unknown> | undefined;
  if (qdrantUrl && embedderMeta && typeof embedderMeta === "object") {
    try {
      const vector = await embedQuery(query, embedderMeta);
      const qdrantApiKeyEnv = String(spec.qdrant_api_key_env || "QDRANT_API_KEY");
      const qdrantApiKey = (process.env[qdrantApiKeyEnv] || "").trim();
      const result = await qdrantSearch(
        qdrantUrl,
        qdrantApiKey,
        collectionName,
        vector,
        topK,
        scoreThreshold,
      );
      return { content: JSON.stringify(result).slice(0, 50_000), is_error: false };
    } catch (e) {
      // 직접 호출 실패 시 cluster shim 으로 폴백 시도 — 외부 환경의 일시적
      // 임베더 키 누락 / Qdrant 연결 오류 시 우아한 degradation.
      // 명시 디버깅을 위해 에러 메시지에 직접 분기 실패 사실 박음.
      const directErr = (e as Error).message;
      const fallback = await dispatchRagViaShim(def.name, spec, query, state);
      if (!fallback.is_error) return fallback;
      return {
        content: `rag direct failed (${directErr}); shim fallback failed (${fallback.content})`,
        is_error: true,
      };
    }
  }

  // ── 2) Cluster shim 폴백 (v0.29 기존 동작) ───────────────────────────
  return dispatchRagViaShim(def.name, spec, query, state);
}

async function dispatchRagViaShim(
  toolName: string,
  spec: Record<string, unknown>,
  query: string,
  state: PipelineState,
): Promise<ToolDispatchResult> {
  const endpoint =
    (state.metadata.rag_endpoint as string) ||
    process.env.HARNESS_RAG_ENDPOINT ||
    "";
  if (!endpoint) {
    const qdrantUrlEnv = String(spec.qdrant_url_env || "QDRANT_URL");
    return {
      content: envMissingMsg(
        toolName,
        [qdrantUrlEnv, "HARNESS_RAG_ENDPOINT"],
        `RAG 검색은 (a) 외부 Qdrant 직접 호출용 ${qdrantUrlEnv}(+임베더 키) 또는 ` +
          "(b) RAG 검색 endpoint HARNESS_RAG_ENDPOINT 중 하나가 필요합니다.",
      ),
      is_error: true,
    };
  }
  const body = {
    collection_name: spec.collection_name,
    query,
    top_k: spec.top_k ?? 4,
    score_threshold: spec.score_threshold ?? 0.0,
  };
  try {
    const resp = await fetch(endpoint, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const text = await resp.text();
    if (!resp.ok) {
      return { content: `rag ${resp.status}: ${text.slice(0, 500)}`, is_error: true };
    }
    return { content: text.slice(0, 50_000), is_error: false };
  } catch (e) {
    return { content: (e as Error).message, is_error: true };
  }
}

// ─── Embedder providers — generic helpers ────────────────────────────
//
// 새 provider 추가는 이 객체에 1 줄 — 엔진 외부에서 register 도 가능.
// 시그니처: (text, meta) → number[]. meta = freeze 시점 박힌 embedder dict.

type EmbedFn = (
  text: string,
  meta: Record<string, unknown>,
) => Promise<number[]>;

const _embedderProviders: Record<string, EmbedFn> = {
  openai: embedOpenAI,
  custom_http: embedCustomHttp,
  voyage: embedVoyage,
};

/** 외부 entry 가 신규 provider 등록 가능 — 엔진 무재배포 확장. */
export function registerEmbedderProvider(name: string, fn: EmbedFn): void {
  _embedderProviders[name] = fn;
}

async function embedQuery(
  text: string,
  meta: Record<string, unknown>,
): Promise<number[]> {
  const provider = String(meta.provider || "").toLowerCase();
  if (!provider) {
    throw new Error("embedder.provider 미박힘 — freeze 시점 메타 누락");
  }
  const fn = _embedderProviders[provider];
  if (!fn) {
    throw new Error(`embedder.provider="${provider}" 미지원 (registered: ${Object.keys(_embedderProviders).join(",")})`);
  }
  const vec = await fn(text, meta);
  const expected = Number(meta.dimension || 0);
  if (expected && vec.length !== expected) {
    throw new Error(`embedder dimension mismatch — expected ${expected}, got ${vec.length}`);
  }
  return vec;
}

async function embedOpenAI(
  text: string,
  meta: Record<string, unknown>,
): Promise<number[]> {
  const model = String(meta.model || "text-embedding-3-small");
  const keyEnv = String(meta.api_key_env || "OPENAI_API_KEY");
  const apiKey = (process.env[keyEnv] || "").trim();
  if (!apiKey) throw new Error(`OpenAI embedder: ${keyEnv} 미설정`);
  const endpoint = String(meta.endpoint || "https://api.openai.com/v1/embeddings");
  const resp = await fetch(endpoint, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({ model, input: text }),
  });
  if (!resp.ok) {
    const t = await resp.text();
    throw new Error(`openai embed ${resp.status}: ${t.slice(0, 300)}`);
  }
  const data = (await resp.json()) as { data?: Array<{ embedding?: number[] }> };
  const vec = data?.data?.[0]?.embedding;
  if (!Array.isArray(vec)) throw new Error("openai embed: 응답에 embedding 없음");
  return vec;
}

async function embedCustomHttp(
  text: string,
  meta: Record<string, unknown>,
): Promise<number[]> {
  const endpoint = String(meta.endpoint || "");
  if (!endpoint) throw new Error("custom_http embedder: endpoint 미박힘");
  const model = String(meta.model || "");
  const keyEnv = String(meta.api_key_env || "CUSTOM_EMBEDDING_API_KEY");
  const apiKey = (process.env[keyEnv] || "").trim();
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (apiKey) headers.authorization = `Bearer ${apiKey}`;
  const resp = await fetch(endpoint, {
    method: "POST",
    headers,
    body: JSON.stringify({ model, input: text }),
  });
  if (!resp.ok) {
    const t = await resp.text();
    throw new Error(`custom_http embed ${resp.status}: ${t.slice(0, 300)}`);
  }
  // OpenAI 호환 응답 우선, 그 외 단일 list 형태도 수용.
  const data = (await resp.json()) as
    | { data?: Array<{ embedding?: number[] }> }
    | { embedding?: number[] }
    | number[];
  if (Array.isArray(data)) return data as number[];
  const openaiLike = (data as { data?: Array<{ embedding?: number[] }> })?.data?.[0]?.embedding;
  if (Array.isArray(openaiLike)) return openaiLike;
  const bare = (data as { embedding?: number[] })?.embedding;
  if (Array.isArray(bare)) return bare;
  throw new Error("custom_http embed: 알 수 없는 응답 형태");
}

async function embedVoyage(
  text: string,
  meta: Record<string, unknown>,
): Promise<number[]> {
  const model = String(meta.model || "voyage-3");
  const keyEnv = String(meta.api_key_env || "VOYAGE_API_KEY");
  const apiKey = (process.env[keyEnv] || "").trim();
  if (!apiKey) throw new Error(`Voyage embedder: ${keyEnv} 미설정`);
  const endpoint = String(meta.endpoint || "https://api.voyageai.com/v1/embeddings");
  const resp = await fetch(endpoint, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({ model, input: text, input_type: "query" }),
  });
  if (!resp.ok) {
    const t = await resp.text();
    throw new Error(`voyage embed ${resp.status}: ${t.slice(0, 300)}`);
  }
  const data = (await resp.json()) as { data?: Array<{ embedding?: number[] }> };
  const vec = data?.data?.[0]?.embedding;
  if (!Array.isArray(vec)) throw new Error("voyage embed: 응답에 embedding 없음");
  return vec;
}

async function qdrantSearch(
  qdrantUrl: string,
  qdrantApiKey: string,
  collectionName: string,
  vector: number[],
  topK: number,
  scoreThreshold: number,
): Promise<Array<Record<string, unknown>>> {
  const url = `${qdrantUrl.replace(/\/$/, "")}/collections/${encodeURIComponent(
    collectionName,
  )}/points/search`;
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (qdrantApiKey) headers["api-key"] = qdrantApiKey;
  const body: Record<string, unknown> = {
    vector,
    limit: topK,
    with_payload: true,
  };
  if (scoreThreshold > 0) body.score_threshold = scoreThreshold;
  const resp = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const t = await resp.text();
    throw new Error(`qdrant search ${resp.status}: ${t.slice(0, 300)}`);
  }
  const data = (await resp.json()) as {
    result?: Array<{ id?: unknown; score?: number; payload?: Record<string, unknown> }>;
  };
  return (data.result || []).map((r) => ({
    id: r.id,
    score: r.score,
    payload: r.payload || {},
  }));
}
