"""
Unit test for AI Video Flow MCP Server JSON-RPC Protocol
Validates initialize, tools/list, and tool execution without external network.
"""

import subprocess
import json
import os
import sys

def run_test():
    server_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "packages", "mcp_server", "server.py"))
    python_bin = sys.executable

    print(f"Testing MCP Server with Python: {python_bin}")
    print(f"Server script: {server_path}")

    proc = subprocess.Popen(
        [python_bin, server_path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    # 1. Send initialize
    init_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "TestClient", "version": "1.0"}
        }
    }
    proc.stdin.write(json.dumps(init_req) + "\n")
    proc.stdin.flush()
    init_res = json.loads(proc.stdout.readline())
    print("Initialize Response:", json.dumps(init_res, indent=2))
    assert init_res.get("result", {}).get("serverInfo", {}).get("name") == "ai-video-flow-resolve"

    # 2. Send tools/list
    tools_req = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {}
    }
    proc.stdin.write(json.dumps(tools_req) + "\n")
    proc.stdin.flush()
    tools_res = json.loads(proc.stdout.readline())
    tools = tools_res.get("result", {}).get("tools", [])
    print(f"Registered Tools Count: {len(tools)}")
    tool_names = [t["name"] for t in tools]
    print("Tools:", tool_names)
    assert "resolve_get_status" in tool_names
    assert "ai_analyze_wedding_clips" in tool_names
    assert "ai_create_wedding_timelines" in tool_names

    # 3. Call resolve_get_status
    call_req = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "resolve_get_status",
            "arguments": {}
        }
    }
    proc.stdin.write(json.dumps(call_req) + "\n")
    proc.stdin.flush()
    call_res = json.loads(proc.stdout.readline())
    print("Tool Call Result (resolve_get_status):", json.dumps(call_res, indent=2))
    assert "result" in call_res

    # Cleanup
    proc.terminate()
    print("\n✅ All MCP Server unit tests PASSED successfully!")

if __name__ == "__main__":
    run_test()
