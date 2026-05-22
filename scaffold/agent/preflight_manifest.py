"""
PreflightManifest: Generate XML manifest of what agent will do.
Includes task description, complexity score, model tier, estimated cost, files needed.
"""

import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from .model_router import ModelRouter
except ImportError:
    from model_router import ModelRouter


class PreflightManifest:
    """Generate preflight manifest as XML."""

    def __init__(self, awos_dir: Path = None):
        self.awos_dir = awos_dir or Path(".awos")
        self.router = ModelRouter()

    def generate(
        self,
        task_description: str,
        complexity_score: int,
        repo_path: str,
        files_involved: list[str] = None,
    ) -> Path:
        """
        Generate preflight manifest.
        Returns path to generated XML file.
        """
        if files_involved is None:
            files_involved = []

        config = self.router.route(complexity_score)
        tier_name = self.router.get_tier_name(complexity_score)

        # Estimate cost (rough guess: 2000 input tokens + 1000 output tokens)
        estimated_cost = (2000 + 1000) / 1_000_000 * config.cost_per_mtok

        # Build XML
        root = ET.Element("preflight-manifest")
        root.set("timestamp", datetime.utcnow().isoformat())

        # Task
        task_elem = ET.SubElement(root, "task")
        task_elem.text = task_description

        # Complexity
        complexity_elem = ET.SubElement(root, "complexity")
        complexity_elem.set("score", str(complexity_score))
        complexity_elem.set("interpretation", "trivial" if complexity_score <= 2 else
                            "simple" if complexity_score <= 4 else
                            "moderate" if complexity_score <= 6 else
                            "complex" if complexity_score <= 8 else
                            "expert-required")

        # Model
        model_elem = ET.SubElement(root, "model-tier")
        model_elem.set("tier-name", tier_name)
        model_elem.set("provider", config.provider)
        model_elem.set("model", config.model)
        model_elem.set("max-tokens", str(config.max_tokens))
        model_elem.set("temperature", str(config.temperature))

        # Cost estimate
        cost_elem = ET.SubElement(root, "cost-estimate")
        cost_elem.set("usd", f"{estimated_cost:.6f}")
        cost_elem.set("input-tokens", "2000")
        cost_elem.set("output-tokens", "1000")

        # Repository
        repo_elem = ET.SubElement(root, "repository")
        repo_elem.set("path", repo_path)

        # Files involved
        files_elem = ET.SubElement(root, "files-involved")
        for file_path in files_involved:
            file_elem = ET.SubElement(files_elem, "file")
            file_elem.text = file_path

        # Consent status
        consent_elem = ET.SubElement(root, "consent-status")
        consent_elem.text = "pending"

        # Format XML
        self._indent_xml(root)

        # Write to file
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        manifest_path = self.awos_dir / f"preflight_{timestamp}.xml"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)

        tree = ET.ElementTree(root)
        tree.write(manifest_path, encoding="utf-8", xml_declaration=True)

        return manifest_path

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
                PreflightManifest._indent_xml(child, level + 1)
            if not child.tail or not child.tail.strip():
                child.tail = indent
        else:
            if level and (not elem.tail or not elem.tail.strip()):
                elem.tail = indent

    def read_manifest(self, manifest_path: Path) -> dict:
        """Read and parse manifest XML."""
        tree = ET.parse(manifest_path)
        root = tree.getroot()

        return {
            "timestamp": root.get("timestamp"),
            "task": root.findtext("task"),
            "complexity_score": int(root.find("complexity").get("score")),
            "model_tier": root.find("model-tier").get("tier-name"),
            "model": root.find("model-tier").get("model"),
            "cost_estimate": float(root.find("cost-estimate").get("usd")),
            "consent_status": root.findtext("consent-status"),
            "repository": root.find("repository").get("path"),
            "files_involved": [
                f.text for f in root.findall("files-involved/file")
            ],
        }


# Quick test
if __name__ == "__main__":
    manifest = PreflightManifest()
    path = manifest.generate(
        task_description="Refactor authentication module",
        complexity_score=7,
        repo_path="/home/user/myproject",
        files_involved=["src/auth.py", "src/session.py"],
    )
    print(f"Manifest written to: {path}")
    print(f"\nManifest content:")
    data = manifest.read_manifest(path)
    for key, value in data.items():
        print(f"  {key}: {value}")
