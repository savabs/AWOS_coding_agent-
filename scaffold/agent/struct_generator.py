"""
STRUCT.xml Generator — Symbol-Only Header Injection

Problem: Agents hallucinate imports. They can't see function signatures.
Solution: Use tree-sitter to extract ONLY function signatures (no bodies).
Store in STRUCT.xml. This way:
  - Agent can see what functions exist
  - Agent can see their signatures
  - Agent doesn't get confused with full source (5000 tokens saved!)
"""

import re
import json
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass
import xml.etree.ElementTree as ET


@dataclass
class Symbol:
    """A code symbol (function, class, method)."""
    name: str
    type: str  # "function", "class", "method"
    signature: str
    file_path: str
    line_no: int
    docstring: Optional[str] = None


class StructGenerator:
    """Generate STRUCT.xml by extracting symbols from Python files."""
    
    def __init__(self, project_root: Path, struct_file: Path = None):
        self.project_root = Path(project_root)
        self.struct_file = Path(struct_file) if struct_file else self.project_root / ".awos" / "STRUCT.xml"
        self.symbols: Dict[str, List[Symbol]] = {}
    
    def extract_python_symbols(self, file_path: Path) -> List[Symbol]:
        """
        Extract function/class signatures from a Python file.
        Uses regex (no tree-sitter dependency for MVP).
        """
        if not file_path.exists() or not file_path.suffix == ".py":
            return []
        
        content = file_path.read_text()
        symbols = []
        
        # Extract classes
        class_pattern = r"^class\s+(\w+)(?:\((.*?)\))?:"
        for match in re.finditer(class_pattern, content, re.MULTILINE):
            class_name = match.group(1)
            bases = match.group(2) or ""
            symbols.append(Symbol(
                name=class_name,
                type="class",
                signature=f"class {class_name}({bases})",
                file_path=str(file_path.relative_to(self.project_root)),
                line_no=content[:match.start()].count("\n") + 1,
            ))
        
        # Extract functions
        func_pattern = r"^def\s+(\w+)\((.*?)\)(?:\s*->\s*(.+?))?:"
        for match in re.finditer(func_pattern, content, re.MULTILINE):
            func_name = match.group(1)
            args = match.group(2)
            return_type = match.group(3) or ""
            symbols.append(Symbol(
                name=func_name,
                type="function",
                signature=f"def {func_name}({args}) -> {return_type}",
                file_path=str(file_path.relative_to(self.project_root)),
                line_no=content[:match.start()].count("\n") + 1,
            ))
        
        return symbols
    
    def scan_project(self, pattern: str = "scaffold/agent/**/*.py") -> int:
        """
        Scan project for all Python files and extract symbols.
        
        Returns:
            Total symbols found
        """
        total = 0
        
        for file_path in self.project_root.glob(pattern):
            if file_path.is_file() and file_path.suffix == ".py":
                symbols = self.extract_python_symbols(file_path)
                if symbols:
                    key = str(file_path.relative_to(self.project_root))
                    self.symbols[key] = symbols
                    total += len(symbols)
                    print(f"  {key}: {len(symbols)} symbols")
        
        return total
    
    def generate_struct_xml(self) -> Path:
        """Generate STRUCT.xml from extracted symbols."""
        self.struct_file.parent.mkdir(parents=True, exist_ok=True)
        
        root = ET.Element("struct")
        root.set("generated", __import__('datetime').datetime.now().isoformat())
        
        for file_path, symbols in sorted(self.symbols.items()):
            file_elem = ET.SubElement(root, "file")
            file_elem.set("path", file_path)
            
            for symbol in symbols:
                sym_elem = ET.SubElement(file_elem, symbol.type)
                sym_elem.set("name", symbol.name)
                sym_elem.set("line", str(symbol.line_no))
                sym_elem.text = symbol.signature
                
                if symbol.docstring:
                    doc_elem = ET.SubElement(sym_elem, "doc")
                    doc_elem.text = symbol.docstring
        
        tree = ET.ElementTree(root)
        tree.write(self.struct_file, encoding="utf-8", xml_declaration=True)
        print(f"\n✓ Generated {self.struct_file}")
        
        return self.struct_file
    
    def get_symbols_as_prompt(self) -> str:
        """Return STRUCT.xml as a prompt block."""
        lines = ["=== SYMBOL INDEX (STRUCT.xml) ===", ""]
        
        for file_path, symbols in sorted(self.symbols.items()):
            lines.append(f"{file_path}:")
            for sym in symbols:
                lines.append(f"  {sym.type:10} {sym.signature:50} (line {sym.line_no})")
            lines.append("")
        
        lines.append("=== END SYMBOLS ===")
        return "\n".join(lines)
    
    def print_summary(self):
        """Print summary of extracted symbols."""
        total_symbols = sum(len(syms) for syms in self.symbols.values())
        
        print("\n" + "=" * 70)
        print("STRUCT.xml SUMMARY")
        print("=" * 70)
        print(f"Files: {len(self.symbols)}")
        print(f"Total symbols: {total_symbols}")
        print(f"\nTop files by symbol count:")
        
        sorted_files = sorted(
            self.symbols.items(),
            key=lambda x: len(x[1]),
            reverse=True
        )
        
        for file_path, symbols in sorted_files[:5]:
            print(f"  {file_path}: {len(symbols)} symbols")
        
        print("=" * 70 + "\n")


# Quick test
if __name__ == "__main__":
    import sys
    
    project_root = Path.cwd()
    gen = StructGenerator(project_root)
    
    print("Scanning project for symbols...")
    total = gen.scan_project()
    print(f"\n✓ Found {total} symbols")
    
    gen.generate_struct_xml()
    gen.print_summary()
