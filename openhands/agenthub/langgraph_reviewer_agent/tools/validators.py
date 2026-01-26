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
            (e.g., "item" to check item.* accesses).

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

        impl_code = path.read_text()

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

        code = path.read_text()
        is_test_file = 'test_' in path.name or path.name.startswith('test')

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return json.dumps({"error": f"Syntax error: {e}"})

        issues = []

        class AntiPatternVisitor(ast.NodeVisitor):
            def __init__(self):
                self.current_function = None

            def visit_FunctionDef(self, node):
                old_func = self.current_function
                self.current_function = node.name
                self.generic_visit(node)
                self.current_function = old_func

            visit_AsyncFunctionDef = visit_FunctionDef

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
