"""Validation tools for code review."""

import ast
import json
import re
from pathlib import Path
from typing import Any

from langchain_core.tools import tool


def _parse_signature_from_spec(spec_text: str) -> list[dict]:
    """Parse method signatures from specification text."""
    signatures = []

    # Pattern for method definitions in Protocol/Interface style
    method_pattern = r'def\s+(\w+)\s*\((.*?)\)\s*(?:->\s*(.+?))?:'

    for match in re.finditer(method_pattern, spec_text, re.MULTILINE | re.DOTALL):
        method_name = match.group(1)
        params_str = match.group(2)
        return_type = match.group(3).strip() if match.group(3) else None

        # Parse parameters
        params = []
        if params_str.strip() and params_str.strip() != 'self':
            for param in params_str.split(','):
                param = param.strip()
                if param and param != 'self':
                    if ':' in param:
                        parts = param.split(':')
                        name = parts[0].strip()
                        type_hint = parts[1].split('=')[0].strip()
                    else:
                        name = param.split('=')[0].strip()
                        type_hint = None
                    params.append({"name": name, "type": type_hint})

        signatures.append({
            "name": method_name,
            "parameters": params,
            "return_type": return_type,
        })

    return signatures


def _extract_impl_signatures(impl_code: str, class_name: str) -> list[dict]:
    """Extract signatures from implementation code."""
    try:
        tree = ast.parse(impl_code)
    except SyntaxError:
        return []

    signatures = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    params = []
                    for arg in item.args.args:
                        if arg.arg != 'self':
                            params.append({
                                "name": arg.arg,
                                "type": ast.unparse(arg.annotation) if arg.annotation else None,
                            })

                    signatures.append({
                        "name": item.name,
                        "parameters": params,
                        "return_type": ast.unparse(item.returns) if item.returns else None,
                    })

    return signatures


@tool
def validate_signatures_tool(
    spec_interface: str,
    impl_file: str,
    impl_class: str
) -> str:
    """Validate that implementation signatures match specification.

    Checks if all methods defined in the specification interface are properly
    implemented with matching names, parameters, and return types.

    Args:
        spec_interface: The interface/protocol definition from specification
            (can be the actual code or description of expected methods).
        impl_file: Path to the implementation file.
        impl_class: Name of the implementation class to validate.

    Returns:
        JSON string with validation results including matches and mismatches.
    """
    try:
        path = Path(impl_file)
        if not path.exists():
            return json.dumps({"error": f"Implementation file not found: {impl_file}"})

        impl_code = path.read_text()

        # Parse specifications
        spec_sigs = _parse_signature_from_spec(spec_interface)
        impl_sigs = _extract_impl_signatures(impl_code, impl_class)

        results = {
            "valid": True,
            "issues": [],
            "spec_methods": [s["name"] for s in spec_sigs],
            "impl_methods": [s["name"] for s in impl_sigs],
        }

        # Check each spec method
        impl_by_name = {s["name"]: s for s in impl_sigs}

        for spec in spec_sigs:
            method_name = spec["name"]

            if method_name not in impl_by_name:
                results["valid"] = False
                results["issues"].append({
                    "type": "missing_method",
                    "method": method_name,
                    "message": f"Method '{method_name}' from spec not implemented",
                })
                continue

            impl = impl_by_name[method_name]

            # Check parameter count
            if len(spec["parameters"]) != len(impl["parameters"]):
                results["valid"] = False
                results["issues"].append({
                    "type": "param_count_mismatch",
                    "method": method_name,
                    "expected": len(spec["parameters"]),
                    "actual": len(impl["parameters"]),
                    "message": f"Method '{method_name}' has {len(impl['parameters'])} params, expected {len(spec['parameters'])}",
                })

            # Check parameter names (if both have the same count)
            if len(spec["parameters"]) == len(impl["parameters"]):
                for i, (spec_param, impl_param) in enumerate(zip(spec["parameters"], impl["parameters"])):
                    if spec_param["name"] != impl_param["name"]:
                        results["issues"].append({
                            "type": "param_name_mismatch",
                            "method": method_name,
                            "position": i,
                            "expected": spec_param["name"],
                            "actual": impl_param["name"],
                            "message": f"Parameter name mismatch in '{method_name}': expected '{spec_param['name']}', got '{impl_param['name']}'",
                        })

            # Check return type (if specified)
            if spec["return_type"] and impl["return_type"]:
                # Normalize types for comparison
                spec_return = spec["return_type"].replace(" ", "")
                impl_return = impl["return_type"].replace(" ", "")

                if spec_return != impl_return:
                    results["issues"].append({
                        "type": "return_type_mismatch",
                        "method": method_name,
                        "expected": spec["return_type"],
                        "actual": impl["return_type"],
                        "message": f"Return type mismatch in '{method_name}': expected '{spec['return_type']}', got '{impl['return_type']}'",
                    })

        return json.dumps(results, indent=2)

    except Exception as e:
        return json.dumps({"error": f"Validation failed: {e}"})


