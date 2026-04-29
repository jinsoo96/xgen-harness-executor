# xgen-harness-engine-node

TypeScript engine for `xgen-harness` — reads `spec.json` (compiled from a
Python harness workflow) and runs the same 13-stage pipeline. **Fully
equivalent** — every stage setting from the original workflow is preserved.

## Install

```bash
npm install xgen-harness-engine-node
```

## Use as MCP server

Wrapper packages (e.g. `xgen-harness-my_agent`) bundle a `spec.json` plus a
1-line `bin/cli.js` that calls this engine:

```js
const { serveMcp } = require("xgen-harness-engine-node");
const spec = require("./spec.json");
serveMcp(spec);
```

The wrapper is the unit Claude Desktop / Cursor / mcp-station picks up:

```json
{
  "mcpServers": {
    "my-agent": {
      "command": "npx",
      "args": ["-y", "xgen-harness-my_agent"]
    }
  }
}
```

## Programmatic use

```ts
import { runOnce, loadSpec } from "xgen-harness-engine-node";

const spec = loadSpec("./spec.json");
const result = await runOnce(spec, "What is 2+2?");
console.log(result.output);
```

## What's inside

- 13 pipeline stages (s00_harness ... s11_finalize) ported 1:1 from the
  Python `xgen-harness` engine
- 4 LLM providers — Anthropic, OpenAI, vLLM (with Qwen native `<tool_call>`
  XML parser), Bedrock-ready
- 4 builtin Policy Guards — cost_cap / max_loop / pii_block / domain_allow
- Tool dispatch — http / mcp_session / rag (frozen at publish time, no
  Python NodeClass dependency)

## Spec source

A spec.json is produced by Python:

```python
from xgen_harness.compile import compile_workflow_to_npm
result = compile_workflow_to_npm(
    harness_config=config,
    gallery_name="my_agent",
    gallery_version="0.1.0",
    out_dir="./dist",
)
```

This produces a complete npm tarball (`xgen-harness-my_agent-0.1.0.tgz`)
with the wrapper + spec + cli.

## License

UNLICENSED (private use). Contact the maintainer for distribution rights.
