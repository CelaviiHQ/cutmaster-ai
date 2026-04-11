---
name: test-mcp
description: "Run end-to-end MCP server tests — start the server via stdio, send JSON-RPC messages, and verify tool responses."
---

# Test the MCP Server

Run this skill to verify the MCP server works end-to-end over stdio, not just as Python imports.

## Quick Test

```bash
uv run pytest tests/ -v
```

## Full MCP Protocol Test

Run this Python script to test the actual JSON-RPC protocol over stdio:

```python
import subprocess, json, time

proc = subprocess.Popen(
    ['.venv/bin/python3', '-m', 'celavii_resolve'],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
)

def send(method, params=None, msg_id=1):
    msg = {"jsonrpc": "2.0", "method": method, "id": msg_id}
    if params:
        msg["params"] = params
    body = json.dumps(msg).encode()
    frame = f"Content-Length: {len(body)}\r\n\r\n".encode() + body
    proc.stdin.write(frame)
    proc.stdin.flush()

def recv():
    header = b""
    while not header.endswith(b"\r\n\r\n"):
        ch = proc.stdout.read(1)
        if not ch:
            return None
        header += ch
    length = int(header.decode().split("Content-Length: ")[1].split("\r\n")[0])
    body = proc.stdout.read(length)
    return json.loads(body.decode())

# 1. Initialize handshake
send("initialize", {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {"name": "test", "version": "1.0"}
})
r = recv()
print(f"Server: {r['result']['serverInfo']}")

# 2. Send initialized notification
notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}).encode()
proc.stdin.write(f"Content-Length: {len(notif)}\r\n\r\n".encode() + notif)
proc.stdin.flush()
time.sleep(1)

# 3. List tools
send("tools/list", {}, 2)
r = recv()
tools = r["result"]["tools"]
print(f"Tools registered: {len(tools)}")

# 4. Call a tool
send("tools/call", {"name": "celavii_get_version", "arguments": {}}, 3)
r = recv()
print(f"celavii_get_version: {r['result']['content'][0]['text']}")

# 5. List resources
send("resources/list", {}, 4)
r = recv()
print(f"Resources: {len(r['result']['resources'])}")

proc.terminate()
print("ALL TESTS PASSED")
```

## What to verify

1. **Server starts** without import errors
2. **Initialize handshake** returns server info with name and version
3. **tools/list** returns 233+ tools
4. **tools/call** on `celavii_get_version` returns the Resolve version (requires Resolve running)
5. **resources/list** returns 5 resources

## Prerequisites

- DaVinci Resolve Studio must be running
- External scripting set to Local
- Virtual environment activated with dependencies installed
