#!/usr/bin/env python3
"""
Chat-based workflow orchestrator.
Natural language interface to project workflow automation.

Usage:
    python3 chat_orchestrator.py              # Start interactive chat
    python3 chat_orchestrator.py "what's next?"  # Single query
"""

import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass

# Import orchestrator
from workflow_orchestrator import ProjectOrchestrator, ProjectTask, TaskStatus


@dataclass
class Intent:
    """Parsed user intent"""
    action: str  # 'status', 'next', 'plan', 'auto', 'help', 'exit', 'unknown'
    count: Optional[int] = None  # for 'auto' action
    raw_input: str = ""


class IntentParser:
    """Parse natural language into actions"""

    INTENT_PATTERNS = {
        'status': [
            r'what.*status',
            r'where.*am.*i',
            r'current.*state',
            r'progress',
            r'how.*done',
            r'what.*stage',
            r'show.*status',
            r"where's.*i",
            r'what.*complete',
        ],
        'next': [
            r"what.*next",
            r'what.*should.*(?:i|do)',
            r'should.*(?:i|do).*work',
            r'work on',
            r'do.*next',
            r'execute.*next',
            r'start.*next',
            r'run.*next.*task',
            r'proceed',
            r'continue',
            r'go ahead',
            r"let's.*go",
            r'begin',
            r'start',
        ],
        'plan': [
            r'show.*plan',
            r'display.*plan',
            r'what.*tasks',
            r'full.*workflow',
            r'all.*tasks',
            r'roadmap',
            r'workflow',
            r'what.*stages',
            r'view.*plan',
            r'list.*tasks',
        ],
        'auto': [
            r'run.*(\d+)',
            r'execute.*(\d+)',
            r'do.*(\d+)',
            r'batch.*(\d+)',
            r'auto.*(\d+)',
            r'next.*(\d+)',
            r'complete.*(\d+)',
        ],
        'help': [
            r'help',
            r'what.*can.*do',
            r'how.*use',
            r'commands',
        ],
        'exit': [
            r'exit',
            r'quit',
            r'bye',
            r'done',
            r'leave',
            r'goodbye',
        ],
    }

    @classmethod
    def parse(cls, user_input: str) -> Intent:
        """Parse user input into intent"""
        user_input = user_input.lower().strip()

        # Check each pattern
        for action, patterns in cls.INTENT_PATTERNS.items():
            for pattern in patterns:
                match = re.search(pattern, user_input)
                if match:
                    count = None
                    # Extract count for 'auto' action
                    if action == 'auto' and match.groups():
                        try:
                            count = int(match.group(1))
                        except (ValueError, IndexError):
                            count = 5  # default to 5

                    return Intent(action=action, count=count, raw_input=user_input)

        return Intent(action='unknown', raw_input=user_input)


