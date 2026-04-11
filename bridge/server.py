"""
XGEN Node MCP Bridge
=====================
xgen-workflow의 Node.execute()를 MCP 도구로 노출.
Rust 하네스 실행기가 subprocess로 spawn → stdio JSON-RPC 2.0 통신.

사용법 (Rust에서 자동 호출):
    python bridge/server.py --nodes-dir /path/to/editor/nodes

기존 노드 코드 변경 0.
"""

import asyncio
import importlib
import json
import pkgutil
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Type


# =============================================================================
# MCP 서버 (JSON-RPC 2.0 over stdio)
# =============================================================================

class NodeMcpBridgeServer:
    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self):
        self.tools: Dict[str, "NodeToolWrapper"] = {}
        self.initialized = False

    def register(self, wrapper: "NodeToolWrapper"):
        self.tools[wrapper.tool_id] = wrapper

    async def handle_request(self, request: Dict) -> Optional[Dict]:
        method = request.get("method", "")
        params = request.get("params", {})
        req_id = request.get("id")

        # notification (id 없음)
        if req_id is None:
            if method == "notifications/initialized":
                self.initialized = True
            return None

        if method == "initialize":
            return self._rpc_ok(req_id, {
                "protocolVersion": self.PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "xgen-node-mcp-bridge", "version": "0.1.0"},
            })

        if method == "tools/list":
            return self._rpc_ok(req_id, {
                "tools": [w.to_mcp_def() for w in self.tools.values()]
            })

        if method == "tools/call":
            return await self._handle_call(req_id, params)

        return self._rpc_err(req_id, -32601, f"Unknown method: {method}")

    async def _handle_call(self, req_id, params):
        name = params.get("name", "")
        arguments = params.get("arguments", {})

        wrapper = self.tools.get(name)
        if not wrapper:
            return self._rpc_ok(req_id, {
                "content": [{"type": "text", "text": f"Tool not found: {name}"}],
                "isError": True,
            })

        try:
            text = await wrapper.call(arguments)
            return self._rpc_ok(req_id, {
                "content": [{"type": "text", "text": text}],
                "isError": False,
            })
        except Exception as e:
            return self._rpc_ok(req_id, {
                "content": [{"type": "text", "text": f"Error: {e}\n{traceback.format_exc()}"}],
                "isError": True,
            })

    @staticmethod
    def _rpc_ok(req_id, result):
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _rpc_err(req_id, code, message):
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


# =============================================================================
# 노드 래퍼
# =============================================================================

# 하네스가 직접 처리 → MCP 도구화 불필요
EXCLUDED_FUNCTIONS = {"agents", "startnode", "endnode", "router", "model_provider"}
EXCLUDED_NODES = {
    "tools/agent_planner", "tools/workflow_tool", "tools/hierarchy_tools",
    "tools/print_any", "tools/print_any_stream", "tools/print_format",
    "tools/print_agent_output", "input_string", "input_integer",
    "input_files", "input_template", "input_schema_provider", "output_schema_provider",
}

PARAM_TYPE_MAP = {
    "STR": {"type": "string"},
    "INT": {"type": "integer"},
    "FLOAT": {"type": "number"},
    "BOOL": {"type": "boolean"},
    "OBJECT": {"type": "object"},
    "LIST": {"type": "array"},
    "SELECT": {"type": "string"},
    "MULTI_SELECT": {"type": "array", "items": {"type": "string"}},
}


def param_to_json_schema(param: Dict) -> Dict:
    """xgen Parameter → JSON Schema property"""
    schema = PARAM_TYPE_MAP.get(param.get("type", "STR"), {"type": "string"}).copy()

    if param.get("min") is not None:
        schema["minimum"] = param["min"]
    if param.get("max") is not None:
        schema["maximum"] = param["max"]

    # SELECT enum
    if param.get("type") == "SELECT" and isinstance(param.get("options"), list):
        vals = []
        for o in param["options"]:
            vals.append(o.get("value", o.get("label", "")) if isinstance(o, dict) else str(o))
        if vals:
            schema["enum"] = vals

    if param.get("value") is not None:
        schema["default"] = param["value"]

    desc = param.get("description_ko") or param.get("description") or param.get("name", "")
    if desc:
        schema["description"] = desc

    return schema


def result_to_text(result: Any) -> str:
    """노드 실행 결과 → MCP 텍스트 응답"""
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, (int, float, bool)):
        return str(result)
    if isinstance(result, (dict, list)):
        return json.dumps(result, ensure_ascii=False, default=str, indent=2)
    # LangChain Tool 객체 → 메타 정보
    if hasattr(result, "name") and hasattr(result, "description"):
        return json.dumps({
            "tool_name": result.name,
            "tool_description": result.description,
            "type": "langchain_tool",
        }, ensure_ascii=False)
    # Generator → 소비
    if hasattr(result, "__next__"):
        parts = []
        for chunk in result:
            parts.append(str(chunk.get("content", chunk)) if isinstance(chunk, dict) else str(chunk))
        return "".join(parts)
    return str(result)


