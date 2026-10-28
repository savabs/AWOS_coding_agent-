# Agent GUI Research - June 2026

This directory contains research on how leading open-source AI agents build their GUIs, conducted to inform AWOS GUI development.

---

## Documents

### 1. [agent_gui_patterns_2026.md](./agent_gui_patterns_2026.md)

**Comprehensive research report** analyzing 4 major open-source agent GUIs:

- **Hermes WebUI** (14K stars) - Simple Python + vanilla JS approach
- **Kanna** (556 stars) - Event-sourcing + WebSocket architecture
- **OpenGUI** (27 stars) - Electron desktop app
- **AICodeStudio** (3 stars) - PWA with Monaco editor

**Key Finding**: Our current AWOS GUI architecture (Python stdlib + vanilla JS + SSE) is validated by industry leaders. We're on the right track.

### 2. [gui_implementation_plan.md](./gui_implementation_plan.md)

**Actionable implementation roadmap** with 4 phases:

- **Phase 1** (1-2 days): PWA manifest, slash commands, theme switching
- **Phase 2** (2-3 days): AG-UI protocol alignment
- **Phase 3** (3-5 days): Workspace browser, prompt queue, auth
- **Phase 4** (5+ days): WebSocket, Monaco editor, voice input

Includes code examples, testing plan, and timeline estimates.

---

## Executive Summary

### What We're Doing Right ✅

1. **Simple architecture**: Python stdlib HTTP server + vanilla JS (validated by Hermes)
2. **Event-driven**: SSE for real-time streaming (industry standard)
3. **Clean separation**: Backend (orchestrator) vs Frontend (GUI)
4. **No build complexity**: No React/bundlers needed

### Quick Wins 🚀

1. **PWA manifest** → Desktop installation without Electron
2. **Slash commands** → `/help`, `/clear`, `/retry`, `/stop`
3. **Theme switching** → Dark/light mode
4. **AG-UI protocol** → Future-proof compatibility

### What to Skip ❌

1. **Electron** → Browser + PWA is simpler
2. **React/Vue** → Vanilla JS working well
3. **Full event sourcing** → Overkill for v1
4. **Bun/Deno** → Python stdlib is fine

---

## Key Insight: AG-UI Protocol

**AG-UI** (Agent-User Interaction) is emerging as the standard protocol for agent GUIs, adopted by:

- Google
- AWS
- Microsoft
- LangChain
- CopilotKit
- Mastra

**What it is**: Like HTTP or MCP - a specification for how agents communicate with frontends.

**Why it matters**: If we adopt AG-UI, our GUI can work with *any* AG-UI-compliant agent backend (LangGraph, CrewAI, etc.) without changes.

**Our current events are already similar** → Migration is straightforward.

---

## Recommended Next Steps

### Immediate (This Week)

1. Read `agent_gui_patterns_2026.md` fully
2. Review `gui_implementation_plan.md` Phase 1
3. Decide: Do we want to implement Phase 1 quick wins?

### Short-term (Next 2 Weeks)

1. Implement Phase 1: PWA + Slash + Theme (1-2 days)
2. Implement Phase 2: AG-UI alignment (2-3 days)
3. Update `docs/specs/gui_layer_spec.md` with findings

### Medium-term (Next Month)

1. Implement Phase 3 features selectively
2. User testing and feedback
3. Iterate based on usage patterns

---

## Resources

- **Hermes WebUI**: https://github.com/nesquena/hermes-webui
- **Kanna**: https://github.com/jakemor/kanna
- **AG-UI Protocol**: https://github.com/CopilotKit/CopilotKit
- **Awesome AI Agents 2026**: https://github.com/ARUNAGIRINATHAN-K/awesome-ai-agents-2026

---

## Questions for Discussion

1. **AG-UI adoption**: Should we align with AG-UI protocol now or wait for v2?
2. **PWA vs Desktop**: Is PWA installation sufficient, or do we need Electron later?
3. **Authentication**: Do we need password protection for network access?
4. **Workspace browser**: Is this high priority or can it wait?
5. **WebSocket upgrade**: Should we move from SSE to WS now or later?

---

**Last Updated**: June 12, 2026  
**Next Review**: After Phase 1 implementation (if we proceed)
