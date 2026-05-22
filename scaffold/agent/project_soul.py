"""
Project Soul — Permanent Cache Blocks

The "soul" of your project: invariant rules, architecture decisions, and patterns.
These are pinned to the prompt cache (5-minute TTL) to avoid re-sending 2,000 tokens
on every message.

Files in soul/:
  - rules.xml         → Constants, constraints, guardrails
  - architecture.xml  → Components, layers, dependencies
  - patterns.xml      → Code style, naming, design patterns
"""

import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime


class ProjectSoul:
    """Manage the project's invariant metadata."""
    
    def __init__(self, soul_dir: Path = None):
        self.soul_dir = Path(soul_dir) if soul_dir else Path(".awos/soul")
        self.soul_dir.mkdir(parents=True, exist_ok=True)
        self.init_soul()
    
    def init_soul(self):
        """Initialize soul/ with rules, architecture, patterns."""
        
        # soul/rules.xml
        rules_file = self.soul_dir / "rules.xml"
        if not rules_file.exists():
            rules = ET.Element("rules")
            ET.SubElement(rules, "budget").text = "15.00"  # Monthly budget in $
            ET.SubElement(rules, "monthly_limit").text = "15.00"
            ET.SubElement(rules, "request_timeout_sec").text = "30"
            ET.SubElement(rules, "max_context_tokens").text = "10000"
            
            constraints = ET.SubElement(rules, "constraints")
            ET.SubElement(constraints, "no_blind_workspace_scans").text = "Explicit hydrate_file() calls only"
            ET.SubElement(constraints, "no_full_file_rewrites").text = "Use SEARCH/REPLACE blocks only"
            ET.SubElement(constraints, "no_repeated_system_prompt").text = "Context pin with cache_control ephemeral"
            
            tree = ET.ElementTree(rules)
            tree.write(rules_file, encoding="utf-8", xml_declaration=True)
            print(f"✓ Created {rules_file}")
        
        # soul/architecture.xml
        arch_file = self.soul_dir / "architecture.xml"
        if not arch_file.exists():
            arch = ET.Element("architecture")
            arch.set("version", "1.0")
            arch.set("updated", datetime.now().isoformat())
            
            layers = ET.SubElement(arch, "layers")
            ET.SubElement(layers, "layer", name="dispatcher").text = "Route requests to models"
            ET.SubElement(layers, "layer", name="hydration").text = "Load context efficiently"
            ET.SubElement(layers, "layer", name="caching").text = "Manage prompt cache (1hr TTL)"
            ET.SubElement(layers, "layer", name="execution").text = "Run model + parse output"
            ET.SubElement(layers, "layer", name="persistence").text = "TASK_STATE.xml + monitoring"
            
            dependencies = ET.SubElement(arch, "dependencies")
            ET.SubElement(dependencies, "dep", name="litellm").text = "Model routing"
            ET.SubElement(dependencies, "dep", name="tree-sitter").text = "STRUCT.xml generation"
            ET.SubElement(dependencies, "dep", name="anthropic").text = "Claude API"
            ET.SubElement(dependencies, "dep", name="deepseek").text = "DeepSeek API"
            
            tree = ET.ElementTree(arch)
            tree.write(arch_file, encoding="utf-8", xml_declaration=True)
            print(f"✓ Created {arch_file}")
        
        # soul/patterns.xml
        patterns_file = self.soul_dir / "patterns.xml"
        if not patterns_file.exists():
            patterns = ET.Element("patterns")
            
            style = ET.SubElement(patterns, "style")
            ET.SubElement(style, "naming").text = "snake_case for functions, CamelCase for classes"
            ET.SubElement(style, "docstring").text = "NumPy style for all functions"
            ET.SubElement(style, "formatting").text = "black (88 chars), ruff strict mode"
            
            conventions = ET.SubElement(patterns, "conventions")
            ET.SubElement(conventions, "error_handling").text = "Explicit try/except, never bare except"
            ET.SubElement(conventions, "type_hints").text = "Required for all public functions"
            ET.SubElement(conventions, "imports").text = "Group: stdlib, third-party, local"
            
            trees = ET.ElementTree(patterns)
            trees.write(patterns_file, encoding="utf-8", xml_declaration=True)
            print(f"✓ Created {patterns_file}")
    
    def load_rules(self) -> dict:
        """Load rules.xml as dict."""
        rules_file = self.soul_dir / "rules.xml"
        if not rules_file.exists():
            return {}
        
        tree = ET.parse(rules_file)
        root = tree.getroot()
        
        return {
            "budget": float(root.findtext("budget", "15.00")),
            "max_context_tokens": int(root.findtext("max_context_tokens", "10000")),
            "timeout_sec": int(root.findtext("request_timeout_sec", "30")),
        }
    
    def load_architecture(self) -> dict:
        """Load architecture.xml as dict."""
        arch_file = self.soul_dir / "architecture.xml"
        if not arch_file.exists():
            return {}
        
        tree = ET.parse(arch_file)
        root = tree.getroot()
        
        layers = {}
        for layer in root.findall(".//layer"):
            layers[layer.get("name")] = layer.text
        
        return {"layers": layers}
    
    def load_patterns(self) -> dict:
        """Load patterns.xml as dict."""
        patterns_file = self.soul_dir / "patterns.xml"
        if not patterns_file.exists():
            return {}
        
        tree = ET.parse(patterns_file)
        root = tree.getroot()
        
        style = {}
        for elem in root.findall(".//style/*"):
            style[elem.tag] = elem.text
        
        return {"style": style}
    
    def get_all_soul(self) -> str:
        """
        Return all soul/ content as a compact prompt block.
        This gets pinned to the cache (once per 5 minutes).
        """
        rules = self.load_rules()
        arch = self.load_architecture()
        patterns = self.load_patterns()
        
        soul_prompt = f"""
=== PROJECT SOUL (Cached) ===

BUDGET & CONSTRAINTS:
  - Monthly Budget: ${rules.get('budget', 15.0):.2f}
  - Max Context: {rules.get('max_context_tokens', 10000)} tokens
  - Timeout: {rules.get('timeout_sec', 30)}s
  - No blind scans (explicit hydrate_file() only)
  - No full-file rewrites (SEARCH/REPLACE blocks only)
  - No redundant system prompts (cache with ephemeral)

ARCHITECTURE:
  {chr(10).join(f"  - {k}: {v}" for k, v in arch.get("layers", {}).items())}

STYLE & PATTERNS:
  {chr(10).join(f"  - {k}: {v}" for k, v in patterns.get("style", {}).items())}

=== END SOUL ===
"""
        return soul_prompt.strip()
    
    def print_soul(self):
        """Print the full soul for debugging."""
        print(self.get_all_soul())


# Quick test
if __name__ == "__main__":
    soul = ProjectSoul(".awos/soul")
    soul.print_soul()