class NodeToolWrapper:
    """Node 클래스 하나 → MCP 도구 하나"""

    def __init__(self, node_cls: Type, tool_id: str):
        self.node_cls = node_cls
        self.tool_id = tool_id
        self.node_name = getattr(node_cls, "nodeName", node_cls.__name__)
        self.description = (
            getattr(node_cls, "description_ko", "")
            or getattr(node_cls, "description", "")
            or self.node_name
        )

        # parameters → JSON Schema
        params = getattr(node_cls, "parameters", [])
        properties = {}
        required = []
        for p in params:
            pid = p.get("id", "")
            if not pid:
                continue
            properties[pid] = param_to_json_schema(p)
            if p.get("required"):
                required.append(pid)

        self.input_schema = {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    def to_mcp_def(self) -> Dict:
        return {
            "name": self.tool_id,
            "description": self.description,
            "inputSchema": self.input_schema,
        }

    async def call(self, arguments: Dict) -> str:
        node = self.node_cls()
        result = node.execute(**arguments)
        if asyncio.iscoroutine(result):
            result = await result
        return result_to_text(result)


# =============================================================================
# 노드 디스커버리
# =============================================================================

def scan_nodes(nodes_dir: str, categories: Optional[List[str]] = None) -> List[NodeToolWrapper]:
    """editor/nodes/ 스캔 → MCP 도구화 가능한 노드만 래핑"""
    nodes_path = Path(nodes_dir)
    if not nodes_path.exists():
        print(f"Error: {nodes_dir} not found", file=sys.stderr)
        return []

    # import 경로 설정
    editor_dir = nodes_path.parent
    for p in [str(editor_dir), str(editor_dir.parent)]:
        if p not in sys.path:
            sys.path.insert(0, p)

    # Node ABC 로드 시도
    node_abc = None
    try:
        from node_composer import Node as NodeABC
        node_abc = NodeABC
    except ImportError:
        print("Warning: node_composer.Node not found, using duck typing", file=sys.stderr)

    discovered = []
    for _, modname, _ in pkgutil.walk_packages([str(nodes_path)], prefix=f"{nodes_path.name}."):
        try:
            module = importlib.import_module(modname)
        except Exception as e:
            print(f"  skip {modname}: {e}", file=sys.stderr)
            continue

        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if not isinstance(obj, type):
                continue

            # Node 서브클래스 확인
            is_node = False
            if node_abc and issubclass(obj, node_abc) and obj is not node_abc:
                is_node = True
            elif (hasattr(obj, "categoryId") and hasattr(obj, "nodeId")
                  and hasattr(obj, "execute") and obj.__name__ != "Node"):
                is_node = True

            if not is_node or getattr(obj, "disable", False):
                continue

            fid = getattr(obj, "functionId", "")
            nid = getattr(obj, "nodeId", "")

            # 하네스가 직접 처리하는 것 제외
            if fid in EXCLUDED_FUNCTIONS or nid in EXCLUDED_NODES:
                continue
            if categories and fid not in categories:
                continue

            # OpenAI는 도구 이름에 [a-zA-Z0-9_-]만 허용
            import re
            tool_id = f"node_{nid.replace('/', '_')}"
            tool_id = re.sub(r'[^a-zA-Z0-9_-]', '_', tool_id)
            wrapper = NodeToolWrapper(obj, tool_id)
            discovered.append(wrapper)
            print(f"  + {tool_id} ({wrapper.node_name})", file=sys.stderr)

    return discovered


# =============================================================================
# stdio 루프
# =============================================================================

async def run_stdio(server: NodeMcpBridgeServer):
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

    wt, wp = await asyncio.get_event_loop().connect_write_pipe(
        asyncio.streams.FlowControlMixin, sys.stdout
    )
    writer = asyncio.StreamWriter(wt, wp, reader, asyncio.get_event_loop())

    while True:
        line = await reader.readline()
        if not line:
            break
        try:
            req = json.loads(line.decode().strip())
        except json.JSONDecodeError:
            continue
        resp = await server.handle_request(req)
        if resp is not None:
            writer.write((json.dumps(resp, ensure_ascii=False) + "\n").encode())
            await writer.drain()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="XGEN Node MCP Bridge")
    parser.add_argument("--nodes-dir", required=True, help="Path to editor/nodes/")
    parser.add_argument("--categories", help="Comma-separated functionIds (e.g. document_loaders,mcp,api_loader)")
    args = parser.parse_args()

    server = NodeMcpBridgeServer()
    cats = args.categories.split(",") if args.categories else None
    wrappers = scan_nodes(args.nodes_dir, cats)
    for w in wrappers:
        server.register(w)

    print(f"xgen-node-mcp-bridge: {len(server.tools)} tools ready", file=sys.stderr)
    asyncio.run(run_stdio(server))


if __name__ == "__main__":
    main()
