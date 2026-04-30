---
title: "Tool Manifest — Agent Capability Map"
tags:
  - doc/wiki
  - topic/tooling
---

# Tool Manifest — Agent Capability Map

This is the canonical reference for every tool available to the agent. Load this at session start alongside `memories/repo/project_structure.md`.

**Update rule:** When a new MCP server is added to `.vscode/mcp.json`, add it here.

---

## 1. Always-Available Tools (no load required)

### File I/O
| Tool | Purpose | Notes |
|---|---|---|
| `read_file` | Read file content with line range | Prefer over `cat` — faster, respects context |
| `create_file` | Create a new file | Fails if file exists — prevents accidental overwrite |
| `replace_string_in_file` | Edit a file in-place | Include 3–5 lines of context before/after target |
| `multi_replace_string_in_file` | Multiple edits in one call | Use when making ≥2 independent edits |
| `list_dir` | List directory contents | Use before reading files to orient |
| `view_image` | View an image file | PNG, JPG, JPEG, GIF, WebP |

### Search
| Tool | Purpose | When to Use |
|---|---|---|
| `grep_search` | Exact text / regex search | Known strings, symbol names, tag searches |
| `file_search` | Find files by glob pattern | Known filename patterns |
| `semantic_search` | Natural language codebase search | Unknown location, conceptual queries |
| `vscode_listCodeUsages` | Find all references to a symbol | Before renaming or deleting a symbol |
| `vscode_renameSymbol` | Rename symbol across workspace | Safe semantic rename |

### Terminal
| Tool | Purpose | Notes |
|---|---|---|
| `run_in_terminal` | Run shell commands | Use `mode=sync` for short commands, `mode=async` for servers |
| `get_terminal_output` | Check async terminal output | Always check before sending next command |
| `send_to_terminal` | Send input to interactive terminal | One answer per prompt |
| `kill_terminal` | Stop a background terminal | Clean up servers when done |

### Memory
| Tool | Purpose | Scope |
|---|---|---|
| `memory` view | Read memory files | User / session / repo scopes |
| `memory` create | Create new memory note | Use for insights, not temp notes |
| `memory` str_replace | Update existing memory | For corrections and additions |

### Agent & Task Management
| Tool | Purpose |
|---|---|
| `manage_todo_list` | Track multi-step task progress |
| `vscode_askQuestions` | Ask user clarifying questions (max 3) |
| `runSubagent` | Delegate to a specialized subagent |
| `tool_search` | Discover and load deferred tools |

---

## 2. Deferred Tools (must load via `tool_search` first)

Call `tool_search("description of what you need")` before using any of these.

### Web Research
| Tool | Cost | When to Use |
|---|---|---|
| `fetch_webpage` | **FREE** | Known URL — official docs, GitHub, API pages |
| `mcp_tavily_tavily_search` | 1cr (basic) / 2cr (advanced) | Discovery when URL is unknown |
| `mcp_tavily_tavily_extract` | 1cr / 5 URLs | Batch URL reading (3+ URLs) |
| `mcp_tavily_tavily_crawl` | 1cr / 5 pages | Read entire docs site |
| `mcp_tavily_tavily_research` | 5–20cr | Deep multi-angle research — **user approval required** |

**Decision tree:**
```
Have URL? → fetch_webpage (FREE)
Need URL? → mcp_tavily_tavily_search basic (1cr) → read result with fetch_webpage (FREE)
Need batch URLs? → mcp_tavily_tavily_extract (1cr/5)
Need whole site? → mcp_tavily_tavily_crawl (1cr/5pages)
Complex research? → ASK USER first → mcp_tavily_tavily_research (5-20cr)
```

### Git (local)
| Tool | Purpose |
|---|---|
| `mcp_git_git_status` | Working tree status |
| `mcp_git_git_log` | Commit history |
| `mcp_git_git_diff` | Show unstaged/staged changes |
| `mcp_git_git_add` | Stage files |
| `mcp_git_git_commit` | Create commit |
| `mcp_git_git_branch` | List / create branches |
| `mcp_git_git_checkout` | Switch branch |

### GitHub (remote)
| Tool | Purpose |
|---|---|
| `mcp_github_issue_read` | Read issues |
| `mcp_github_pull_request_read` | Read PRs |
| `mcp_github_get_file_contents` | Read file from any repo/branch |
| `mcp_github_search_code` | Search code across GitHub |
| `mcp_github_list_commits` | Commit history |
| `mcp_github_create_pull_request` | Open a PR |

### Library Documentation
| Tool | Purpose |
|---|---|
| `mcp_context7_resolve-library-id` | Find a library's Context7 ID |
| `mcp_context7_query-docs` | Get current, version-accurate API docs |

**Use before any library implementation** to avoid hallucinating deprecated APIs.

### Structured Reasoning
| Tool | Purpose |
|---|---|
| `mcp_sequential-th_sequentialthinking` | Multi-step reasoning with revision |

Use when: choosing between design options, debugging complex chains, planning a multi-step implementation.

### Persistent Memory Graph
| Tool | Purpose |
|---|---|
| `mcp_memory_create_entities` | Add named entities to the knowledge graph |
| `mcp_memory_create_relations` | Link entities |
| `mcp_memory_add_observations` | Add facts to entities |
| `mcp_memory_search_nodes` | Search the graph |
| `mcp_memory_read_graph` | Full graph dump |

Use for: tracking entities across sessions (people, companies, symbols, concepts).

---

## 3. Tool Use Discipline

### Never hallucinate tool availability
Always call `tool_search` before using a deferred tool. If it returns nothing, the tool is not available.

### Prefer cheaper tools first
`fetch_webpage` > `tavily_search` > `tavily_research`
`grep_search` > `semantic_search` (grep is faster for exact matches)
`read_file` > `run_in_terminal cat` (read_file is more efficient)

### Parallel calls for independent operations
When gathering context from multiple files, call `read_file` on all of them in one parallel batch.
Do NOT chain sequential reads unless each depends on the previous.

### Terminal for verification
Running `python -c "import lib; print(lib.func())"` costs 0 credits and gives ground truth.
Prefer terminal probing over Tavily for API/library verification.

### Cost guard for Tavily
Track Tavily credit spend. After 5 credits in a session, ask before spending more.
Never use `tavily_research` without explicit user approval.

---

## 4. MCP Setup

Copy `.vscode/mcp.json.template` to `.vscode/mcp.json` and set environment variables:

```bash
export TAVILY_API_KEY="tvly-..."
export GITHUB_TOKEN="ghp_..."
```

Or add them to `.env` (never commit `.env`). The MCP servers will auto-start when VS Code loads.

## Related

- [[copilot-instructions]] — the operating instructions that govern tool use rules
- [[AWOS]] — full doctrine including Tool Capability Matrix section
