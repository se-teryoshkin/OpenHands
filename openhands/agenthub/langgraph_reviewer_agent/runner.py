#!/usr/bin/env python3
r"""Standalone runner for the LangGraph Code Review Agent.

This script allows running the code review agent from the command line
for testing and experimentation purposes.

Usage:
    python -m openhands.agenthub.langgraph_reviewer_agent.runner \\
        --spec /path/to/spec.md \\
        --code /path/to/generated/code \\
        --module ModuleName

Environment Variables:
    GPT_OSS_HOST: Base URL for the LLM API (default: https://api.openai.com/v1)
    GPT_OSS_KEY: API key for the LLM
    GPT_OSS_MODEL_NAME: Model name (default: gpt-4o)
    LANGFUSE_PUBLIC_KEY: Langfuse public key (optional tracing)
    LANGFUSE_SECRET_KEY: Langfuse secret key (optional tracing)
    LANGFUSE_HOST: Langfuse host (default: http://localhost:3000)
"""

import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Run the LangGraph Code Review Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--spec", "-s",
        required=True,
        help="Path to the specification file",
    )
    parser.add_argument(
        "--code", "-c",
        required=True,
        help="Root directory of the generated code",
    )
    parser.add_argument(
        "--module", "-m",
        required=True,
        help="Name of the module being reviewed",
    )
    parser.add_argument(
        "--component-docs", "-d",
        help="Path to file containing external component documentation",
    )
    parser.add_argument(
        "--output", "-o",
        choices=["markdown", "json"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    parser.add_argument(
        "--output-file", "-f",
        help="Write output to file instead of stdout",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Stream agent progress to stderr",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output",
    )
    parser.add_argument(
        "--model",
        help="Override the model name (env: GPT_OSS_MODEL_NAME)",
    )
    parser.add_argument(
        "--api-key",
        help="Override the API key (env: GPT_OSS_KEY)",
    )
    parser.add_argument(
        "--base-url",
        help="Override the API base URL (env: GPT_OSS_HOST)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Strict mode: fail on warnings too",
    )
    parser.add_argument(
        "--test-tools",
        action="store_true",
        help="Test the tools without running the full agent",
    )
    parser.add_argument(
        "--enable-langfuse",
        action="store_true",
        help="Enable Langfuse tracing for this run",
    )
    parser.add_argument(
        "--disable-langfuse",
        action="store_true",
        help="Disable Langfuse tracing for this run",
    )
    parser.add_argument(
        "--langfuse-session-id",
        help="Optional Langfuse session ID (groups traces in Langfuse)",
    )

    args = parser.parse_args()

    # Validate paths
    spec_path = Path(args.spec)
    if not spec_path.exists():
        print(f"Error: Specification file not found: {args.spec}", file=sys.stderr)
        sys.exit(1)

    code_path = Path(args.code)
    if not code_path.exists():
        print(f"Error: Code directory not found: {args.code}", file=sys.stderr)
        sys.exit(1)

    # Load component docs if provided
    component_docs = None
    if args.component_docs:
        docs_path = Path(args.component_docs)
        if docs_path.exists():
            component_docs = docs_path.read_text(encoding='utf-8')
        else:
            print(f"Warning: Component docs file not found: {args.component_docs}", file=sys.stderr)

    if args.test_tools:
        # Test tools without full agent
        run_tool_tests(str(spec_path), str(code_path))
        return

    # Import here to avoid slow startup for --help
    from openhands.agenthub.langgraph_reviewer_agent.config import ReviewAgentConfig
    from openhands.agenthub.langgraph_reviewer_agent.structured_agent import StructuredCodeReviewAgent

    # Create config
    config = ReviewAgentConfig.from_env()

    # Apply overrides
    if args.model:
        config.llm_model_name = args.model
    if args.api_key:
        config.llm_api_key = args.api_key
    if args.base_url:
        config.llm_base_url = args.base_url
    if args.strict:
        config.strict_mode = True
    config.verbose = args.verbose
    if args.enable_langfuse:
        config.langfuse_enabled = True
    if args.disable_langfuse:
        config.langfuse_enabled = False
    if args.langfuse_session_id:
        config.langfuse_session_id = args.langfuse_session_id

    # Validate config
    try:
        config.validate()
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Create agent (StructuredCodeReviewAgent; module_names from spec)
    agent = StructuredCodeReviewAgent(config, verbose=args.verbose)

    if args.verbose:
        print(f"Using model: {config.llm_model_name}", file=sys.stderr)
        print(f"API base URL: {config.llm_base_url}", file=sys.stderr)
        print(
            "Langfuse tracing: "
            + (
                f"enabled (host={config.langfuse_host}, session={config.langfuse_session_id or 'auto'})"
                if config.langfuse_enabled
                else "disabled"
            ),
            file=sys.stderr,
        )

    # Run review (StructuredCodeReviewAgent has no streaming; module_names filters by spec)
    if args.stream:
        print("Note: Streaming not available for StructuredCodeReviewAgent, running full review.", file=sys.stderr)
    print("Running review...", file=sys.stderr)
    result = agent.review(
        spec_path=str(spec_path),
        code_root=str(code_path),
        module_names=[args.module] if args.module else None,
    )

    if args.output == "markdown":
        output = result.to_markdown()
    else:
        output = result.model_dump_json(indent=2)

    write_output(output, args.output_file)

    # Print summary
    print(file=sys.stderr)
    status = "✅ PASSED" if result.passed else "❌ FAILED"
    print(f"Review {status}", file=sys.stderr)
    print(f"  Errors: {result.error_count}", file=sys.stderr)
    print(f"  Warnings: {result.warning_count}", file=sys.stderr)
    print(f"  Info: {result.info_count}", file=sys.stderr)

    if not result.passed:
        sys.exit(1)


def format_result(result_data: dict, format_type: str) -> str:
    """Format the result data."""
    if format_type == "json":
        return json.dumps(result_data, indent=2)

    # Markdown format
    return result_data.get("markdown_report", json.dumps(result_data, indent=2))


def write_output(output: str, output_file: str | None):
    """Write output to file or stdout."""
    if output_file:
        Path(output_file).write_text(output)
        print(f"Output written to: {output_file}", file=sys.stderr)
    else:
        print(output)


def run_tool_tests(spec_path: str, code_path: str):
    """Test the tools without running the full agent."""
    print("Testing code analysis tools...")
    print()

    from openhands.agenthub.langgraph_reviewer_agent.tools.code_analyzer import (
        extract_signatures_tool,
        extract_field_accesses_tool,
        extract_test_info_tool,
    )
    from openhands.agenthub.langgraph_reviewer_agent.tools.file_tools import (
        find_python_files_tool,
        read_file_tool,
    )

    # Find Python files
    print(f"📁 Finding Python files in: {code_path}")
    files_result = find_python_files_tool.invoke({"root_path": code_path})
    files_data = json.loads(files_result)

    if "error" in files_data:
        print(f"  Error: {files_data['error']}")
    else:
        print(f"  Found {files_data['total_source']} source files")
        print(f"  Found {files_data['total_test']} test files")

    print()

    # Test signature extraction on source files
    for source_file in files_data.get("source_files", [])[:3]:  # First 3
        file_path = source_file["path"]
        print(f"📝 Extracting signatures from: {source_file['relative_path']}")

        sig_result = extract_signatures_tool.invoke({"file_path": file_path})
        sig_data = json.loads(sig_result)

        if "error" in sig_data:
            print(f"  Error: {sig_data['error']}")
        else:
            for cls in sig_data.get("classes", []):
                print(f"  Class: {cls['name']}")
                for method in cls.get("methods", [])[:5]:
                    params = ", ".join(
                        f"{p['name']}: {p['type']}" if p['type'] else p['name']
                        for p in method.get("parameters", [])
                    )
                    ret = f" -> {method['return_type']}" if method.get("return_type") else ""
                    print(f"    - {method['name']}({params}){ret}")
        print()

    # Test on test files
    for test_file in files_data.get("test_files", [])[:2]:  # First 2
        file_path = test_file["path"]
        print(f"🧪 Analyzing test file: {test_file['relative_path']}")

        test_result = extract_test_info_tool.invoke({"test_file_path": file_path})
        test_data = json.loads(test_result)

        if "error" in test_data:
            print(f"  Error: {test_data['error']}")
        else:
            print(f"  Test functions: {test_data['test_count']}")
            print(f"  Assertions: {test_data['assertion_count']}")
            print(f"  Uses mocking: {test_data['uses_mocking']}")
            if test_data.get("mocked_objects"):
                print(f"  Mocked: {test_data['mocked_objects'][:3]}")
        print()

    print("✅ Tool tests complete")


if __name__ == "__main__":
    main()
