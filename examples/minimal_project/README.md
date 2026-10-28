---
title: "Example: Minimal Project Walk-through"
tags:
  - doc/wiki
  - topic/example
---

# Example: Minimal Project Walk-through

This example walks through using Agentic OS for a small real project:
**adding a web search tool to the agent scaffold**.

It demonstrates the full workflow: Research → Spec → Task → Implement → Checkpoint.

---

## Step 0: Bootstrap

```bash
python scripts/new_project.py --name my-agent --dest ~/projects/my-agent
cd ~/projects/my-agent
code .
```

Open `memories/repo/project_structure.md` and fill in your project's identity.

---

## Step 1: Write the Research Note

Create `docs/research/web_search_tool.md` from the template:

```bash
cp docs/research/RESEARCH_TEMPLATE.md docs/research/web_search_tool.md
```

Fill it in:

```markdown
---
title: "Research: Web Search Tool"
tags:
  - doc/research
  - phase/1
  - topic/search
  - layer/surveillance
---

# Research: Web Search Tool

## Current Architecture
- tools/base.py has Tool ABC and ToolRegistry
- No external search tool exists yet

## Observations
- Need: fetch web content given a query string
- Options: SerpAPI (paid), DuckDuckGo Instant Answer API (free), Brave Search API (free tier)
- DuckDuckGo Instant Answer: https://api.duckduckgo.com/?q=<query>&format=json
  - No auth required, rate limits apply, not a full web search

## Risks
- Rate limiting under load
- Results may not include all pages (Instant Answer, not full index)

## Data Requirements
- Input: query string
- Output: list of {title, url, snippet}

## Math/Algorithm Survey
- No math required — pure I/O tool

## External Sources
- DuckDuckGo Instant Answer API: https://api.duckduckgo.com/
- Requests library: https://requests.readthedocs.io/

## Depth Roadmap
- L1: Return top-level search results (today)
- L2: Entity extraction from results
- L3: Cross-domain entity linking

## Related

- [[web_search_tool_spec]]
- [[web_search_tool_task]]
```

---

## Step 2: Write the Spec

Create `docs/specs/web_search_tool_spec.md`:

```markdown
---
title: "Spec: Web Search Tool"
tags:
  - doc/spec
  - phase/1
  - topic/search
  - layer/surveillance
---

# Spec: Web Search Tool

## Goal
Implement a WebSearchTool that fetches results from DuckDuckGo Instant Answer API.

## Files Affected
- `agent/tools/web_search.py` (new)
- `agent/cli.py` (register the tool)
- `scaffold/tests/test_scaffold.py` (add 3 tests)

## Implementation Steps
1. Create `agent/tools/web_search.py` with WebSearchTool class
2. Implement execute(): call DDG API, parse JSON, return ToolResult
3. Add timeout (10s) and error handling for network failures
4. Register WebSearchTool in cli.py
5. Write tests: happy path (mock response), network timeout, empty results

## Interface Contract
- Parameters: {"query": "search query string"}
- Returns: ToolResult.ok with data={"results": [{title, url, snippet}, ...]}
- On failure: ToolResult.fail with descriptive error message

## Edge Cases
- Empty query string → validation error before calling API
- Network timeout → ToolResult.fail("Request timed out")
- API returns 0 results → ToolResult.ok with empty results list, not a failure

## Testing Plan
- Mock requests.get to test parsing logic without network
- Mock timeout to test error handling
- Pass empty query to test validation

## Related
- [[web_search_tool]] (research)
- [[web_search_tool_task]] (task)
```

---

## Step 3: Create the Task File

Create `tasks/active/web_search_tool.md`:

```markdown
---
title: "Task: Web Search Tool"
tags:
  - doc/task
  - status/active
  - phase/1
  - topic/search
  - layer/surveillance
---

# Task: Web Search Tool

Status: active
Research: [[web_search_tool]]
Spec: [[web_search_tool_spec]]

## Steps

- [ ] 1.1: Create agent/tools/web_search.py with WebSearchTool stub
- [ ] 1.2: Implement execute() with DDG API call and JSON parsing
- [ ] 1.3: Add timeout and error handling
- [ ] 1.4: Register tool in cli.py
- [ ] 1.5: Write 3 tests (happy + 2 failure cases)
- [ ] 1.6: Run tests — all pass
- [ ] 1.7: Update memories/repo/project_structure.md with new tool count

## Related
- [[web_search_tool]] — research
- [[web_search_tool_spec]] — spec
```

---

## Step 4: Implement (one step at a time)

For each step in the task file:
1. Mark it `[~]` (in-progress) in the task file
2. Make the change
3. Run the relevant test
4. Mark it `[x]` (done)
5. Move to the next step

For step 1.1:
```python
# agent/tools/web_search.py
import requests
from .base import Tool, ToolResult

class WebSearchTool(Tool):
    name = "web_search"
    description = "Search the web using DuckDuckGo Instant Answer API"
    parameters = {"query": "Search query string"}

    def execute(self, args: dict) -> ToolResult:
        query = args["query"].strip()
        if not query:
            return ToolResult.fail("Query cannot be empty")
        try:
            resp = requests.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            results = [
                {"title": r.get("Text", ""), "url": r.get("FirstURL", ""), "snippet": r.get("Text", "")}
                for r in data.get("RelatedTopics", [])
                if isinstance(r, dict) and r.get("FirstURL")
            ]
            return ToolResult.ok(f"Found {len(results)} results for '{query}'", {"results": results})
        except requests.Timeout:
            return ToolResult.fail("Request timed out")
        except requests.RequestException as e:
            return ToolResult.fail(f"Network error: {e}")
```

---

## Step 5: Write and Run Tests

```bash
pytest scaffold/tests/test_scaffold.py -v
```

All tests should pass. Then run the quality gate:

```bash
python scripts/quality_gate.py --task tasks/active/web_search_tool.md
```

---

## Step 6: Write a Checkpoint

```bash
python scripts/session_checkpoint.py -m "Implemented WebSearchTool with DDG API; 3 tests pass"
```

---

## Step 7: Mark Task Complete

1. Edit `tasks/active/web_search_tool.md`:
   - Change `Status: active` to `Status: completed`
   - Change `status/active` tag to `status/done`
2. Move to `tasks/done/`:
   ```bash
   mv tasks/active/web_search_tool.md tasks/done/web_search_tool.md
   ```
3. Update `memories/repo/project_structure.md` with new tool count.

---

## Related

- [[AWOS]] — full doctrine
- [[QUICK_START]] — 5-minute setup guide
- [[GLOSSARY]] — key terms
