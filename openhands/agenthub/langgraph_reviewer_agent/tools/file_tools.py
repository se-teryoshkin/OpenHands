"""File operation tools for the code review agent."""

import json
from pathlib import Path

from langchain_core.tools import tool


@tool
def read_file_tool(file_path: str) -> str:
    """Read the contents of a file.

    Args:
        file_path: Path to the file to read.

    Returns:
        The contents of the file, or an error message if the file cannot be read.
    """
    try:
        path = Path(file_path)
        if not path.exists():
            return f"Error: File not found: {file_path}"

        if not path.is_file():
            return f"Error: Path is not a file: {file_path}"

        # Check file size (limit to 100KB for safety)
        if path.stat().st_size > 100_000:
            return f"Error: File too large (> 100KB): {file_path}"

        content = path.read_text(encoding='utf-8', errors='replace')
        return content

    except PermissionError:
        return f"Error: Permission denied: {file_path}"
    except Exception as e:
        return f"Error reading file: {e}"


@tool
def list_files_tool(directory_path: str, pattern: str = "*") -> str:
    """List files in a directory.

    Args:
        directory_path: Path to the directory to list.
        pattern: Glob pattern to filter files (default: "*" for all files).

    Returns:
        JSON string containing list of files and directories.
    """
    try:
        path = Path(directory_path)
        if not path.exists():
            return json.dumps({"error": f"Directory not found: {directory_path}"})

        if not path.is_dir():
            return json.dumps({"error": f"Path is not a directory: {directory_path}"})

        files = []
        dirs = []

        for item in path.glob(pattern):
            if item.is_file():
                files.append({
                    "name": item.name,
                    "path": str(item),
                    "size": item.stat().st_size,
                })
            elif item.is_dir():
                dirs.append({
                    "name": item.name,
                    "path": str(item),
                })

        result = {
            "directory": directory_path,
            "pattern": pattern,
            "files": sorted(files, key=lambda x: x["name"]),
            "directories": sorted(dirs, key=lambda x: x["name"]),
            "total_files": len(files),
            "total_dirs": len(dirs),
        }

        return json.dumps(result, indent=2)

    except Exception as e:
        return json.dumps({"error": f"Failed to list directory: {e}"})


@tool
def find_python_files_tool(root_path: str, exclude_patterns: list[str] | None = None) -> str:
    """Find all Python files in a directory tree.

    Args:
        root_path: Root directory to search from.
        exclude_patterns: Optional list of patterns to exclude
            (e.g., ["__pycache__", "venv", ".git"]).

    Returns:
        JSON string containing categorized Python files (source, test, config).
    """
    try:
        root = Path(root_path)
        if not root.exists():
            return json.dumps({"error": f"Root path not found: {root_path}"})

        if exclude_patterns is None:
            exclude_patterns = ["__pycache__", "venv", ".venv", ".git", "node_modules", ".tox"]

        source_files = []
        test_files = []
        config_files = []

        for py_file in root.rglob("*.py"):
            # Check exclusions
            skip = False
            for pattern in exclude_patterns:
                if pattern in str(py_file):
                    skip = True
                    break

            if skip:
                continue

            rel_path = str(py_file.relative_to(root))
            file_info = {
                "path": str(py_file),
                "relative_path": rel_path,
                "name": py_file.name,
            }

            # Categorize
            if py_file.name.startswith("test_") or "tests/" in rel_path or "test/" in rel_path:
                test_files.append(file_info)
            elif py_file.name in ("conftest.py", "setup.py", "pyproject.toml"):
                config_files.append(file_info)
            else:
                source_files.append(file_info)

        result = {
            "root": root_path,
            "source_files": sorted(source_files, key=lambda x: x["path"]),
            "test_files": sorted(test_files, key=lambda x: x["path"]),
            "config_files": sorted(config_files, key=lambda x: x["path"]),
            "total_source": len(source_files),
            "total_test": len(test_files),
            "total_config": len(config_files),
        }

        return json.dumps(result, indent=2)

    except Exception as e:
        return json.dumps({"error": f"Failed to find Python files: {e}"})
