"""
HITLConsent: Human-in-the-loop approval gate.
Default: DENY (user must explicitly approve).
"""

from pathlib import Path
import xml.etree.ElementTree as ET
from datetime import datetime


class HITLConsent:
    """Manage human-in-the-loop consent."""

    def __init__(self, cost_log_path: Path = None):
        self.cost_log_path = cost_log_path or Path(".awos/COST_LOG.xml")

    def display_manifest(self, manifest_data: dict) -> None:
        """Pretty-print manifest for user review."""
        print("\n" + "=" * 60)
        print("PREFLIGHT MANIFEST — Please Review")
        print("=" * 60)
        print(f"\nTask: {manifest_data['task']}")
        print(f"Repository: {manifest_data['repository']}")
        print(f"\nComplexity Score: {manifest_data['complexity_score']}/10")
        print(f"Estimated Tier: {manifest_data['model_tier']}")
        print(f"Model: {manifest_data['model']}")
        print(f"Estimated Cost: ${manifest_data['cost_estimate']:.6f}")
        print(f"\nFiles Involved:")
        for f in manifest_data["files_involved"]:
            print(f"  - {f}")
        print("\n" + "=" * 60)

    def request_approval(self) -> bool:
        """
        Ask user for approval.
        Default: DENY (user must explicitly type 'y' or 'yes').
        """
        while True:
            user_input = input("\nProceed with this task? [y/n] (default: n): ").strip().lower()
            if user_input in ["y", "yes"]:
                return True
            elif user_input in ["n", "no", ""]:
                return False
            else:
                print("Please enter 'y' or 'n'.")

    def log_decision(
        self,
        task: str,
        model: str,
        complexity: int,
        cost_estimate: float,
        approved: bool,
    ) -> None:
        """
        Log approval/denial to COST_LOG.xml.
        """
        self.cost_log_path.parent.mkdir(parents=True, exist_ok=True)

        # Read or create cost log
        if self.cost_log_path.exists():
            tree = ET.parse(self.cost_log_path)
            root = tree.getroot()
        else:
            root = ET.Element("cost-log")
            tree = ET.ElementTree(root)

        # Add entry
        entry = ET.SubElement(root, "decision")
        entry.set("timestamp", datetime.utcnow().isoformat())
        entry.set("task", task)
        entry.set("model", model)
        entry.set("complexity", str(complexity))
        entry.set("cost-estimate", f"{cost_estimate:.6f}")
        entry.set("approved", "yes" if approved else "no")

        # Write back
        self._indent_xml(root)
        tree.write(self.cost_log_path, encoding="utf-8", xml_declaration=True)

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
                HITLConsent._indent_xml(child, level + 1)
            if not child.tail or not child.tail.strip():
                child.tail = indent
        else:
            if level and (not elem.tail or not elem.tail.strip()):
                elem.tail = indent


# Quick test
if __name__ == "__main__":
    consent = HITLConsent()

    # Simulate manifest
    manifest = {
        "task": "Refactor authentication",
        "repository": "/home/user/myproject",
        "complexity_score": 7,
        "model_tier": "Claude-Haiku (Balanced)",
        "model": "claude-3-5-haiku",
        "cost_estimate": 0.015,
        "files_involved": ["src/auth.py", "src/session.py"],
    }

    # Display and request
    consent.display_manifest(manifest)
    # Note: Uncomment to test interactive approval
    # approved = consent.request_approval()
    # consent.log_decision(
    #     task=manifest["task"],
    #     model=manifest["model"],
    #     complexity=manifest["complexity_score"],
    #     cost_estimate=manifest["cost_estimate"],
    #     approved=approved,
    # )

    print("\n[Test mode: skipped interactive approval]")
