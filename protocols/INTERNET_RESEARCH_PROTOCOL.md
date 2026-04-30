# Internet Research Protocol

> **Corollary: never hallucinate facts about external systems.**
> Any factual assertion about APIs, libraries, data sources, or mathematical methods
> must be verified against a real source before it enters code, specs, or research docs.

---

## Tool Selection Decision Tree

```
Do I have a specific URL?
  YES → fetch_webpage (FREE, always first choice)

  NO → Do I know the exact official docs URL from context?
         YES → fetch_webpage (FREE)

         NO → What kind of discovery is needed?
                Simple — find the right docs/repo:
                  → tavily_search (basic, max_results=5) [1 credit]
                  → Read best result with fetch_webpage (FREE)

                Complex — compare multiple sources, niche topic:
                  → tavily_search (basic first, max_results=5)
                  → If insufficient: tavily_search (advanced) [2 credits]

                Need 3+ pages from one documentation site:
                  → tavily_crawl or tavily_extract [1 credit/5 pages]

                Deep multi-query research (rare):
                  → ASK USER first → tavily_research [5–20 credits]
```

---

## Tool Reference

| Tool | Cost | Use when |
|---|---|---|
| `fetch_webpage` | **FREE** | URL is known (user provided, official docs, GitHub README) |
| `tavily_search` basic | 1 credit | Discovery — finding the right source |
| `tavily_search` advanced | 2 credits | Basic returned insufficient results on niche topic |
| `tavily_extract` | 1 credit/5 URLs | Batch fetch of 3+ known URLs at once |
| `tavily_crawl` | 1 credit/5 pages | Need comprehensive coverage of a documentation site |
| `tavily_map` | 1 credit/10 pages | Discover URL structure before crawling |
| `tavily_research` | 5–20 credits | Deep multi-angle research — **always ask user first** |

---

## Hard Rules

**Rule 1: If the user provides a URL, use `fetch_webpage`.** Never route through Tavily for a URL the user gave you. It is free and gives full content.

**Rule 2: If you know the official docs URL, use `fetch_webpage` directly.** Don't search for PyPI, GitHub READMEs, or official library docs when you know the URL pattern.

**Rule 3: Default `tavily_search` parameters: `depth=basic`, `max_results=5`.** Only escalate to `advanced` if basic returns nothing useful on a genuinely obscure topic.

**Rule 4: After `tavily_search` finds a URL, read it with `fetch_webpage` (free).** Don't use `tavily_extract` for single URLs.

**Rule 5: `tavily_extract` is for batches only.** If you need 3+ known URLs, batch them into one `tavily_extract` call. For 1–2 URLs, use `fetch_webpage` on each.

**Rule 6: `tavily_research` requires explicit user approval.** It is expensive (5–20 credits). Ask before using.

**Rule 7: Prefer terminal testing over search for verification.** Testing an API call is free and provides ground truth:
```bash
python -c "import requests; r = requests.get('https://api.example.com/v1/data'); print(r.json())"
```
This confirms the endpoint exists, the response schema, and the auth requirements simultaneously.
No Tavily credits needed.

**Rule 8: Cache within session.** If you searched for something once this session, don't search again — reference the earlier result. Session search knowledge is free to reuse.

---

## What Must Be Verified (Never Guessed)

The following facts must be verified against a real source before entering code, specs, or research docs:

### API / Service Facts
- Endpoint URLs, path parameters, query parameters
- Authentication method (API key, OAuth, bearer token)
- Request and response schemas (field names, types, nesting)
- Rate limits, quotas, pagination behavior
- Which fields are optional vs. required
- Error response codes and their meanings

### Library / Framework Facts
- Function signatures and their parameter names
- Default parameter values
- Return types and possible exceptions
- Required vs. optional imports
- Version-specific behavior (function moved, renamed, deprecated)

### Data Source Facts
- What symbols, identifiers, or codes are valid
- Historical data availability window
- Update frequency and freshness guarantees
- Geographic or instrument coverage
- License and attribution requirements

### Mathematical Method Facts
- Convergence guarantees and conditions
- Computational complexity
- Numerical stability under what conditions
- Required input assumptions (stationarity, independence, etc.)
- Known failure modes

---

## When Search Returns Nothing Useful

1. Try different keyword variants and synonyms
2. Try searching for the specific error message or behavior
3. Try terminal verification (run the code, check the actual behavior)
4. If still stuck: **say so explicitly.** Mark the claim as "UNVERIFIED" in the research doc.
5. Never fill the gap with a plausible-sounding guess.

```markdown
## External Sources

| Source | URL | Status |
|---|---|---|
| API docs endpoint format | https://api.example.com/docs | VERIFIED — accessed 2026-04-28 |
| Rate limit | — | UNVERIFIED — could not find in docs; test before production use |
```

---

## Recording Verified Sources

Every factual claim in a research doc must be traceable to a verified source.

Standard format:
```markdown
## External Sources

| Claim | Source | URL | Date |
|---|---|---|---|
| Endpoint is /v2/data | Official API docs | https://docs.example.com/api | 2026-04-28 |
| Rate limit is 100 req/min | Rate limits page | https://docs.example.com/limits | 2026-04-28 |
| Library uses X algorithm | Paper: Author et al. 2019 | https://arxiv.org/abs/XXXX | 2026-04-28 |
```

---

## Token-Saving Strategies

1. **Use `fetch_webpage` for known docs.** PyPI pages, GitHub READMEs, official library docs all have predictable URL patterns. These are free.

2. **Batch related searches.** Instead of 5 searches for 5 related topics, do one broad search: "yfinance supported futures symbols commodity energy metals agricultural."

3. **Write findings to files immediately.** The research doc is the persistent search cache. Findings written now save re-searching next session.

4. **For API verification, run it.** `python -c "from library import func; print(func.__doc__)"` is faster than searching.

5. **Don't verify what was already verified.** If this session's research doc already lists a source, reference it directly. Don't search for it again.
