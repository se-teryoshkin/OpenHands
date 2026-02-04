"""Validation tools for code review."""

import ast
import json
import re
from pathlib import Path
from typing import Any
import jedi

from langchain_core.tools import tool

# Try to import TOML parser (Python 3.11+ has tomllib built-in)
try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # type: ignore  # Fallback for older Python (optional dependency)
    except ImportError:
        tomllib = None  # No TOML support


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

        impl_code = path.read_text(encoding='utf-8')

        # Parse specifications
        spec_sigs = _parse_signature_from_spec(spec_interface)
        impl_sigs = _extract_impl_signatures(impl_code, impl_class)

        results = {
            "valid": True,
            "file": impl_file,  # Store file path for fallback extraction
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
            (e.g., "item" to check item.* accesses).

    Returns:
        JSON string with validation results listing any invalid field accesses.
    """
    try:
        path = Path(source_file)
        if not path.exists():
            return json.dumps({"error": f"Source file not found: {source_file}"})

        source_code = path.read_text(encoding='utf-8')

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

        code = path.read_text(encoding='utf-8')

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

        # Check for superficial hasattr-only tests
        hasattr_count = code.count("hasattr(")
        actual_method_calls = 0
        for method in interface_methods:
            # Check if the method is actually called (not just hasattr checked)
            # Look for patterns like: service.method_name( or .method_name(
            import re
            call_pattern = rf'\.{method}\s*\('
            if re.search(call_pattern, code):
                actual_method_calls += 1

        if hasattr_count > 0 and actual_method_calls == 0:
            issues.append({
                "type": "superficial_tests",
                "message": f"Tests only check method existence with hasattr() ({hasattr_count} times) "
                           f"but never actually call the methods. Tests should invoke methods and verify behavior.",
                "hasattr_count": hasattr_count,
                "actual_calls": actual_method_calls,
            })

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
            "file": test_file,  # Store file path for fallback extraction
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
def validate_model_field_mapping_tool(
    source_model_definition: str,
    target_model_definition: str,
    implementation_file: str,
    mapping_method_name: str = ""
) -> str:
    """Validate field-level mapping between source and target models.

    This tool performs a comprehensive check to ensure all required fields
    in the target model are properly mapped from the source model.
    Useful for detecting missing field mappings when transforming data
    between different model representations (e.g., API models to domain models).

    Args:
        source_model_definition: The source model definition (e.g., from AppFactory docs).
            Can be Pydantic model, dataclass, or simple class definition.
        target_model_definition: The target model definition from specification.
        implementation_file: Path to the file containing the mapping implementation.
        mapping_method_name: Optional name of the method that performs mapping.
            If provided, focuses analysis on that specific method.

    Returns:
        JSON string with detailed field mapping analysis including:
        - source_fields: All fields in source model
        - target_fields: All fields in target model (with required/optional info)
        - mapped_fields: Fields that appear to be mapped in implementation
        - unmapped_required: Required target fields not being mapped
        - type_mismatches: Fields with incompatible types
        - recommendations: Suggestions for fixing issues
    """
    try:
        path = Path(implementation_file)
        if not path.exists():
            return json.dumps({"error": f"Implementation file not found: {implementation_file}"})

        impl_code = path.read_text(encoding='utf-8')

        # Parse source model fields
        source_fields = _extract_model_fields(source_model_definition)

        # Parse target model fields
        target_fields = _extract_model_fields(target_model_definition)

        # Analyze the implementation for field mappings
        field_mappings = _analyze_field_mappings(impl_code, mapping_method_name)

        # Find target fields that are mapped in implementation
        mapped_target_fields = set()
        for field_name in target_fields.keys():
            # Check various patterns for field assignment
            patterns = [
                rf'{field_name}\s*=',  # field =
                rf'"{field_name}"\s*:',  # "field":
                rf"'{field_name}'\s*:",  # 'field':
                rf'{field_name}\s*:',  # field: (in constructor)
            ]
            for pattern in patterns:
                if re.search(pattern, impl_code, re.IGNORECASE):
                    mapped_target_fields.add(field_name)
                    break

        # Identify unmapped required fields
        unmapped_required = []
        unmapped_optional = []

        for field_name, field_info in target_fields.items():
            if field_name not in mapped_target_fields:
                field_data = {
                    "field": field_name,
                    "type": field_info["type"],
                    "possible_source": _find_similar_field(field_name, source_fields),
                }
                if field_info.get("required", False):
                    unmapped_required.append(field_data)
                else:
                    unmapped_optional.append(field_data)

        # Check for type mismatches in mapped fields
        type_mismatches = []
        for field_name in mapped_target_fields:
            if field_name in target_fields and field_name in source_fields:
                source_type = source_fields[field_name].get("type", "")
                target_type = target_fields[field_name].get("type", "")

                # Normalize types for comparison
                source_normalized = _normalize_type(source_type)
                target_normalized = _normalize_type(target_type)

                if source_normalized and target_normalized:
                    if not _types_compatible(source_normalized, target_normalized):
                        type_mismatches.append({
                            "field": field_name,
                            "source_type": source_type,
                            "target_type": target_type,
                            "message": f"Type mismatch for '{field_name}': source is '{source_type}', target expects '{target_type}'"
                        })

        # Build recommendations
        recommendations = []

        if unmapped_required:
            recommendations.append(
                f"Map the following required fields: {[f['field'] for f in unmapped_required]}"
            )

        for field_data in unmapped_required:
            if field_data["possible_source"]:
                recommendations.append(
                    f"Field '{field_data['field']}' might map from source field '{field_data['possible_source']}'"
                )

        if type_mismatches:
            recommendations.append(
                "Add type conversion for mismatched types to ensure compatibility"
            )

        # Build issues list
        issues = []

        for field_data in unmapped_required:
            issues.append({
                "type": "unmapped_required_field",
                "field": field_data["field"],
                "target_type": field_data["type"],
                "possible_source": field_data["possible_source"],
                "message": f"Required target field '{field_data['field']}' ({field_data['type']}) is not mapped from source",
                "severity": "error",
            })

        for mismatch in type_mismatches:
            issues.append({
                "type": "type_mismatch",
                "field": mismatch["field"],
                "source_type": mismatch["source_type"],
                "target_type": mismatch["target_type"],
                "message": mismatch["message"],
                "severity": "warning",
            })

        results = {
            "valid": len(unmapped_required) == 0 and len(type_mismatches) == 0,
            "source_fields": {k: v for k, v in source_fields.items()},
            "target_fields": {k: v for k, v in target_fields.items()},
            "mapped_fields": list(mapped_target_fields),
            "unmapped_required": unmapped_required,
            "unmapped_optional": unmapped_optional,
            "type_mismatches": type_mismatches,
            "coverage": {
                "total_target_fields": len(target_fields),
                "mapped_count": len(mapped_target_fields),
                "unmapped_required_count": len(unmapped_required),
                "mapping_percentage": round(len(mapped_target_fields) / len(target_fields) * 100, 1) if target_fields else 100,
            },
            "recommendations": recommendations,
            "issues": issues,
        }

        return json.dumps(results, indent=2)

    except Exception as e:
        return json.dumps({"error": f"Field mapping validation failed: {e}"})


def _extract_model_fields(model_definition: str) -> dict[str, dict]:
    """Extract field information from a model definition."""
    fields = {}

    # Pattern for Pydantic Field definitions
    # field_name: Type = Field(...)
    pydantic_pattern = r'(\w+)\s*:\s*([^=\n]+?)\s*=\s*Field\s*\('
    for match in re.finditer(pydantic_pattern, model_definition):
        field_name = match.group(1)
        field_type = match.group(2).strip()

        # Check if it has a default
        field_start = match.end()
        field_section = model_definition[match.start():match.start() + 500]
        has_default = 'default=' in field_section or 'default_factory=' in field_section
        is_optional = 'Optional' in field_type or field_type.endswith('| None')

        fields[field_name] = {
            "type": field_type,
            "required": not has_default and not is_optional and '...' in field_section,
            "optional": is_optional,
        }

    # Pattern for simple type annotations: field_name: Type
    simple_pattern = r'^\s*(\w+)\s*:\s*([^=\n]+?)(?:\s*=\s*(.+?))?$'
    for match in re.finditer(simple_pattern, model_definition, re.MULTILINE):
        field_name = match.group(1)
        if field_name not in fields and field_name not in ('self', 'class', 'def', 'return'):
            field_type = match.group(2).strip()
            default_value = match.group(3)

            is_optional = 'Optional' in field_type or field_type.endswith('| None')
            has_default = default_value is not None and default_value.strip() != ''

            fields[field_name] = {
                "type": field_type,
                "required": not has_default and not is_optional,
                "optional": is_optional,
            }

    # Pattern for dataclass fields: field_name: Type = field(...)
    dataclass_pattern = r'(\w+)\s*:\s*([^=\n]+?)\s*=\s*field\s*\('
    for match in re.finditer(dataclass_pattern, model_definition, re.IGNORECASE):
        field_name = match.group(1)
        if field_name not in fields:
            field_type = match.group(2).strip()
            is_optional = 'Optional' in field_type or field_type.endswith('| None')

            fields[field_name] = {
                "type": field_type,
                "required": not is_optional,
                "optional": is_optional,
            }

    return fields


def _analyze_field_mappings(impl_code: str, method_name: str = "") -> dict:
    """Analyze implementation code for field mappings."""
    mappings = {
        "assignments": [],
        "constructor_calls": [],
        "dict_literals": [],
    }

    # If method name specified, try to extract just that method
    if method_name:
        method_pattern = rf'def\s+{method_name}\s*\([^)]*\)[^:]*:(.+?)(?=\n    def |\nclass |\Z)'
        match = re.search(method_pattern, impl_code, re.DOTALL)
        if match:
            impl_code = match.group(1)

    # Find field assignments (target.field = source.field)
    assign_pattern = r'(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)'
    for match in re.finditer(assign_pattern, impl_code):
        mappings["assignments"].append({
            "target_var": match.group(1),
            "target_field": match.group(2),
            "source_var": match.group(3),
            "source_field": match.group(4),
        })

    # Find constructor/model instantiation with keyword args
    constructor_pattern = r'(\w+)\s*\(\s*([^)]+)\s*\)'
    for match in re.finditer(constructor_pattern, impl_code):
        args_str = match.group(2)
        # Parse keyword arguments
        kwarg_pattern = r'(\w+)\s*=\s*([^,)]+)'
        for kwmatch in re.finditer(kwarg_pattern, args_str):
            mappings["constructor_calls"].append({
                "constructor": match.group(1),
                "field": kwmatch.group(1),
                "value": kwmatch.group(2).strip(),
            })

    return mappings


def _find_similar_field(target_field: str, source_fields: dict) -> str | None:
    """Find a similar field name in source that might map to target."""
    target_lower = target_field.lower()

    # Direct match
    if target_field in source_fields:
        return target_field

    # Case-insensitive match
    for source_field in source_fields:
        if source_field.lower() == target_lower:
            return source_field

    # Common naming variations (generic patterns)
    variations = {
        # Location-related
        'area': ['region', 'location', 'city', 'zone', 'area_id'],
        'location': ['area', 'region', 'city', 'address', 'place'],
        # Time/schedule-related
        'schedule': ['format', 'working_format', 'time_format'],
        'format': ['schedule', 'type', 'mode'],
        # Type/category-related
        'type': ['kind', 'category', 'classification'],
        'category': ['type', 'kind', 'group'],
        # Naming variations
        'name': ['title', 'label', 'display_name'],
        'title': ['name', 'heading', 'label'],
        'description': ['details', 'text', 'content', 'summary'],
        # ID variations
        'id': ['identifier', 'uuid', 'key'],
        'uuid': ['id', 'identifier', 'guid'],
        # Status variations
        'status': ['state', 'condition'],
        'state': ['status', 'phase'],
        # User-related
        'user': ['owner', 'creator', 'author'],
        'user_id': ['owner_id', 'creator_id', 'author_id'],
    }

    # Check if target has known variations
    for key, values in variations.items():
        if target_lower == key or target_lower in values:
            for source_field in source_fields:
                if source_field.lower() in values or source_field.lower() == key:
                    return source_field

    # Substring match
    for source_field in source_fields:
        if target_lower in source_field.lower() or source_field.lower() in target_lower:
            return source_field

    return None


def _normalize_type(type_str: str) -> str:
    """Normalize type string for comparison."""
    if not type_str:
        return ""

    # Remove Optional wrapper
    type_str = re.sub(r'Optional\[(.+)\]', r'\1', type_str)

    # Remove whitespace
    type_str = type_str.replace(" ", "")

    # Remove quotes
    type_str = type_str.replace("'", "").replace('"', '')

    return type_str.lower()


def _types_compatible(source_type: str, target_type: str) -> bool:
    """Check if source type is compatible with target type."""
    # Same types are compatible
    if source_type == target_type:
        return True

    # Common compatible type mappings
    compatible_pairs = [
        ('str', 'string'),
        ('int', 'integer'),
        ('float', 'number'),
        ('bool', 'boolean'),
        ('list', 'array'),
        ('dict', 'object'),
    ]

    for t1, t2 in compatible_pairs:
        if (source_type == t1 and target_type == t2) or (source_type == t2 and target_type == t1):
            return True

    # If target allows None (Optional), any source is potentially compatible
    if 'none' in target_type or 'optional' in target_type:
        return True

    # List types - check inner type
    if source_type.startswith('list[') and target_type.startswith('list['):
        inner_source = source_type[5:-1]
        inner_target = target_type[5:-1]
        return _types_compatible(inner_source, inner_target)

    return False


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
        return json.dumps({"error": f"Structure validation failed: {e}"})


@tool
def validate_code_quality_tool(file_path: str) -> str:
    """Detect code quality issues and anti-patterns in Python code.

    Checks for:
    - Bare except clauses (except:, except Exception:)
    - Exception suppression (except: pass)
    - Broad exception handling without re-raise
    - Mocking in production code (not test files)
    - Empty except blocks

    Args:
        file_path: Path to the Python file to analyze.

    Returns:
        JSON string with detected anti-patterns and their locations.
    """
    try:
        path = Path(file_path)
        if not path.exists():
            return json.dumps({"error": f"File not found: {file_path}"})

        code = path.read_text(encoding='utf-8')
        is_test_file = 'test_' in path.name or path.name.startswith('test')

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return json.dumps({"error": f"Syntax error: {e}"})

        issues = []

        class AntiPatternVisitor(ast.NodeVisitor):
            def __init__(self):
                self.current_function = None

            def visit_FunctionDef(self, node: ast.FunctionDef):
                old_func = self.current_function
                self.current_function = node.name
                self.generic_visit(node)
                self.current_function = old_func

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
                old_func = self.current_function
                self.current_function = node.name
                self.generic_visit(node)
                self.current_function = old_func

            def visit_Try(self, node):
                for handler in node.handlers:
                    # Check for bare except (except:)
                    if handler.type is None:
                        issues.append({
                            "type": "bare_except",
                            "line": handler.lineno,
                            "message": "Bare 'except:' clause catches all exceptions including KeyboardInterrupt and SystemExit. Use specific exception types.",
                            "function": self.current_function,
                            "severity": "error",
                        })
                    # Check for except Exception
                    elif isinstance(handler.type, ast.Name) and handler.type.id == 'Exception':
                        # Check if it re-raises
                        has_raise = any(
                            isinstance(stmt, ast.Raise)
                            for stmt in ast.walk(handler)
                        )
                        if not has_raise:
                            issues.append({
                                "type": "broad_exception_without_reraise",
                                "line": handler.lineno,
                                "message": "'except Exception:' without re-raise suppresses all errors. Either handle specific exceptions or re-raise.",
                                "function": self.current_function,
                                "severity": "error",
                            })

                    # Check for exception suppression (except: pass or except Exception: pass)
                    if len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass):
                        issues.append({
                            "type": "exception_suppression",
                            "line": handler.lineno,
                            "message": "Exception is silently suppressed with 'pass'. This hides errors and makes debugging difficult.",
                            "function": self.current_function,
                            "severity": "error",
                        })

                    # Check for empty except block (only has ... or pass)
                    if len(handler.body) == 1 and isinstance(handler.body[0], ast.Expr):
                        if isinstance(handler.body[0].value, ast.Constant) and handler.body[0].value.value is ...:
                            issues.append({
                                "type": "empty_except_block",
                                "line": handler.lineno,
                                "message": "Empty except block with '...' suppresses errors.",
                                "function": self.current_function,
                                "severity": "warning",
                            })

                self.generic_visit(node)

            def visit_Call(self, node):
                # Check for mocking in non-test files
                if not is_test_file:
                    func_name = ""
                    if isinstance(node.func, ast.Name):
                        func_name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        func_name = node.func.attr

                    if func_name in ('MagicMock', 'Mock', 'AsyncMock', 'patch'):
                        issues.append({
                            "type": "mock_in_production_code",
                            "line": node.lineno,
                            "message": f"'{func_name}' found in production code. Mocking should only be used in test files.",
                            "function": self.current_function,
                            "severity": "error",
                        })

                self.generic_visit(node)

        visitor = AntiPatternVisitor()
        visitor.visit(tree)

        # Categorize issues by severity
        errors = [i for i in issues if i.get("severity") == "error"]
        warnings = [i for i in issues if i.get("severity") == "warning"]

        results = {
            "valid": len(errors) == 0,
            "file": file_path,
            "is_test_file": is_test_file,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "issues": issues,
            "summary": {
                "bare_except": len([i for i in issues if i["type"] == "bare_except"]),
                "broad_exception_without_reraise": len([i for i in issues if i["type"] == "broad_exception_without_reraise"]),
                "exception_suppression": len([i for i in issues if i["type"] == "exception_suppression"]),
                "mock_in_production_code": len([i for i in issues if i["type"] == "mock_in_production_code"]),
            }
        }

        return json.dumps(results, indent=2)

    except Exception as e:
        return json.dumps({"error": f"Code quality validation failed: {e}"})


@tool
def validate_pydantic_usage_tool(code_root: str, api_data_structures: str = "") -> str:
    """Validate proper Pydantic usage in the codebase.

    Checks for:
    - Models that should inherit from BaseModel but don't
    - Duplicate model definitions across files in the SAME codebase

    NOTE: api_data_structures is a SPECIFICATION document describing what models
    should look like. Models implementing this spec are CORRECT, not duplicates.
    This tool only flags when the SAME model is defined in MULTIPLE implementation files.

    Args:
        code_root: Root directory of the code to analyze.
        api_data_structures: Optional API spec (for reference, not for import checking).

    Returns:
        JSON string with validation results.
    """
    try:
        root = Path(code_root)
        if not root.exists():
            return json.dumps({"error": f"Directory not found: {code_root}"})

        issues = []
        model_definitions = {}  # model_name -> list of file paths
        models_in_api = set()

        # Extract model names from API data structures (for reference only)
        if api_data_structures:
            class_pattern = r'class\s+(\w+)\s*\([^)]*BaseModel[^)]*\)'
            for match in re.finditer(class_pattern, api_data_structures):
                models_in_api.add(match.group(1))

        # Analyze all Python files
        for py_file in root.rglob("*.py"):
            if "__pycache__" in str(py_file):
                continue

            try:
                code = py_file.read_text(encoding='utf-8')
                tree = ast.parse(code)
            except (SyntaxError, UnicodeDecodeError):
                continue

            relative_path = str(py_file.relative_to(root))
            is_models_file = "models" in py_file.name.lower()

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    class_name = node.name

                    # Check if class should be a Pydantic model
                    is_pydantic = False
                    base_classes = []
                    for base in node.bases:
                        if isinstance(base, ast.Name):
                            base_classes.append(base.id)
                            if base.id in ('BaseModel', 'BaseSettings'):
                                is_pydantic = True
                        elif isinstance(base, ast.Attribute):
                            base_classes.append(base.attr)
                            if base.attr in ('BaseModel', 'BaseSettings'):
                                is_pydantic = True

                    # Only track Pydantic models for duplicate detection
                    if is_pydantic:
                        if class_name not in model_definitions:
                            model_definitions[class_name] = []
                        model_definitions[class_name].append({
                            "file": relative_path,
                            "line": node.lineno,
                        })

                    # Check if this looks like a data model that should use Pydantic
                    looks_like_data_model = (
                        is_models_file or
                        class_name.endswith(('Response', 'Request', 'Model', 'Schema', 'View', 'Info')) or
                        class_name in models_in_api
                    )

                    # Check if class uses dataclass decorator
                    is_dataclass = any(
                        isinstance(d, ast.Name) and d.id == 'dataclass'
                        or isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == 'dataclass'
                        for d in node.decorator_list
                    )

                    if looks_like_data_model and not is_pydantic and not is_dataclass:
                        # Check if it's a simple class with typed attributes
                        has_typed_attrs = any(
                            isinstance(item, ast.AnnAssign) for item in node.body
                        )
                        if has_typed_attrs:
                            issues.append({
                                "type": "missing_pydantic",
                                "file": relative_path,
                                "line": node.lineno,
                                "class_name": class_name,
                                "message": f"Class '{class_name}' looks like a data model but doesn't inherit from BaseModel. Consider using Pydantic.",
                                "severity": "warning",
                            })

                    # NOTE: We do NOT flag models that match the API spec as "redefinitions"
                    # The API spec is documentation, not importable code. Implementation is correct.

        # Check for duplicate model definitions
        for model_name, locations in model_definitions.items():
            if len(locations) > 1:
                # Filter to only show duplicates of data model-like classes
                if (model_name.endswith(('Response', 'Request', 'Model', 'Schema', 'View', 'Info'))
                    or model_name in models_in_api):
                    files = [loc["file"] for loc in locations]
                    issues.append({
                        "type": "duplicate_model",
                        "model_name": model_name,
                        "locations": locations,
                        "message": f"Model '{model_name}' is defined in multiple files: {', '.join(files)}. Consider using a shared definition.",
                        "severity": "error",
                    })

        errors = [i for i in issues if i.get("severity") == "error"]
        warnings = [i for i in issues if i.get("severity") == "warning"]

        results = {
            "valid": len(errors) == 0,
            "code_root": code_root,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "issues": issues,
            "models_in_api": list(models_in_api),
            "summary": {
                "missing_pydantic": len([i for i in issues if i["type"] == "missing_pydantic"]),
                "model_redefinition": len([i for i in issues if i["type"] == "model_redefinition"]),
                "duplicate_model": len([i for i in issues if i["type"] == "duplicate_model"]),
            }
        }

        return json.dumps(results, indent=2)

    except Exception as e:
        return json.dumps({"error": f"Pydantic validation failed: {e}"})


@tool
def validate_project_structure_tool(
    code_root: str,
    module_name: str,
    recommended_structure: str = ""
) -> str:
    """Validate project structure and file organization.

    Checks for:
    - Files in wrong locations (e.g., SOLUTION_SUMMARY.md in project root)
    - Missing recommended directories (src/, tests/)
    - Incorrect module organization

    Args:
        code_root: Root directory of the project.
        module_name: Name of the module being reviewed.
        recommended_structure: Optional text describing recommended structure.

    Returns:
        JSON string with validation results.
    """
    try:
        root = Path(code_root)
        if not root.exists():
            return json.dumps({"error": f"Directory not found: {code_root}"})

        issues = []
        structure_info = {
            "has_src": False,
            "has_tests": False,
            "has_pyproject": False,
            "has_readme": False,
            "files_in_root": [],
            "module_dirs": [],
        }

        # Check root level files
        for item in root.iterdir():
            if item.is_file():
                structure_info["files_in_root"].append(item.name)

                # Check for files that shouldn't be in root
                if item.name == "SOLUTION_SUMMARY.md":
                    issues.append({
                        "type": "misplaced_file",
                        "file": item.name,
                        "message": "SOLUTION_SUMMARY.md should not be in the project root. Move it to the module directory or docs folder.",
                        "severity": "warning",
                    })
                elif item.name.endswith('.py') and item.name not in ('__init__.py', 'conftest.py', 'setup.py'):
                    issues.append({
                        "type": "misplaced_file",
                        "file": item.name,
                        "message": f"Python file '{item.name}' found in project root. Should be in src/ or a module directory.",
                        "severity": "warning",
                    })

            elif item.is_dir():
                if item.name == "src":
                    structure_info["has_src"] = True
                elif item.name in ("tests", "test"):
                    structure_info["has_tests"] = True
                elif item.name.endswith("_module") or item.name == module_name.lower().replace("service", "_module"):
                    structure_info["module_dirs"].append(item.name)

        # Check for pyproject.toml or setup.py
        if (root / "pyproject.toml").exists():
            structure_info["has_pyproject"] = True
        if (root / "setup.py").exists():
            structure_info["has_pyproject"] = True

        # Check for README
        if (root / "README.md").exists() or (root / "readme.md").exists():
            structure_info["has_readme"] = True

        # Check src/ directory structure if it exists
        src_dir = root / "src"
        if src_dir.exists():
            for item in src_dir.iterdir():
                if item.is_dir() and not item.name.startswith('_'):
                    structure_info["module_dirs"].append(f"src/{item.name}")

                    # Check module has __init__.py
                    if not (item / "__init__.py").exists():
                        issues.append({
                            "type": "missing_init",
                            "directory": f"src/{item.name}",
                            "message": f"Module directory 'src/{item.name}' is missing __init__.py.",
                            "severity": "warning",
                        })

                    # Check for test files in source directory (should be in tests/)
                    for py_file in item.glob("test_*.py"):
                        issues.append({
                            "type": "misplaced_test",
                            "file": f"src/{item.name}/{py_file.name}",
                            "message": f"Test file '{py_file.name}' found in source directory. Consider moving to tests/ directory.",
                            "severity": "info",
                        })

        # Check for common structure issues
        if not structure_info["has_src"] and not structure_info["module_dirs"]:
            issues.append({
                "type": "missing_structure",
                "message": "No src/ directory or module directories found. Recommended structure: project/src/module_name/",
                "severity": "warning",
            })

        # Parse recommended structure if provided
        if recommended_structure:
            # Check if the structure matches recommendations
            if "src/" in recommended_structure and not structure_info["has_src"]:
                issues.append({
                    "type": "structure_mismatch",
                    "message": "Recommended structure includes src/ directory but it's missing.",
                    "severity": "warning",
                })

            if "tests/" in recommended_structure and not structure_info["has_tests"]:
                issues.append({
                    "type": "structure_mismatch",
                    "message": "Recommended structure includes tests/ directory but it's missing.",
                    "severity": "info",
                })

        errors = [i for i in issues if i.get("severity") == "error"]
        warnings = [i for i in issues if i.get("severity") == "warning"]

        results = {
            "valid": len(errors) == 0,
            "code_root": code_root,
            "structure": structure_info,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "issues": issues,
        }

        return json.dumps(results, indent=2)

    except Exception as e:
        return json.dumps({"error": f"Structure validation failed: {e}"})


@tool
def validate_api_model_compliance_tool(
    impl_file: str,
    api_data_structures: str
) -> str:
    """Validate that implementation models comply with API data structure definitions.

    Compares implementation model fields with API-defined Pydantic models to ensure:
    - All required fields are present
    - Field types match
    - Optional fields are correctly marked

    Args:
        impl_file: Path to the implementation file with models.
        api_data_structures: API data structure definitions (Pydantic models as text).

    Returns:
        JSON string with compliance results.
    """
    try:
        impl_path = Path(impl_file)
        if not impl_path.exists():
            return json.dumps({"error": f"File not found: {impl_file}"})

        impl_code = impl_path.read_text(encoding='utf-8')
        issues = []

        # Parse API data structures to extract model definitions
        api_models = {}
        class_pattern = r'class\s+(\w+)\s*\([^)]*BaseModel[^)]*\):\s*(?:"""[^"]*"""\s*)?((?:[^}]+?)?)(?=\nclass|\n#|$)'

        for match in re.finditer(class_pattern, api_data_structures, re.DOTALL):
            model_name = match.group(1)
            body = match.group(2)

            fields = {}
            # Parse field definitions
            field_pattern = r'(\w+):\s*(Optional\[)?([^=\n]+?)\]?\s*=\s*Field\(([^)]+)\)'
            for field_match in re.finditer(field_pattern, body):
                field_name = field_match.group(1)
                is_optional = field_match.group(2) is not None
                field_type = field_match.group(3).strip()
                field_args = field_match.group(4)

                # Check if field is required (... as default)
                is_required = '...' in field_args and 'default=' not in field_args

                fields[field_name] = {
                    "type": field_type,
                    "optional": is_optional,
                    "required": is_required and not is_optional,
                }

            # Also check simple type annotations
            simple_field_pattern = r'(\w+):\s*(Optional\[)?([^\n=]+?)\]?\s*(?:=\s*([^\n]+))?$'
            for field_match in re.finditer(simple_field_pattern, body, re.MULTILINE):
                field_name = field_match.group(1)
                if field_name not in fields and field_name not in ('model_config',):
                    is_optional = field_match.group(2) is not None
                    field_type = field_match.group(3).strip()
                    default = field_match.group(4)

                    fields[field_name] = {
                        "type": field_type,
                        "optional": is_optional or default is not None,
                        "required": not is_optional and default is None,
                    }

            api_models[model_name] = fields

        # Parse implementation to find model definitions
        try:
            impl_tree = ast.parse(impl_code)
        except SyntaxError as e:
            return json.dumps({"error": f"Syntax error in implementation: {e}"})

        impl_models = {}
        for node in ast.walk(impl_tree):
            if isinstance(node, ast.ClassDef):
                # Check if it inherits from BaseModel
                is_pydantic = any(
                    (isinstance(base, ast.Name) and base.id == 'BaseModel')
                    or (isinstance(base, ast.Attribute) and base.attr == 'BaseModel')
                    for base in node.bases
                )

                if is_pydantic or node.name in api_models:
                    fields = {}
                    for item in node.body:
                        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                            field_name = item.target.id
                            type_hint = ast.unparse(item.annotation) if hasattr(ast, 'unparse') else str(item.annotation)
                            has_default = item.value is not None
                            is_optional = 'Optional' in type_hint or 'None' in type_hint

                            fields[field_name] = {
                                "type": type_hint,
                                "optional": is_optional,
                                "has_default": has_default,
                            }

                    impl_models[node.name] = {
                        "fields": fields,
                        "line": node.lineno,
                    }

        # Compare implementation with API definitions
        for model_name, api_fields in api_models.items():
            if model_name in impl_models:
                impl_info = impl_models[model_name]
                impl_fields = impl_info["fields"]

                # Check for missing required fields
                for field_name, field_info in api_fields.items():
                    if field_info["required"] and field_name not in impl_fields:
                        issues.append({
                            "type": "missing_required_field",
                            "model": model_name,
                            "field": field_name,
                            "line": impl_info["line"],
                            "message": f"Model '{model_name}' is missing required field '{field_name}' (type: {field_info['type']}).",
                            "severity": "error",
                        })
                    elif field_name not in impl_fields and not field_info["optional"]:
                        issues.append({
                            "type": "missing_field",
                            "model": model_name,
                            "field": field_name,
                            "line": impl_info["line"],
                            "message": f"Model '{model_name}' is missing field '{field_name}'.",
                            "severity": "warning",
                        })

                # Check for type mismatches (simplified comparison)
                for field_name, impl_field_info in impl_fields.items():
                    if field_name in api_fields:
                        api_type = api_fields[field_name]["type"]
                        impl_type = impl_field_info["type"]
                        # Simple check - see if core type is present
                        api_core = re.sub(r'Optional\[|\]|List\[', '', api_type)
                        impl_core = re.sub(r'Optional\[|\]|List\[', '', impl_type)
                        if api_core != impl_core and api_core.lower() != impl_core.lower():
                            issues.append({
                                "type": "type_mismatch",
                                "model": model_name,
                                "field": field_name,
                                "expected": api_type,
                                "actual": impl_type,
                                "line": impl_info["line"],
                                "message": f"Field '{field_name}' in '{model_name}' has type '{impl_type}' but API expects '{api_type}'.",
                                "severity": "warning",
                            })

        errors = [i for i in issues if i.get("severity") == "error"]
        warnings = [i for i in issues if i.get("severity") == "warning"]

        results = {
            "valid": len(errors) == 0,
            "file": impl_file,
            "api_models_checked": list(api_models.keys()),
            "impl_models_found": list(impl_models.keys()),
            "error_count": len(errors),
            "warning_count": len(warnings),
            "issues": issues,
        }

        return json.dumps(results, indent=2)

    except Exception as e:
        return json.dumps({"error": f"API model compliance validation failed: {e}"})

def _iter_python_files(code_root: str) -> list[Path]:
    root = Path(code_root)
    if not root.exists():
        return []
    files: list[Path] = []
    for p in root.rglob("*.py"):
        if "__pycache__" in str(p):
            continue
        files.append(p)
    return files


def _extract_packages_from_poetry_lock(code_root: Path) -> set[str]:
    """Extract package names from poetry.lock if it exists.

    Returns a set of package names (e.g., {'pydantic', 'typing', ...}).
    """
    if tomllib is None:
        return set()

    lock_file = code_root / "poetry.lock"
    if not lock_file.exists():
        return set()

    try:
        with open(lock_file, 'rb') as f:
            data = tomllib.load(f)

        packages = set()
        if 'package' in data:
            for pkg in data['package']:
                if 'name' in pkg:
                    # Normalize package names: replace dashes with dots for comparison
                    # e.g., "appfactory-components" -> "appfactory.components"
                    pkg_name = pkg['name'].replace('-', '.')
                    packages.add(pkg_name)
                    # Also add the original name in case imports use dashes
                    packages.add(pkg['name'])
        return packages
    except Exception:
        return set()


def _extract_packages_from_pyproject(code_root: Path) -> set[str]:
    """Extract direct dependencies from pyproject.toml if it exists.

    Returns a set of package names.
    """
    if tomllib is None:
        return set()

    pyproject_file = code_root / "pyproject.toml"
    if not pyproject_file.exists():
        return set()

    try:
        with open(pyproject_file, 'rb') as f:
            data = tomllib.load(f)

        packages = set()
        # Check for Poetry-style dependencies
        if 'tool' in data and 'poetry' in data['tool']:
            poetry_data = data['tool']['poetry']
            if 'dependencies' in poetry_data:
                for dep_name in poetry_data['dependencies'].keys():
                    if dep_name != 'python':
                        packages.add(dep_name.replace('-', '.'))
                        packages.add(dep_name)

        # Check for PEP 621-style dependencies (project.dependencies)
        if 'project' in data and 'dependencies' in data['project']:
            for dep in data['project']['dependencies']:
                # Dependencies can be strings like "pydantic>=2.0" or dicts
                if isinstance(dep, str):
                    # Extract package name (before version specifiers)
                    pkg_name = dep.split('>=')[0].split('==')[0].split('~=')[0].split('!=')[0].split('@')[0].strip()
                    if pkg_name:
                        packages.add(pkg_name.replace('-', '.'))
                        packages.add(pkg_name)

        return packages
    except Exception:
        return set()


def _find_dependency_root(code_root: Path) -> Path:
    """Return the directory that contains pyproject.toml or poetry.lock.

    When generated code is passed to review, code_root may be the extraction
    directory (e.g. temp dir) with the actual project in a single subdirectory
    (e.g. code_root/M4_lvm_run/pyproject.toml). This helper checks code_root
    first, then each immediate subdirectory, so dependency files are found either way.
    """
    if not code_root.exists() or not code_root.is_dir():
        return code_root
    if (code_root / "pyproject.toml").exists() or (code_root / "poetry.lock").exists():
        return code_root
    subdirs = [p for p in code_root.iterdir() if p.is_dir() and not p.name.startswith(".")]
    for sub in subdirs:
        if (sub / "pyproject.toml").exists() or (sub / "poetry.lock").exists():
            return sub
    return code_root


def _extract_known_packages(code_root: Path) -> set[str]:
    """Extract known package names from dependency files.

    Checks poetry.lock (preferred, includes transitive deps) and pyproject.toml (fallback).
    Looks at code_root and, if needed, one level of subdirectories (so generated code
    extracted to a subdir still has its pyproject.toml/poetry.lock seen).
    Also includes common stdlib packages.
    """
    packages = set()
    dep_root = _find_dependency_root(code_root)

    # Try poetry.lock first (most complete)
    packages.update(_extract_packages_from_poetry_lock(dep_root))

    # Fallback to pyproject.toml if poetry.lock not found
    if not packages:
        packages.update(_extract_packages_from_pyproject(dep_root))

    # Add common stdlib packages as fallback
    stdlib_packages = {
        'typing', 'dataclasses', 'collections', 'pathlib', 'os', 'sys', 'json',
        're', 'datetime', 'time', 'functools', 'itertools', 'operator', 'enum',
        'abc', 'contextlib', 'copy', 'hashlib', 'io', 'logging', 'math', 'random',
        'string', 'struct', 'threading', 'unittest', 'urllib', 'warnings', 'weakref',
        'asyncio', 'concurrent', 'multiprocessing', 'queue', 'select', 'socket',
        'ssl', 'subprocess', 'tempfile', 'traceback', 'uuid', 'xml', 'zipfile',
        'argparse', 'configparser', 'csv', 'email', 'html', 'http', 'sqlite3',
        'base64', 'binascii', 'codecs', 'decimal', 'fractions', 'statistics',
    }
    packages.update(stdlib_packages)

    return packages


@tool
def validate_cross_file_usage_tool(
    code_root: str,
    max_inferences: int = 400,
    include_tests: bool = False,
    external_components_path: str = "",
) -> str:
    """Validate basic cross-file symbol resolution and usage.

    Purpose:
    - Detect unresolved imports / symbols that look project-local.
    - Detect obvious attribute/method accesses where Jedi can infer a
      project-local definition for the base but cannot resolve the member.

    This validator is intentionally conservative (high precision) and caps Jedi
    inference calls to keep runtime predictable.

    Args:
        code_root: Root directory of the generated code.
        max_inferences: Hard cap on Jedi inference calls across the whole repo.
        include_tests: Whether to also analyze test_*.py files.
        external_components_path: Optional path to external components (e.g., AppFactory-components).
                                  Used to resolve imports but NOT validated. If provided, imports from
                                  this path will be considered valid and not flagged as errors.

    Returns:
        JSON string with {valid, issues, stats}.
    """
    try:
        root = Path(code_root)
        if not root.exists():
            return json.dumps({"error": f"Directory not found: {code_root}"})

        # Build sys_path: include code_root and external_components_path if provided
        sys_path = [str(root)]
        external_components_root = None
        if external_components_path:
            external_path = Path(external_components_path)
            if external_path.exists():
                external_components_root = external_path
                # Add the parent directory so imports like "appfactory.components" resolve correctly
                sys_path.append(str(external_path.parent))
                sys_path.append(str(external_path))

        project = jedi.Project(path=str(root), sys_path=sys_path)

        # Extract known packages from dependency files (poetry.lock, pyproject.toml)
        known_packages = _extract_known_packages(root)

        files = _iter_python_files(code_root)
        if not include_tests:
            files = [p for p in files if "test" not in p.name.lower()]

        issues: list[dict[str, Any]] = []
        infer_calls = 0
        analyzed_files = 0

        def _is_known_package(module_name: str) -> bool:
            """Check if an import is from a known dependency package."""
            if not module_name:
                return False
            # Extract top-level module name (first part before any dots)
            top_level = module_name.split('.')[0]
            # Check if it's in known packages (handle both dash and dot variants)
            return top_level in known_packages or top_level.replace('-', '.') in known_packages

        def _is_external_component_import(module_name: str) -> bool:
            """Check if an import is from external components (should be ignored)."""
            if not external_components_root or not module_name:
                return False
            # Check if module starts with appfactory (or other external component prefixes)
            if module_name.startswith("appfactory."):
                return True
            # Check if the module path exists in external components
            parts = module_name.split(".")
            if parts:
                potential_path = external_components_root / parts[0]
                if potential_path.exists():
                    return True
            return False

        def _add_issue(file_path: str, line: int | None, msg: str, issue_type: str) -> None:
            # Skip issues from external components files (we don't validate those)
            if external_components_root:
                try:
                    file_path_obj = Path(file_path)
                    if external_components_root in file_path_obj.parents or file_path_obj == external_components_root:
                        return  # Don't report issues in external components
                except Exception:
                    pass

            issues.append(
                {
                    "type": issue_type,
                    "file": file_path,
                    "line": line,
                    "message": msg,
                    "severity": "warning" if issue_type != "unresolved_import" else "error",
                }
            )

        for path in files:
            try:
                code = path.read_text(encoding="utf-8")
            except Exception:
                continue

            analyzed_files += 1

            try:
                tree = ast.parse(code)
            except SyntaxError:
                continue

            # 1) Unresolved imports (best-effort, avoid 3rd-party noise)
            for node in ast.walk(tree):
                if infer_calls >= max_inferences:
                    break

                if isinstance(node, ast.Import):
                    line = getattr(node, "lineno", None) or 1
                    base_col = getattr(node, "col_offset", 0)

                    for alias in node.names:
                        if infer_calls >= max_inferences:
                            break

                        src_line = code.splitlines()[line - 1] if 0 < line <= len(code.splitlines()) else ""
                        idx = src_line.find(alias.name)
                        if idx < 0:
                            idx = base_col

                        script = jedi.Script(code, path=str(path), project=project)
                        infer_calls += 1
                        inferred = script.infer(line, idx + len(alias.name))

                        if not inferred:
                            # Conservative: only flag if it smells like project-local (not stdlib)
                            if "." in alias.name or alias.name in {"src", "app", "module"}:
                                _add_issue(
                                    str(path),
                                    line,
                                    f"Unresolved import: import {alias.name}",
                                    "unresolved_import",
                                )

                elif isinstance(node, ast.ImportFrom):
                    line = getattr(node, "lineno", None) or 1
                    base_col = getattr(node, "col_offset", 0)

                    # Skip known dependency packages (from poetry.lock/pyproject.toml)
                    if node.module and _is_known_package(node.module):
                        continue

                    # Skip external component imports (they're trusted, not validated)
                    if node.module and _is_external_component_import(node.module):
                        continue

                    # Resolve module
                    if node.module and infer_calls < max_inferences:
                        src_line = code.splitlines()[line - 1] if 0 < line <= len(code.splitlines()) else ""
                        idx = src_line.find(node.module)
                        if idx < 0:
                            idx = base_col

                        script = jedi.Script(code, path=str(path), project=project)
                        infer_calls += 1
                        inferred_mod = script.infer(line, idx + len(node.module))
                        if not inferred_mod and (node.level or 0) == 0:
                            _add_issue(
                                str(path),
                                line,
                                f"Unresolved import module: from {node.module} import ...",
                                "unresolved_import",
                            )

                    # Resolve imported names (skip if from external components)
                    if not _is_external_component_import(node.module or ""):
                        for alias in node.names:
                            if infer_calls >= max_inferences:
                                break
                            if alias.name == "*":
                                continue

                            src_line = code.splitlines()[line - 1] if 0 < line <= len(code.splitlines()) else ""
                            idx = src_line.find(alias.name)
                            if idx < 0:
                                idx = base_col

                            script = jedi.Script(code, path=str(path), project=project)
                            infer_calls += 1
                            inferred = script.infer(line, idx + len(alias.name))
                            if not inferred and (node.level or 0) == 0:
                                _add_issue(
                                    str(path),
                                    line,
                                    f"Unresolved imported symbol: from {node.module} import {alias.name}",
                                    "unresolved_import",
                                )

            if infer_calls >= max_inferences:
                break

            # 2) Obvious attribute access failures where base is project-local
            for node in ast.walk(tree):
                if infer_calls >= max_inferences:
                    break
                if not isinstance(node, ast.Attribute):
                    continue
                if not hasattr(node, "lineno") or not hasattr(node, "col_offset"):
                    continue

                line = node.lineno
                src_line = code.splitlines()[line - 1] if 0 < line <= len(code.splitlines()) else ""
                attr = node.attr
                idx = src_line.find(attr, node.col_offset)
                if idx < 0:
                    continue

                script = jedi.Script(code, path=str(path), project=project)
                infer_calls += 1
                inferred_attr = script.infer(line, idx + len(attr))
                if inferred_attr:
                    continue

                # Skip private/class attributes (e.g. cls._instance in singletons). Jedi often
                # cannot resolve these when they are set dynamically; reporting them is noisy
                # and causes false positives for valid patterns.
                if attr.startswith("_"):
                    continue

                # Infer base only if simple name; keep precision high
                if isinstance(node.value, ast.Name) and infer_calls < max_inferences:
                    base_name = node.value.id
                    base_idx = src_line.find(base_name)
                    if base_idx >= 0:
                        infer_calls += 1
                        base_defs = script.infer(line, base_idx + len(base_name))
                        base_is_project = any(
                            d.module_path and str(root) in str(d.module_path) for d in base_defs
                        )
                        if base_is_project:
                            _add_issue(
                                str(path),
                                line,
                                f"Possibly invalid member access '{base_name}.{attr}' "
                                f"(could not resolve '{attr}' on inferred project symbol)",
                                "unresolved_member",
                            )

        errors = [i for i in issues if i.get("severity") == "error"]
        results = {
            "valid": len(errors) == 0,
            "issues": issues,
            "stats": {
                "analyzed_files": analyzed_files,
                "infer_calls": infer_calls,
                "infer_cap": max_inferences,
            },
        }
        return json.dumps(results, indent=2)
    except Exception as e:
        return json.dumps({"error": f"Cross-file usage validation failed: {e}"})