@tool
def validate_field_access_tool(
    source_file: str,
    class_definition: str,
    variable_pattern: str = ""
) -> str:
    """Validate that field accesses on objects are valid.

    Checks if accessed attributes actually exist in the class definition.

    Args:
        source_file: Path to the source file to check.
        class_definition: The class definition with its fields
            (e.g., Pydantic model or dataclass definition).
        variable_pattern: Optional pattern to match variable names
            (e.g., "vacancy" to check vacancy.* accesses).

    Returns:
        JSON string with validation results listing any invalid field accesses.
    """
    try:
        path = Path(source_file)
        if not path.exists():
            return json.dumps({"error": f"Source file not found: {source_file}"})

        source_code = path.read_text()

        # Extract valid fields from class definition
        valid_fields = set()

        # Pattern for field definitions
        field_patterns = [
            r'(\w+)\s*:\s*\w+',  # name: Type
            r'(\w+)\s*=\s*Field\(',  # name = Field(...)
            r'self\.(\w+)\s*=',  # self.name = ...
        ]

        for pattern in field_patterns:
            for match in re.finditer(pattern, class_definition):
                valid_fields.add(match.group(1))

        # Parse source file and find field accesses
        try:
            tree = ast.parse(source_code)
        except SyntaxError as e:
            return json.dumps({"error": f"Syntax error in source: {e}"})

        class FieldChecker(ast.NodeVisitor):
            def __init__(self):
                self.invalid_accesses = []
                self.all_accesses = []

            def visit_Attribute(self, node: ast.Attribute):
                if isinstance(node.value, ast.Name):
                    var_name = node.value.id
                    # Check if this matches our pattern
                    if not variable_pattern or variable_pattern in var_name.lower():
                        self.all_accesses.append({
                            "variable": var_name,
                            "field": node.attr,
                            "line": node.lineno,
                        })

                        if node.attr not in valid_fields and not node.attr.startswith('_'):
                            self.invalid_accesses.append({
                                "variable": var_name,
                                "field": node.attr,
                                "line": node.lineno,
                            })

                self.generic_visit(node)

        checker = FieldChecker()
        checker.visit(tree)

        results = {
            "valid": len(checker.invalid_accesses) == 0,
            "valid_fields": list(valid_fields),
            "invalid_accesses": checker.invalid_accesses,
            "total_accesses_checked": len(checker.all_accesses),
        }

        if checker.invalid_accesses:
            results["issues"] = [
                {
                    "type": "invalid_field_access",
                    "message": f"Access to non-existent field '{a['field']}' on '{a['variable']}' at line {a['line']}",
                    "field": a["field"],
                    "line": a["line"],
                }
                for a in checker.invalid_accesses
            ]

        return json.dumps(results, indent=2)

    except Exception as e:
        return json.dumps({"error": f"Validation failed: {e}"})


@tool
def validate_mapping_tool(
    source_model: str,
    target_model: str,
    mapping_code: str
) -> str:
    """Validate that data mapping between models is complete.

    Checks if all required fields in the target model are properly populated.

    Args:
        source_model: The source model definition (e.g., Pydantic model).
        target_model: The target model definition.
        mapping_code: The code that performs the mapping.

    Returns:
        JSON string with validation results showing missing or incorrect mappings.
    """
    try:
        # Extract fields from source model
        source_fields = set()
        for match in re.finditer(r'(\w+)\s*:\s*\w+', source_model):
            source_fields.add(match.group(1))

        # Extract required fields from target model (non-Optional without defaults)
        target_fields = {}
        for match in re.finditer(r'(\w+)\s*:\s*(\S+)', target_model):
            field_name = match.group(1)
            field_type = match.group(2)
            is_optional = 'Optional' in field_type or 'None' in field_type
            target_fields[field_name] = {
                "type": field_type,
                "optional": is_optional,
            }

        # Check which target fields are assigned in mapping code
        assigned_fields = set()

        # Pattern for field assignments
        patterns = [
            r'(\w+)\s*=\s*\w+\.\w+',  # field = source.field
            r'"(\w+)"\s*:\s*',  # "field": in dict
            r"'(\w+)'\s*:\s*",  # 'field': in dict
            r'(\w+)\s*=\s*[^,\n]+',  # field = something
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, mapping_code):
                assigned_fields.add(match.group(1))

        # Find missing required fields
        missing_required = []
        for field_name, field_info in target_fields.items():
            if not field_info["optional"] and field_name not in assigned_fields:
                missing_required.append({
                    "field": field_name,
                    "type": field_info["type"],
                })

        results = {
            "valid": len(missing_required) == 0,
            "source_fields": list(source_fields),
            "target_fields": list(target_fields.keys()),
            "assigned_fields": list(assigned_fields),
            "missing_required": missing_required,
        }

        if missing_required:
            results["issues"] = [
                {
                    "type": "missing_required_field",
                    "message": f"Required field '{f['field']}' ({f['type']}) is not mapped",
                    "field": f["field"],
                }
                for f in missing_required
            ]

        return json.dumps(results, indent=2)

    except Exception as e:
        return json.dumps({"error": f"Validation failed: {e}"})