class ChatOrchestrator:
    """Chat interface for workflow orchestrator"""

    def __init__(self, project_root: Optional[str] = None):
        """Initialize chat orchestrator"""
        if project_root is None:
            project_root = str(Path(__file__).parent.parent.parent)

        os.chdir(project_root)
        self.orchestrator = ProjectOrchestrator()
        self.parser = IntentParser()
        self.running = False

    # ===== Response Generators =====

    def _format_status(self) -> str:
        """Format status for chat"""
        status = self.orchestrator.get_project_status()
        total = status.get('total_tasks', 0)
        completed = status.get('completed', 0)
        percent = (completed / total * 100) if total > 0 else 0

        lines = [
            "📊 **Project Status**",
            "",
            f"**Stage:** {status.get('current_stage', 'init').upper()}",
            f"**Progress:** {completed}/{total} completed ({percent:.0f}%)",
            f"**Total Cost:** ${status.get('total_cost', 0):.4f}",
            "",
        ]

        # Find next task
        next_task = self.orchestrator.get_next_task()
        if next_task:
            files = ', '.join(next_task.files_to_modify) if next_task.files_to_modify else 'N/A'
            lines.extend([
                "**🎯 Next Step:**",
                f"- **{next_task.title}**",
                f"- {next_task.description}",
                f"- Stage: {next_task.stage.value}",
                f"- Files: {files}",
                "",
            ])
        else:
            lines.append("✅ **All tasks completed!**")

        return "\n".join(lines)

    def _format_plan(self) -> str:
        """Format full workflow plan"""
        # Group tasks by stage
        stages = {}
        for task in self.orchestrator.tasks.values():
            stage = task.stage.value
            if stage not in stages:
                stages[stage] = []
            stages[stage].append(task)

        lines = ["📋 **Full Workflow Plan**", ""]

        stage_order = [
            'setup', 'structure', 'features', 'testing', 'security',
            'optimization', 'documentation', 'deployment'
        ]

        for stage in stage_order:
            if stage not in stages:
                continue

            stage_tasks = stages[stage]
            lines.append(f"**▶ {stage.upper()}**")

            for task in stage_tasks:
                status_icon = {
                    'completed': '✅',
                    'in_progress': '🔄',
                    'pending': '⏳',
                    'failed': '❌',
                    'blocked': '🚫',
                }.get(task.status.value, '⏳')

                title = task.title
                desc = task.description
                deps = task.depends_on

                lines.append(f"  {status_icon} **{title}**")
                lines.append(f"     {desc}")
                if deps:
                    lines.append(f"     Dependencies: {', '.join(deps)}")

            lines.append("")

        return "\n".join(lines)

    def _format_help(self) -> str:
        """Format help message"""
        return """🤖 **Workflow Assistant**

I understand natural language! Just ask me things like:

**Status & Planning:**
- "What's my status?" / "Where am I?" / "Show progress"
- "What should I work on?" / "What's next?"
- "Show me the plan" / "What tasks are there?"

**Execution:**
- "Let's do the next step" / "Proceed"
- "Run 5 tasks" / "Do 3 more" / "Auto-execute 10"

**Control:**
- "Help" / "What can you do?"
- "Exit" / "Quit" / "Done"

**Examples:**
```
> what's my current status?
> show me the plan
> what should i work on?
> run the next task
> execute 3 tasks
> can i see the plan?
> i'm done, exit
```

I'll figure out what you mean and do it! 🚀"""

    # ===== Action Handlers =====

    def handle_status(self) -> str:
        """Handle status request"""
        return self._format_status()

    def handle_next(self) -> str:
        """Handle next task execution"""
        next_task = self.orchestrator.get_next_task()

        if not next_task:
            return "✅ **All tasks completed!**\n\nRun `orchestrate plan` to see full workflow."

        title = next_task.title
        desc = next_task.description
        files = ', '.join(next_task.files_to_modify) if next_task.files_to_modify else 'N/A'

        return f"""🚀 **Executing: {title}**

{desc}

**Files:** {files}

I'm launching Aider now... Type `/exit` in Aider when done."""

    def handle_plan(self) -> str:
        """Handle plan request"""
        return self._format_plan()

    def handle_auto(self, count: Optional[int] = None) -> str:
        """Handle auto-execute request"""
        if count is None:
            count = 5

        status = self.orchestrator.get_project_status()
        total = status.get('total_tasks', 0)
        completed = status.get('completed', 0)
        pending = total - completed

        if pending == 0:
            return "✅ **All tasks already completed!**"

        if count > pending:
            count = pending

        return f"""🔄 **Auto-Executing {count} Tasks**

I'll run the next {count} logical tasks in sequence:
- Execute each task with Aider
- After each one, ask you to confirm before continuing
- Track progress automatically

Ready to go? I'll start now...

(Type `/exit` in Aider to skip a task and move to the next one)"""

    def handle_help(self) -> str:
        """Handle help request"""
        return self._format_help()

    def handle_unknown(self, user_input: str) -> str:
        """Handle unknown intent"""
        return f"""🤔 I didn't quite understand: "{user_input}"

Try asking me things like:
- "What should I work on?"
- "Show me the plan"
- "Run the next task"
- "Do 5 more tasks"

Or type "help" for more examples!"""

    # ===== Chat Loop =====

    def process_input(self, user_input: str) -> Tuple[str, bool]:
        """
        Process user input and return response + continue flag

        Returns:
            (response_text, should_continue)
        """
        if not user_input.strip():
            return "", True

        intent = self.parser.parse(user_input)

        # Route to handler
        if intent.action == 'status':
            response = self.handle_status()
        elif intent.action == 'next':
            response = self.handle_next()
        elif intent.action == 'plan':
            response = self.handle_plan()
        elif intent.action == 'auto':
            response = self.handle_auto(intent.count)
        elif intent.action == 'help':
            response = self.handle_help()
        elif intent.action == 'exit':
            return "👋 Goodbye!", False
        else:
            response = self.handle_unknown(user_input)

        return response, True

    def run_interactive(self):
        """Run interactive chat loop"""
        self.running = True

        print("\n" + "=" * 70)
        print("🤖 WORKFLOW ASSISTANT (Chat-Based Orchestrator)")
        print("=" * 70)
        print("\nType 'help' for commands or just ask naturally!")
        print("Type 'exit' to quit.\n")

        # Show initial status
        print(self._format_status())
        print()

        while self.running:
            try:
                user_input = input("You: ").strip()

                if not user_input:
                    continue

                response, should_continue = self.process_input(user_input)

                if response:
                    print(f"\nAssistant: {response}\n")

                if not should_continue:
                    self.running = False

            except KeyboardInterrupt:
                print("\n\n👋 Goodbye!")
                self.running = False
            except EOFError:
                self.running = False

    def run_single_query(self, query: str):
        """Run a single query and exit"""
        response, _ = self.process_input(query)
        if response:
            print(response)


def main():
    """Main entry point"""
    chat = ChatOrchestrator()

    if len(sys.argv) > 1:
        # Single query mode
        query = " ".join(sys.argv[1:])
        chat.run_single_query(query)
    else:
        # Interactive mode
        chat.run_interactive()


if __name__ == "__main__":
    main()
