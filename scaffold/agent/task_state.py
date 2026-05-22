"""
TaskState Persistence — Delta State (Not Chat History)

Problem: 50-turn chat = grow tokens exponentially.
Solution: Keep a TASK_STATE.xml that is updated after every turn.
Delete old chat messages; keep only the current state.

This keeps history cost FLAT instead of exponential.
"""

import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Any
import json


class TaskState:
    """Minimal, flat state that replaces chat history."""
    
    def __init__(self, task_id: str, state_dir: Path = None):
        self.task_id = task_id
        self.state_dir = Path(state_dir) if state_dir else Path(".awos/state")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_dir / f"{task_id}.xml"
        self.init_state()
    
    def init_state(self):
        """Initialize empty state."""
        if not self.state_file.exists():
            root = ET.Element("task_state")
            root.set("task_id", self.task_id)
            root.set("created", datetime.now().isoformat())
            root.set("updated", datetime.now().isoformat())
            
            # Task metadata
            ET.SubElement(root, "status").text = "in_progress"
            ET.SubElement(root, "step").text = "1"
            ET.SubElement(root, "description").text = "(empty)"
            
            # What files are being worked on
            ET.SubElement(root, "files_modified")
            
            # What's been decided/learned
            ET.SubElement(root, "decisions")
            
            # Token tracking
            tracking = ET.SubElement(root, "tracking")
            ET.SubElement(tracking, "tokens_used").text = "0"
            ET.SubElement(tracking, "cost_so_far").text = "0.0"
            ET.SubElement(tracking, "estimated_total").text = "0.0"
            
            # Current focus
            ET.SubElement(root, "current_focus").text = "(initializing)"
            
            tree = ET.ElementTree(root)
            tree.write(self.state_file, encoding="utf-8", xml_declaration=True)
    
    def update(self, **kwargs):
        """Update state fields."""
        tree = ET.parse(self.state_file)
        root = tree.getroot()
        root.set("updated", datetime.now().isoformat())
        
        for key, value in kwargs.items():
            elem = root.find(key)
            if elem is None:
                elem = ET.SubElement(root, key)
            elem.text = str(value)
        
        tree.write(self.state_file, encoding="utf-8", xml_declaration=True)
    
    def add_file_modified(self, file_path: str, operation: str = "modified"):
        """Log a file modification."""
        tree = ET.parse(self.state_file)
        root = tree.getroot()
        
        files_elem = root.find("files_modified")
        if files_elem is None:
            files_elem = ET.SubElement(root, "files_modified")
        
        file_elem = ET.SubElement(files_elem, "file")
        file_elem.set("path", file_path)
        file_elem.set("operation", operation)
        file_elem.set("timestamp", datetime.now().isoformat())
        
        tree.write(self.state_file, encoding="utf-8", xml_declaration=True)
    
    def add_decision(self, decision: str, rationale: str = ""):
        """Log a decision made."""
        tree = ET.parse(self.state_file)
        root = tree.getroot()
        
        decisions_elem = root.find("decisions")
        if decisions_elem is None:
            decisions_elem = ET.SubElement(root, "decisions")
        
        decision_elem = ET.SubElement(decisions_elem, "decision")
        decision_elem.set("timestamp", datetime.now().isoformat())
        ET.SubElement(decision_elem, "what").text = decision
        if rationale:
            ET.SubElement(decision_elem, "why").text = rationale
        
        tree.write(self.state_file, encoding="utf-8", xml_declaration=True)
    
    def update_tracking(self, tokens_used: int, cost: float, estimated_total: float = None):
        """Update token/cost tracking."""
        tree = ET.parse(self.state_file)
        root = tree.getroot()
        
        tracking = root.find("tracking")
        if tracking is None:
            tracking = ET.SubElement(root, "tracking")
        
        tracking.find("tokens_used").text = str(tokens_used)
        tracking.find("cost_so_far").text = f"{cost:.6f}"
        
        if estimated_total:
            tracking.find("estimated_total").text = f"{estimated_total:.6f}"
        
        tree.write(self.state_file, encoding="utf-8", xml_declaration=True)
    
    def load(self) -> Dict[str, Any]:
        """Load state as dict."""
        if not self.state_file.exists():
            return {}
        
        tree = ET.parse(self.state_file)
        root = tree.getroot()
        
        state = {
            "task_id": root.get("task_id"),
            "status": root.findtext("status", ""),
            "step": int(root.findtext("step", "0")),
            "description": root.findtext("description", ""),
            "current_focus": root.findtext("current_focus", ""),
        }
        
        # Get tracking
        tracking = root.find("tracking")
        if tracking is not None:
            state["tracking"] = {
                "tokens_used": int(tracking.findtext("tokens_used", "0")),
                "cost_so_far": float(tracking.findtext("cost_so_far", "0.0")),
                "estimated_total": float(tracking.findtext("estimated_total", "0.0")),
            }
        
        # Get files modified
        files_elem = root.find("files_modified")
        if files_elem is not None:
            state["files"] = [
                {
                    "path": f.get("path"),
                    "operation": f.get("operation"),
                    "timestamp": f.get("timestamp"),
                }
                for f in files_elem.findall("file")
            ]
        
        # Get decisions
        decisions_elem = root.find("decisions")
        if decisions_elem is not None:
            state["decisions"] = [
                {
                    "what": d.findtext("what"),
                    "why": d.findtext("why", ""),
                    "timestamp": d.get("timestamp"),
                }
                for d in decisions_elem.findall("decision")
            ]
        
        return state
    
    def get_as_prompt_block(self) -> str:
        """Return state as compact prompt block (for context)."""
        state = self.load()
        
        lines = [
            "=== TASK STATE (Not Chat History) ===",
            f"Task: {state.get('task_id')}",
            f"Status: {state.get('status')} (step {state.get('step')})",
            f"Focus: {state.get('current_focus')}",
        ]
        
        if state.get("tracking"):
            t = state["tracking"]
            lines.append(f"Tokens: {t['tokens_used']} (cost ${t['cost_so_far']:.4f})")
        
        if state.get("decisions"):
            lines.append(f"Decisions made: {len(state['decisions'])}")
        
        if state.get("files"):
            lines.append(f"Files modified: {len(state['files'])}")
        
        lines.append("=== END TASK STATE ===")
        return "\n".join(lines)
    
    def clear_old_chat(self):
        """
        Delete old chat history.
        Keep only this state file.
        This prevents exponential token growth.
        """
        # In a real system, you'd delete old conversation.json files
        # For now, this is a placeholder
        pass
    
    def print_state(self):
        """Pretty-print state."""
        state = self.load()
        print("\n" + "=" * 70)
        print(f"TASK STATE: {state.get('task_id')}")
        print("=" * 70)
        print(f"Status: {state.get('status')}")
        print(f"Step: {state.get('step')}")
        print(f"Focus: {state.get('current_focus')}")
        
        if state.get("tracking"):
            t = state["tracking"]
            print(f"\nTracking:")
            print(f"  Tokens used: {t['tokens_used']}")
            print(f"  Cost: ${t['cost_so_far']:.4f}")
        
        if state.get("decisions"):
            print(f"\nDecisions ({len(state['decisions'])}):")
            for d in state["decisions"][-3:]:  # Last 3
                print(f"  - {d['what']}")
        
        if state.get("files"):
            print(f"\nFiles Modified ({len(state['files'])}):")
            for f in state["files"][-3:]:  # Last 3
                print(f"  - {f['path']} ({f['operation']})")
        
        print("=" * 70 + "\n")


# Quick test
if __name__ == "__main__":
    state = TaskState("test_task", ".awos/state")
    
    # Update state
    state.update(
        status="in_progress",
        step=1,
        description="Fix bug in dispatcher",
        current_focus="search_replace_parser.py"
    )
    
    state.add_decision("Use SEARCH/REPLACE blocks", "To avoid full-file rewrites")
    state.add_file_modified("scaffold/agent/dispatcher.py", "modified")
    state.update_tracking(tokens_used=1500, cost=0.0075, estimated_total=0.015)
    
    state.print_state()
    print(state.get_as_prompt_block())
