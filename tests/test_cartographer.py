"""
Tests for TreeSitterCartographer

Step 1.3: Implement TreeSitterCartographer class
Exit condition: pytest tests/test_cartographer.py passes; all fixtures extracted correctly
"""

import pytest
import tempfile
import os
from pathlib import Path

from tree_sitter import Language, Parser
from scaffold.agent.cartographer import TreeSitterCartographer, Symbol


@pytest.fixture
def cartographer():
    """Create cartographer with Python language support."""
    try:
        import tree_sitter_python as tspython
        python_lang = Language(tspython.language())
    except Exception:
        pytest.skip("Tree-Sitter Python bindings not available")

    return TreeSitterCartographer({'python': python_lang})


@pytest.fixture
def temp_python_file():
    """Create a temporary Python file with test code."""
    code = '''
def my_function(x: int) -> str:
    """Test function."""
    return str(x)

class MyClass:
    """Test class."""
    
    def __init__(self):
        self.value = 0
    
    def method(self):
        """Test method."""
        return self.value
'''
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write(code)
        f.flush()
        yield f.name
    
    os.unlink(f.name)


def test_parse_python_file(cartographer, temp_python_file):
    """Test parsing a Python file and extracting symbols."""
    tree, symbols = cartographer.parse_file(temp_python_file, 'python')
    
    assert tree is not None, "AST tree should be returned"
    assert len(symbols) > 0, "Should extract at least one symbol"


def test_extract_function(cartographer, temp_python_file):
    """Test extraction of function symbol."""
    tree, symbols = cartographer.parse_file(temp_python_file, 'python')
    
    functions = [s for s in symbols if s.type == 'function']
    assert len(functions) >= 1, "Should extract at least one function"
    
    func = next((s for s in functions if s.name == 'my_function'), None)
    assert func is not None, "Should extract my_function"
    assert func.line > 0, "Function should have line number"


def test_extract_class(cartographer, temp_python_file):
    """Test extraction of class symbol."""
    tree, symbols = cartographer.parse_file(temp_python_file, 'python')
    
    classes = [s for s in symbols if s.type == 'class']
    assert len(classes) >= 1, "Should extract at least one class"
    
    cls = next((s for s in classes if s.name == 'MyClass'), None)
    assert cls is not None, "Should extract MyClass"


def test_symbol_properties(cartographer, temp_python_file):
    """Test that extracted symbols have required properties."""
    tree, symbols = cartographer.parse_file(temp_python_file, 'python')
    
    assert len(symbols) > 0
    sym = symbols[0]
    
    assert sym.id is not None, "Symbol should have ID"
    assert sym.type is not None, "Symbol should have type"
    assert sym.name is not None, "Symbol should have name"
    assert sym.line > 0, "Symbol should have line number"
    assert sym.language == 'python', "Symbol should have language"
    assert sym.file_path == temp_python_file, "Symbol should have file path"


def test_content_hash(cartographer, temp_python_file):
    """Test content hashing."""
    hash1 = cartographer.get_content_hash(temp_python_file)
    hash2 = cartographer.get_content_hash(temp_python_file)
    
    assert hash1 == hash2, "Hash should be stable"
    assert len(hash1) == 16, "XXH3-64 hex should be 16 chars"
    assert all(c in '0123456789abcdef' for c in hash1), "Hash should be valid hex"


def test_unsupported_language(cartographer, temp_python_file):
    """Test error handling for unsupported language."""
    with pytest.raises(ValueError):
        cartographer.parse_file(temp_python_file, 'unsupported_lang')


def test_nonexistent_file(cartographer):
    """Test error handling for missing file."""
    with pytest.raises(FileNotFoundError):
        cartographer.parse_file('/nonexistent/file.py', 'python')


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
