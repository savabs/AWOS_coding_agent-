# Chat Interface Quick Reference

**Start interactive chat:**
```bash
orchestrate-chat
```

**Ask a single question:**
```bash
orchestrate-chat "what's my status?"
orchestrate-chat "show me the plan"
orchestrate-chat "run 3 tasks"
```

---

## Natural Language Examples

### Get Status
```
> what's my status?
> where am i?
> show progress
> what stage am i in?
> how many tasks are done?
```

### Get Next Task
```
> what should i work on?
> what's next?
> what do i do next?
> proceed
> let's go
```

### See Full Plan
```
> show me the plan
> what are all the tasks?
> what's the full workflow?
> list all tasks
> show the roadmap
```

### Run Tasks
```
> run the next step
> execute 3 tasks
> do 5 more
> auto-execute 10
> start the next task
```

### Get Help
```
> help
> what can you do?
> how do i use this?
> show examples
```

### Exit
```
> exit
> quit
> bye
> done
```

---

## Real Conversation Example

```
$ orchestrate-chat

🤖 WORKFLOW ASSISTANT
Type 'help' for commands or just ask naturally!
Type 'exit' to quit.

📊 **Project Status**

**Stage:** INIT
**Progress:** 0/11 completed (0%)

**🎯 Next Step:**
- **Initialize Project Structure**

You: where am i?
Assistant: 📊 **Project Status**
...

You: what should i do?
Assistant: 🚀 **Executing: Initialize Project Structure**
...

You: show me the plan
Assistant: 📋 **Full Workflow Plan**
...

You: help
Assistant: 🤖 **Workflow Assistant**
I understand natural language!
...

You: exit
Assistant: 👋 Goodbye!
```

---

## How It Works

The chat interface uses **intent parsing** to understand what you mean:

1. You type something natural: `"what's next?"`
2. Parser detects intent: `'next'`
3. System performs action: Shows next task
4. Returns response in conversational format

The agent internally decides what to do. You don't need to remember commands.

---

## Intents the Agent Recognizes

| Your Input | What Agent Does | Examples |
|---|---|---|
| Status intent | Shows project progress | "Where am I?", "What's done?" |
| Next intent | Shows next task | "What's next?", "Proceed" |
| Plan intent | Shows all tasks | "Show the plan", "List all" |
| Auto intent | Run multiple tasks | "Run 5", "Do 3 more" |
| Help intent | Show help | "Help", "What can you do?" |
| Exit intent | Quit chat | "Exit", "Done", "Bye" |

---

## Tips

✅ **Be natural** — Don't worry about exact wording
```
Good: "what should i work on?"
Good: "what's the next thing?"
Good: "where am i?"
```

✅ **Specify numbers for auto-run**
```
Good: "run 5 tasks"
Bad:  "run some tasks"
```

✅ **Use familiar phrases**
```
Good: "show me the plan"
Good: "proceed"
Good: "where am i?"
```

❌ **Don't overthink it** — If it doesn't work, just ask "help"

---

## Integration with Aider

When you ask "what's next?" or "run the next step":

1. Chat interface finds next task
2. Launches Aider with files and prompt
3. You chat with Aider to modify code
4. When you `/exit` Aider, orchestrator marks task complete
5. Next time you ask "what's next?", it knows to move forward

**No manual state tracking needed!**
