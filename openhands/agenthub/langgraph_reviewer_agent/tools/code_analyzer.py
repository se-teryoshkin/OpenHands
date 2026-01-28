"""Code analysis tools using Python AST."""

import ast
import json
from pathlib import Path
from typing import Any

from langchain_core.tools import tool


class SignatureExtractor(ast.NodeVisitor):
    """Extract method signatures from Python AST."""

    def __init__(self):
        self.classes: dict[str, dict] = {}
        self.functions: list[dict] = []
        self.current_class: str | None = None

    def visit_ClassDef(self, node: ast.ClassDef):
        self.current_class = node.name
        self.classes[node.name] = {
            "name": node.name,
            "methods": [],
            "bases": [self._get_name(base) for base in node.bases],
            "fields": [],
            "line": node.lineno,
        }
        self.generic_visit(node)
        self.current_class = None

    def visit_FunctionDef(self, node: ast.FunctionDef):
        sig = self._extract_signature(node)
        if self.current_class:
            self.classes[self.current_class]["methods"].append(sig)
        else:
            self.functions.append(sig)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_AnnAssign(self, node: ast.AnnAssign):
        """Extract class field annotations."""
        if self.current_class and isinstance(node.target, ast.Name):
            field_info = {
                "name": node.target.id,
                "type": ast.unparse(node.annotation) if node.annotation else None,
                "has_default": node.value is not None,
                "line": node.lineno,
            }
            self.classes[self.current_class]["fields"].append(field_info)

    def _extract_signature(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict:
        params = []
        for arg in node.args.args:
            param = {
                "name": arg.arg,
                "type": ast.unparse(arg.annotation) if arg.annotation else None,
            }
            params.append(param)

        return {
            "name": node.name,
            "parameters": params,
            "return_type": ast.unparse(node.returns) if node.returns else None,
            "is_async": isinstance(node, ast.AsyncFunctionDef),
            "line": node.lineno,
            "decorators": [ast.unparse(d) for d in node.decorator_list],
        }

    def _get_name(self, node) -> str:
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return f"{self._get_name(node.value)}.{node.attr}"
        elif isinstance(node, ast.Subscript):
            return f"{self._get_name(node.value)}[...]"
        return ""


class FieldAccessExtractor(ast.NodeVisitor):
    """Extract field accesses from Python code."""

    def __init__(self):
        self.field_accesses: list[dict] = []
        self.current_function: str | None = None

    def visit_FunctionDef(self, node: ast.FunctionDef):
        old_func = self.current_function
        self.current_function = node.name
        self.generic_visit(node)
        self.current_function = old_func

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Attribute(self, node: ast.Attribute):
        access = {
            "attribute": node.attr,
            "line": node.lineno,
            "context": self.current_function,
        }

        # Try to get the variable name
        if isinstance(node.value, ast.Name):
            access["variable"] = node.value.id
        elif isinstance(node.value, ast.Attribute):
            access["chain"] = self._get_attribute_chain(node.value)

        self.field_accesses.append(access)
        self.generic_visit(node)

    def _get_attribute_chain(self, node: ast.Attribute) -> list[str]:
        chain = [node.attr]
        current = node.value
        while isinstance(current, ast.Attribute):
            chain.insert(0, current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            chain.insert(0, current.id)
        return chain


class TestInfoExtractor(ast.NodeVisitor):
    """Extract test information from test files."""

    def __init__(self):
        self.test_functions: list[dict] = []
        self.mocked_objects: list[str] = []
        self.assertions: list[dict] = []
        self.imports: list[str] = []
        self.current_function: str | None = None

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self.imports.append(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        module = node.module or ""
        for alias in node.names:
            self.imports.append(f"{module}.{alias.name}")

    def visit_FunctionDef(self, node: ast.FunctionDef):
        if node.name.startswith("test_"):
            self.current_function = node.name
            self.test_functions.append({
                "name": node.name,
                "line": node.lineno,
                "decorators": [ast.unparse(d) for d in node.decorator_list],
            })
            self.generic_visit(node)
            self.current_function = None

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call):
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        # Check for mock patterns
        if func_name in ("patch", "MagicMock", "Mock", "AsyncMock"):
            if node.args:
                try:
                    self.mocked_objects.append(ast.unparse(node.args[0]))
                except:
                    pass

        # Check for assertions
        if func_name.startswith("assert") or func_name == "assertEqual":
            self.assertions.append({
                "type": func_name,
                "line": node.lineno,
                "function": self.current_function,
            })

        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert):
        self.assertions.append({
            "type": "assert",
            "line": node.lineno,
            "function": self.current_function,
        })


@tool
def extract_signatures_tool(file_path: str) -> str:
    """Extract class and method signatures from a Python file.

    Args:
        file_path: Path to the Python file to analyze.

    Returns:
        JSON string containing extracted signatures including classes, methods,
        parameters, return types, and inheritance information.
    """
    try:
        path = Path(file_path)
        if not path.exists():
            return json.dumps({"error": f"File not found: {file_path}"})

        code = path.read_text(encoding='utf-8')
        tree = ast.parse(code)

        extractor = SignatureExtractor()
        extractor.visit(tree)

        result = {
            "file": file_path,
            "classes": list(extractor.classes.values()),
            "functions": extractor.functions,
        }

        return json.dumps(result, indent=2)
    except SyntaxError as e:
        return json.dumps({"error": f"Syntax error in file: {e}"})
    except Exception as e:
        return json.dumps({"error": f"Failed to parse file: {e}"})


@tool
def extract_field_accesses_tool(file_path: str) -> str:
    """Extract all field/attribute accesses from a Python file.

    Useful for detecting access to potentially non-existent fields.

    Args:
        file_path: Path to the Python file to analyze.

    Returns:
        JSON string containing all attribute accesses found in the code,
        including the variable name, attribute, and line number.
    """
    try:
        path = Path(file_path)
        if not path.exists():
            return json.dumps({"error": f"File not found: {file_path}"})

        code = path.read_text(encoding='utf-8')
        tree = ast.parse(code)

        extractor = FieldAccessExtractor()
        extractor.visit(tree)

        result = {
            "file": file_path,
            "field_accesses": extractor.field_accesses,
        }

        return json.dumps(result, indent=2)
    except SyntaxError as e:
        return json.dumps({"error": f"Syntax error in file: {e}"})
    except Exception as e:
        return json.dumps({"error": f"Failed to parse file: {e}"})


@tool
def extract_test_info_tool(test_file_path: str) -> str:
    """Extract test information from a test file.

    Extracts test functions, mocked objects, assertions, and imports.

    Args:
        test_file_path: Path to the test file to analyze.

    Returns:
        JSON string containing test functions, mocked objects, assertions count,
        and imports found in the test file.
    """
    try:
        path = Path(test_file_path)
        if not path.exists():
            return json.dumps({"error": f"File not found: {test_file_path}"})

        code = path.read_text(encoding='utf-8')
        tree = ast.parse(code)

        extractor = TestInfoExtractor()
        extractor.visit(tree)

        result = {
            "file": test_file_path,
            "test_functions": extractor.test_functions,
            "test_count": len(extractor.test_functions),
            "mocked_objects": list(set(extractor.mocked_objects)),
            "assertions": extractor.assertions,
            "assertion_count": len(extractor.assertions),
            "imports": extractor.imports,
            "uses_mocking": bool(extractor.mocked_objects),
        }

        return json.dumps(result, indent=2)
    except SyntaxError as e:
        return json.dumps({"error": f"Syntax error in file: {e}"})
    except Exception as e:
        return json.dumps({"error": f"Failed to parse file: {e}"})
