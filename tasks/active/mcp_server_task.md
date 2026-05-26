# AWOS MCP Server Task

## Status: PENDING

## Spec
`docs/specs/mcp_server_spec.md` — contains the complete file content. Just copy it.

## Steps
- [x] 1. Run `pip install mcp` and add `mcp>=1.0.0` to `requirements.txt`
- [x] 2. Create `mcp_server.py` at project root using `FastMCP` high-level API
- [x] 3. Add `"awos"` server entry to `.vscode/mcp.json.template`
- [x] 4. Write `test_mcp_server.py` — 20 assertions
- [x] 5. Run `python3 test_mcp_server.py`, fix import issues
- [x] 6. Mark COMPLETE

## Test Results
- `test_mcp_server.py`: 20/20 PASS

## Files to create
- `mcp_server.py` (new — full content in spec)
- `test_mcp_server.py` (new — content in spec)

## Files to modify
- `requirements.txt` — add `mcp>=1.0.0`
- `.vscode/mcp.json.template` — add `awos` server entry

## Gotchas
- The `mcp` SDK uses `async` — tests must use `asyncio.run()`
- `stdio_server()` is the entry point — do not change to HTTP
- Lazy imports inside `_get_*()` functions prevent startup errors if optional deps missing
- Do not add `ANTHROPIC_API_KEY` check — let it fail naturally with a clear error

## Estimated effort
4–6 hours (mostly understanding the mcp SDK, actual code is ~120 lines)
