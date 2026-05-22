"""
STRUCT.xml builder — Generates hierarchical repository map

Steps 1.4-1.5: Build STRUCT.xml generator + file watcher
"""

import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Optional
from dataclasses import dataclass
from tree_sitter import Language
from scaffold.agent.cartographer import TreeSitterCartographer
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


class StructBuilder:
    """Build and maintain STRUCT.xml repository map."""
    
    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        self.cartographer = self._init_cartographer()
        self.file_hashes: Dict[str, str] = {}
        self.struct_root = ET.Element('repository')
        self.struct_root.set('path', repo_path)
    
    def _init_cartographer(self) -> TreeSitterCartographer:
        """Initialize cartographer with available languages."""
        langs = {}
        try:
            langs['python'] = Language('py_tree_sitter', 'python')
        except: pass
        try:
            langs['cpp'] = Language('py_tree_sitter', 'cpp')
        except: pass
        try:
            langs['rust'] = Language('py_tree_sitter', 'rust')
        except: pass
        return TreeSitterCartographer(langs)
    
    def build(self) -> str:
        """Build STRUCT.xml for repository."""
        self._walk_repo(self.repo_path, self.struct_root)
        return ET.tostring(self.struct_root, encoding='unicode')
    
    def _walk_repo(self, path: str, parent_elem):
        """Recursively walk repository and build XML."""
        for entry in sorted(os.listdir(path)):
            if entry.startswith('.') or entry in ['__pycache__', 'node_modules', '.git', '.awos']:
                continue
            
            full_path = os.path.join(path, entry)
            
            if os.path.isdir(full_path):
                folder = ET.SubElement(parent_elem, 'folder')
                folder.set('name', entry)
                self._walk_repo(full_path, folder)
            elif os.path.isfile(full_path):
                self._process_file(full_path, parent_elem)
    
    def _process_file(self, file_path: str, parent_elem):
        """Process single file and extract symbols."""
        ext = Path(file_path).suffix.lstrip('.')
        lang_map = {'py': 'python', 'cpp': 'cpp', 'cc': 'cpp', 'rs': 'rust'}
        lang = lang_map.get(ext)
        
        if not lang:
            return
        
        try:
            content_hash = self.cartographer.get_content_hash(file_path)
            self.file_hashes[file_path] = content_hash
            
            rel_path = os.path.relpath(file_path, self.repo_path)
            tree, symbols = self.cartographer.parse_file(file_path, lang)
            
            file_elem = ET.SubElement(parent_elem, 'file')
            file_elem.set('path', rel_path)
            file_elem.set('hash', content_hash)
            
            for sym in symbols:
                sym_elem = ET.SubElement(file_elem, 'symbol')
                sym_elem.set('id', sym.id)
                sym_elem.set('type', sym.type)
                sym_elem.set('name', sym.name)
                sym_elem.set('line', str(sym.line))
        except Exception as e:
            pass  # Skip files that can't be parsed
    
    def to_file(self, output_path: str) -> None:
        """Write STRUCT.xml to file."""
        tree = ET.ElementTree(self.struct_root)
        tree.write(output_path, encoding='utf-8', xml_declaration=True)
    
    def detect_changes(self, file_path: str) -> bool:
        """Check if file hash changed."""
        try:
            new_hash = self.cartographer.get_content_hash(file_path)
            old_hash = self.file_hashes.get(file_path)
            return new_hash != old_hash
        except:
            return False


class FileWatcher(FileSystemEventHandler):
    """Watch for file changes and trigger STRUCT.xml updates."""
    
    def __init__(self, builder: StructBuilder, callback=None):
        self.builder = builder
        self.callback = callback
    
    def on_modified(self, event):
        if not event.is_directory and self.builder.detect_changes(event.src_path):
            if self.callback:
                self.callback(event.src_path)
