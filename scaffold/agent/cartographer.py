"""
TreeSitterCartographer — Core parser for extracting AST symbols from source files

Layer 1 of AWOS v2 Blueprint: Universal Cartographer

This module provides language-agnostic AST parsing and symbol extraction using Tree-Sitter.
It identifies functions, classes, methods, and other symbols across multiple programming languages.
"""

import os
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
import warnings
import xxhash
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from tree_sitter import Language, Parser


@dataclass
class Symbol:
    """Represents a code symbol (function, class, method, etc.)"""
    id: str                    # Unique identifier (e.g., "f1", "c1_m2")
    type: str                  # Symbol type: function, class, method, variable, etc.
    name: str                  # Symbol name
    signature: str             # Full signature (e.g., "def render(self, x: int) -> None")
    line: int                  # Line number where symbol starts
    end_line: int              # Line number where symbol ends
    language: str              # Language (python, cpp, rust, etc.)
    file_path: str             # Full path to file


class TreeSitterCartographer:
    """
    AST parser and symbol extractor using Tree-Sitter.
    
    Supports multiple languages via Tree-Sitter language modules.
    Each language parser must be provided at initialization.
    """
    
    LANGUAGE_ALIASES = {
        'py': 'python',
        'cpp': 'c_plus_plus',
        'cc': 'c_plus_plus',
        'cxx': 'c_plus_plus',
        'c++': 'c_plus_plus',
        'rs': 'rust',
        'go': 'go',
        'js': 'javascript',
        'ts': 'typescript',
        'java': 'java',
    }
    
    def __init__(self, language_modules: Dict[str, Language]):
        """
        Initialize cartographer with language modules.
        
        Args:
            language_modules: Dict mapping language names to Language objects.
                             Use Language(tree_sitter_python.language()) for v0.22+.
        """
        self.language_modules = language_modules
        self.symbol_counter = 0  # For generating unique IDs
        # Parser is created per language in parse_file (v0.22+ API)
    
    def parse_file(self, file_path: str, language: str) -> Tuple[Optional[object], List[Symbol]]:
        """
        Parse a source file and extract symbols.
        
        Args:
            file_path: Path to source file
            language: Programming language (python, cpp, rust, etc.)
        
        Returns:
            Tuple of (AST tree, list of Symbol objects)
        """
        if language not in self.language_modules:
            raise ValueError(f"Unsupported language: {language}")
        
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        
        # Read file
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            source_code = f.read()
        
        # Create parser with language (v0.22+ API)
        lang = self.language_modules[language]
        parser = Parser(lang)
        
        # Parse
        tree = parser.parse(source_code.encode('utf-8'))
        
        # Extract symbols based on language
        symbols = self._extract_symbols(tree, source_code, file_path, language)
        
        return tree, symbols
    
    def _extract_symbols(self, tree: object, source_code: str, file_path: str, language: str) -> List[Symbol]:
        """
        Extract symbols from AST tree.
        
        Args:
            tree: Tree-Sitter AST tree
            source_code: Source code string
            file_path: Path to source file
            language: Language name
        
        Returns:
            List of Symbol objects
        """
        symbols = []
        
        if language == 'python':
            symbols = self._extract_python_symbols(tree, source_code, file_path)
        elif language == 'cpp':
            symbols = self._extract_cpp_symbols(tree, source_code, file_path)
        elif language == 'rust':
            symbols = self._extract_rust_symbols(tree, source_code, file_path)
        # Add more languages as needed
        
        return symbols
    
    def _extract_python_symbols(self, tree: object, source_code: str, file_path: str) -> List[Symbol]:
        """Extract symbols from Python AST."""
        symbols = []
        lines = source_code.split('\n')
        
        def traverse(node, depth=0):
            if node.type in ('function_definition', 'class_definition'):
                name = None
                for child in node.children:
                    if child.type == 'name':
                        name = child.text.decode('utf-8')
                        break
                
                if name:
                    self.symbol_counter += 1
                    symbol_id = f"f{self.symbol_counter}" if node.type == 'function_definition' else f"c{self.symbol_counter}"
                    
                    symbol = Symbol(
                        id=symbol_id,
                        type='function' if node.type == 'function_definition' else 'class',
                        name=name,
                        signature=self._get_signature(node, source_code),
                        line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        language='python',
                        file_path=file_path
                    )
                    symbols.append(symbol)
            
            for child in node.children:
                traverse(child, depth + 1)
        
        traverse(tree.root_node)
        return symbols
    
    def _extract_cpp_symbols(self, tree: object, source_code: str, file_path: str) -> List[Symbol]:
        """Extract symbols from C/C++ AST."""
        symbols = []
        
        def traverse(node):
            if node.type in ('function_definition', 'declaration'):
                # Simplified extraction — can be enhanced
                if node.type == 'function_definition':
                    self.symbol_counter += 1
                    symbol = Symbol(
                        id=f"f{self.symbol_counter}",
                        type='function',
                        name=self._get_cpp_name(node, source_code),
                        signature=source_code[node.start_byte:node.end_byte].split('\n')[0],
                        line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        language='cpp',
                        file_path=file_path
                    )
                    symbols.append(symbol)
            
            for child in node.children:
                traverse(child)
        
        traverse(tree.root_node)
        return symbols
    
    def _extract_rust_symbols(self, tree: object, source_code: str, file_path: str) -> List[Symbol]:
        """Extract symbols from Rust AST."""
        symbols = []
        
        def traverse(node):
            if node.type in ('function_item', 'struct_item', 'enum_item', 'impl_item'):
                name = None
                for child in node.children:
                    if child.type == 'identifier':
                        name = child.text.decode('utf-8')
                        break
                
                if name:
                    self.symbol_counter += 1
                    symbol_type = {
                        'function_item': 'function',
                        'struct_item': 'struct',
                        'enum_item': 'enum',
                        'impl_item': 'impl',
                    }.get(node.type, 'unknown')
                    
                    symbol = Symbol(
                        id=f"f{self.symbol_counter}",
                        type=symbol_type,
                        name=name,
                        signature=self._get_signature(node, source_code),
                        line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        language='rust',
                        file_path=file_path
                    )
                    symbols.append(symbol)
            
            for child in node.children:
                traverse(child)
        
        traverse(tree.root_node)
        return symbols
    
    def _get_signature(self, node: object, source_code: str) -> str:
        """Extract function/class signature from node."""
        try:
            sig = source_code[node.start_byte:node.end_byte].split('\n')[0]
            return sig[:100]  # Limit to 100 chars
        except:
            return "unknown"
    
    def _get_cpp_name(self, node: object, source_code: str) -> str:
        """Extract function name from C/C++ node."""
        try:
            text = source_code[node.start_byte:node.end_byte]
            # Simplified: find identifier after return type
            parts = text.split()
            return parts[-1] if parts else "unknown"
        except:
            return "unknown"
    
    def get_content_hash(self, file_path: str) -> str:
        """
        Get XXH3 content hash of file.
        
        Args:
            file_path: Path to file
        
        Returns:
            Hex string of XXH3-64 hash
        """
        h = xxhash.xxh3_64()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                h.update(chunk)
        return h.hexdigest()