@tool
def validate_test_quality_tool(
    test_file: str,
    interface_methods: list[str],
    allow_mocking: bool = True
) -> str:
    """Validate test file quality and coverage.

    Checks if tests cover the required interface methods and follow best practices.

    Args:
        test_file: Path to the test file.
        interface_methods: List of method names that must be tested.
        allow_mocking: Whether mocking is allowed (default True).
            Set to False if spec requires testing real implementations.

    Returns:
        JSON string with test quality analysis including coverage and issues.
    """
    try:
        path = Path(test_file)
        if not path.exists():
            return json.dumps({"error": f"Test file not found: {test_file}"})

        code = path.read_text()

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return json.dumps({"error": f"Syntax error in test file: {e}"})

        # Extract test information
        test_functions = []
        mocked_objects = []
        assertions = 0

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith('test_'):
                test_functions.append(node.name)

            if isinstance(node, ast.Call):
                func_name = ""
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr

                if func_name in ('patch', 'MagicMock', 'Mock', 'AsyncMock'):
                    if node.args:
                        try:
                            mocked_objects.append(ast.unparse(node.args[0]))
                        except:
                            pass

                if func_name.startswith('assert') or func_name in ('assertEqual', 'assertTrue', 'assertFalse'):
                    assertions += 1

            if isinstance(node, ast.Assert):
                assertions += 1

        # Check coverage
        covered_methods = []
        uncovered_methods = []

        for method in interface_methods:
            # Check if any test mentions this method
            if any(method.lower() in test.lower() for test in test_functions):
                covered_methods.append(method)
            elif method.lower() in code.lower():
                covered_methods.append(method)
            else:
                uncovered_methods.append(method)

        issues = []

        # Check for excessive mocking
        if not allow_mocking and mocked_objects:
            issues.append({
                "type": "forbidden_mocking",
                "message": f"Mocking is not allowed but found: {mocked_objects}",
                "mocked": mocked_objects,
            })

        # Check for uncovered methods
        for method in uncovered_methods:
            issues.append({
                "type": "missing_test",
                "message": f"No test found for method '{method}'",
                "method": method,
            })

        # Check for low assertion density
        if test_functions and assertions / len(test_functions) < 1:
            issues.append({
                "type": "low_assertions",
                "message": f"Low assertion density: {assertions} assertions for {len(test_functions)} tests",
            })

        results = {
            "valid": len(issues) == 0,
            "test_count": len(test_functions),
            "test_functions": test_functions,
            "assertion_count": assertions,
            "mocked_objects": list(set(mocked_objects)),
            "uses_mocking": bool(mocked_objects),
            "covered_methods": covered_methods,
            "uncovered_methods": uncovered_methods,
            "coverage_ratio": len(covered_methods) / len(interface_methods) if interface_methods else 1.0,
            "issues": issues,
        }

        return json.dumps(results, indent=2)

    except Exception as e:
        return json.dumps({"error": f"Validation failed: {e}"})


@tool
def validate_structure_tool(
    project_root: str,
    module_name: str,
    expected_files: list[str] | None = None
) -> str:
    """Validate project file organization.

    Checks if files are organized according to conventions.

    Args:
        project_root: Path to project root directory.
        module_name: Name of the module being validated.
        expected_files: Optional list of expected file patterns
            (e.g., ["src/{module}/service.py", "tests/test_{module}.py"]).

    Returns:
        JSON string with structure validation results.
    """
    try:
        root = Path(project_root)
        if not root.exists():
            return json.dumps({"error": f"Project root not found: {project_root}"})

        # Default expected structure
        if expected_files is None:
            expected_files = [
                f"src/{module_name}/__init__.py",
                f"src/{module_name}/service.py",
                f"tests/test_{module_name}.py",
                "pyproject.toml",
            ]

        # Check each expected file
        found_files = []
        missing_files = []

        for pattern in expected_files:
            # Replace {module} placeholder
            path_str = pattern.replace("{module}", module_name)
            full_path = root / path_str

            if full_path.exists():
                found_files.append(path_str)
            else:
                missing_files.append(path_str)

        # Find all Python files
        all_python_files = list(root.rglob("*.py"))
        python_files = [str(f.relative_to(root)) for f in all_python_files if "__pycache__" not in str(f)]

        issues = []

        for missing in missing_files:
            issues.append({
                "type": "missing_file",
                "message": f"Expected file not found: {missing}",
                "file": missing,
            })

        # Check for files in wrong locations (module files outside module dir)
        module_pattern = re.compile(rf'{module_name}', re.IGNORECASE)
        for py_file in python_files:
            if module_pattern.search(py_file):
                if not py_file.startswith(f"src/{module_name}") and not py_file.startswith("tests/"):
                    if module_name.lower() in py_file.lower():
                        issues.append({
                            "type": "misplaced_file",
                            "message": f"Module-related file in unexpected location: {py_file}",
                            "file": py_file,
                        })

        results = {
            "valid": len(issues) == 0,
            "found_files": found_files,
            "missing_files": missing_files,
            "all_python_files": python_files,
            "issues": issues,
        }

        return json.dumps(results, indent=2)

    except Exception as e:
        return json.dumps({"error": f"Validation failed: {e}"})
