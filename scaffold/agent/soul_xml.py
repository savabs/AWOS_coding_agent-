"""
SoulXML: Persistent project memory (SOUL.xml).
Stores: rules, patterns, decisions, cost history, learnings.
"""

import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass


@dataclass
class Learning:
    """A learned pattern."""

    pattern: str
    type: str  # "success", "failure", "optimization"
    confidence: float  # 0–1
    context: str  # What situation this applies to
    date: str


class SoulXML:
    """Manage SOUL.xml persistence."""

    SOUL_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<soul>
  <metadata>
    <project-name>AWOS V2 Project</project-name>
    <created>{created}</created>
    <last-updated>{last_updated}</last-updated>
    <version>1.0</version>
  </metadata>
  
  <rules>
    <!-- Rules learned from experience -->
  </rules>
  
  <patterns>
    <!-- Patterns that work well -->
  </patterns>
  
  <decisions>
    <!-- Major decisions made -->
  </decisions>
  
  <cost-history>
    <!-- Cost tracking for optimization -->
  </cost-history>
</soul>
"""

    def __init__(self, soul_path: Path = None):
        self.soul_path = Path(soul_path) if soul_path else Path(".awos/SOUL.xml")

    def init_soul(self) -> Path:
        """Create SOUL.xml if it doesn't exist."""
        if self.soul_path.exists():
            return self.soul_path

        self.soul_path.parent.mkdir(parents=True, exist_ok=True)

        now = datetime.utcnow().isoformat()
        content = self.SOUL_TEMPLATE.format(created=now, last_updated=now)

        self.soul_path.write_text(content)
        return self.soul_path

    def add_learning(self, learning: Learning) -> None:
        """Append a learning to SOUL.xml."""
        self.init_soul()

        try:
            tree = ET.parse(self.soul_path)
            root = tree.getroot()
        except Exception:
            self.init_soul()
            tree = ET.parse(self.soul_path)
            root = tree.getroot()

        # Determine which section
        if learning.type == "success":
            section = root.find("patterns")
        elif learning.type == "failure":
            section = root.find("rules")
        else:
            section = root.find("patterns")

        if section is None:
            return

        # Add entry
        entry = ET.SubElement(section, "entry")
        entry.set("type", learning.type)
        entry.set("date", learning.date)
        entry.set("confidence", str(learning.confidence))

        pattern_elem = ET.SubElement(entry, "pattern")
        pattern_elem.text = learning.pattern

        context_elem = ET.SubElement(entry, "context")
        context_elem.text = learning.context

        # Update last-modified
        metadata = root.find("metadata/last-updated")
        if metadata is not None:
            metadata.text = datetime.utcnow().isoformat()

        # Write back
        self._indent_xml(root)
        tree.write(self.soul_path, encoding="utf-8", xml_declaration=True)

    def add_cost_record(self, task: str, model: str, cost: float, tokens: int) -> None:
        """Add cost record to SOUL.xml."""
        self.init_soul()

        try:
            tree = ET.parse(self.soul_path)
            root = tree.getroot()
        except Exception:
            return

        cost_section = root.find("cost-history")
        if cost_section is None:
            return

        record = ET.SubElement(cost_section, "record")
        record.set("date", datetime.utcnow().isoformat())
        record.set("task", task)
        record.set("model", model)
        record.set("cost", f"{cost:.6f}")
        record.set("tokens", str(tokens))

        tree.write(self.soul_path, encoding="utf-8", xml_declaration=True)

    def get_learnings(self, learning_type: str = None) -> list[Learning]:
        """Get all learnings from SOUL.xml."""
        if not self.soul_path.exists():
            return []

        try:
            tree = ET.parse(self.soul_path)
            root = tree.getroot()
        except Exception:
            return []

        learnings = []

        # Get patterns
        for entry in root.findall(".//patterns/entry"):
            if learning_type and entry.get("type") != learning_type:
                continue

            pattern = entry.findtext("pattern", "")
            context = entry.findtext("context", "")
            learn_type = entry.get("type", "")
            confidence = float(entry.get("confidence", "0.5"))
            date = entry.get("date", "")

            learnings.append(
                Learning(
                    pattern=pattern,
                    type=learn_type,
                    confidence=confidence,
                    context=context,
                    date=date,
                )
            )

        # Get rules
        for entry in root.findall(".//rules/entry"):
            if learning_type and entry.get("type") != learning_type:
                continue

            pattern = entry.findtext("pattern", "")
            context = entry.findtext("context", "")
            learn_type = entry.get("type", "")
            confidence = float(entry.get("confidence", "0.5"))
            date = entry.get("date", "")

            learnings.append(
                Learning(
                    pattern=pattern,
                    type=learn_type,
                    confidence=confidence,
                    context=context,
                    date=date,
                )
            )

        return learnings

    def get_cost_summary(self) -> dict:
        """Get cost statistics from SOUL.xml."""
        if not self.soul_path.exists():
            return {"total_cost": 0.0, "record_count": 0}

        try:
            tree = ET.parse(self.soul_path)
            root = tree.getroot()
        except Exception:
            return {"total_cost": 0.0, "record_count": 0}

        total_cost = 0.0
        record_count = 0

        for record in root.findall(".//cost-history/record"):
            try:
                cost = float(record.get("cost", "0"))
                total_cost += cost
                record_count += 1
            except ValueError:
                pass

        return {"total_cost": total_cost, "record_count": record_count}

    @staticmethod
    def _indent_xml(elem, level=0):
        """Pretty-print XML."""
        indent = "\n" + level * "  "
        if len(elem):
            if not elem.text or not elem.text.strip():
                elem.text = indent + "  "
            if not elem.tail or not elem.tail.strip():
                elem.tail = indent
            for child in elem:
                SoulXML._indent_xml(child, level + 1)
            if not child.tail or not child.tail.strip():
                child.tail = indent
        else:
            if level and (not elem.tail or not elem.tail.strip()):
                elem.tail = indent


# Quick test
if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        soul = SoulXML(soul_path=tmpdir / "SOUL.xml")

        # Initialize
        soul.init_soul()
        print(f"✓ SOUL.xml created")

        # Add learnings
        soul.add_learning(
            Learning(
                pattern="Always check user permissions before API call",
                type="success",
                confidence=0.95,
                context="Security checks in auth module",
                date=datetime.utcnow().isoformat(),
            )
        )

        soul.add_learning(
            Learning(
                pattern="Avoid modifying global state without synchronization",
                type="failure",
                confidence=0.9,
                context="Race condition in concurrent handlers",
                date=datetime.utcnow().isoformat(),
            )
        )

        # Add cost
        soul.add_cost_record(
            task="Fix auth bug",
            model="deepseek-chat",
            cost=0.0024,
            tokens=1000,
        )

        # Retrieve
        learnings = soul.get_learnings()
        print(f"✓ {len(learnings)} learnings stored")

        cost_summary = soul.get_cost_summary()
        print(f"✓ Cost summary: ${cost_summary['total_cost']:.6f} ({cost_summary['record_count']} records)")
