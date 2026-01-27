#!/usr/bin/env python3
"""
Code Review Agent Example

This example demonstrates how to use the LangGraph-based Code Review Agent
to review generated code against a specification.

Uses environment variables from .env file:
    GPT_OSS_HOST - vLLM server URL (e.g., http://localhost:8000/v1)
    GPT_OSS_KEY - API key for the server
    GPT_OSS_MODEL_NAME - Model name served by vLLM

Usage:
    # Run the example (loads from .env automatically)
    cd /Users/ngc436/Documents/projects/OpenHands
    poetry run python examples/code_review_agent/run_review.py

    # Or with debug logging to see all agent steps
    poetry run python examples/code_review_agent/run_review.py --debug

The example uses:
- Module specification: test_data/module_M4/M4.md
- Generated code: test_data/module_M4/M4_v1.1_run3_before_CR_1.zip (extracted)

This code is BEFORE human code review, so the agent should find issues.
"""

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")


def setup_logging(debug: bool = False):
    """Setup logging configuration."""
    level = logging.DEBUG if debug else logging.INFO

    # Configure root logger
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    # Quiet down noisy loggers
    for logger_name in ["httpx", "httpcore", "urllib3", "openai"]:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def extract_code(zip_path: Path, extract_to: Path) -> Path:
    """Extract the generated code from a zip file.

    Args:
        zip_path: Path to the zip file.
        extract_to: Directory to extract to.

    Returns:
        Path to the extracted code root.
    """
    print(f"📦 Extracting {zip_path.name}...")

    with zipfile.ZipFile(zip_path, 'r') as zf:
        zf.extractall(extract_to)

    # List extracted contents
    contents = list(extract_to.iterdir())
    print(f"   Extracted {len(list(extract_to.rglob('*')))} files")

    return extract_to


def run_review_example(
    spec_path: Path,
    code_zip_path: Path,
    module_name: str,
    debug: bool = False,
    output_file: Path | None = None,
    data_structures_path: Path | None = None,
    coding_guidelines_path: Path | None = None,
    modules_description_path: Path | None = None,
    use_structured: bool = False,
):
    """Run the code review example.

    Args:
        spec_path: Path to the specification file.
        code_zip_path: Path to the zip file with generated code.
        module_name: Name of the module.
        debug: Enable debug logging.
        output_file: Optional path to write the report.
        data_structures_path: Optional path to API data structures file.
        coding_guidelines_path: Optional path to coding guidelines file.
        modules_description_path: Optional path to modules description file.
        use_structured: If True, use faster StructuredCodeReviewAgent.
    """
    # Check for required environment variables
    api_key = os.getenv("GPT_OSS_KEY")
    api_host = os.getenv("GPT_OSS_HOST")
    model_name = os.getenv("GPT_OSS_MODEL_NAME")

    if not api_key:
        print("❌ Error: GPT_OSS_KEY not found in environment!")
        print("   Make sure your .env file contains GPT_OSS_KEY")
        print()
        print("   Example .env file:")
        print("     GPT_OSS_HOST=http://your-vllm-server:8000/v1")
        print("     GPT_OSS_KEY=your-api-key")
        print("     GPT_OSS_MODEL_NAME=your-model-name")
        sys.exit(1)

    print("🔑 Environment variables loaded from .env:")
    print(f"   GPT_OSS_HOST: {api_host or 'not set (will use default)'}")
    print(f"   GPT_OSS_KEY: {'*' * 8}...{api_key[-4:] if len(api_key) > 4 else '****'}")
    print(f"   GPT_OSS_MODEL_NAME: {model_name or 'not set (will use default)'}")

    # Import the agent (after checking API key to fail fast)
    agent_type = "Structured" if use_structured else "ReAct"
    print(f"🔧 Loading {agent_type} Code Review Agent...")

    try:
        from openhands.agenthub.langgraph_reviewer_agent import (
            CodeReviewAgent,
            ReviewAgentConfig,
        )
        if use_structured:
            from openhands.agenthub.langgraph_reviewer_agent.structured_agent import (
                StructuredCodeReviewAgent,
            )
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print()
        print("   Make sure you have installed the required dependencies:")
        print("     poetry add langgraph langchain-openai")
        sys.exit(1)

    # Create a temporary directory for extracted code
    with tempfile.TemporaryDirectory(prefix="code_review_") as temp_dir:
        temp_path = Path(temp_dir)

        # Extract the code
        code_root = extract_code(code_zip_path, temp_path)

        # Show what we're reviewing
        print()
        print("=" * 60)
        print("CODE REVIEW AGENT EXAMPLE")
        print("=" * 60)
        print()
        print(f"📋 Specification: {spec_path}")
        print(f"📁 Code directory: {code_root}")
        print(f"📦 Module: {module_name}")
        print()

        # List the Python files
        python_files = list(code_root.rglob("*.py"))
        print(f"Found {len(python_files)} Python files:")
        for pf in python_files:
            if "__pycache__" not in str(pf):
                print(f"   - {pf.relative_to(code_root)}")
        print()

        # Create configuration - will automatically use GPT_OSS_* env vars
        config = ReviewAgentConfig(
            verbose=debug,
            max_iterations=25,  # Increased to allow more thorough review
        )

        print()
        print(f"🤖 Using model: {config.llm_model_name}")
        print(f"🌐 API endpoint: {config.llm_base_url}")
        print(f"⚡ Agent type: {agent_type}")
        print()

        # Create agent
        if use_structured:
            agent = StructuredCodeReviewAgent(config, verbose=debug)
        else:
            agent = CodeReviewAgent(config, debug=debug)

        # Load additional context documents
        data_structures = None
        coding_guidelines = None
        modules_description = None

        if data_structures_path and data_structures_path.exists():
            data_structures = data_structures_path.read_text(encoding='utf-8')
            print(f"📖 Using data structures: {data_structures_path}")
        if coding_guidelines_path and coding_guidelines_path.exists():
            coding_guidelines = coding_guidelines_path.read_text(encoding='utf-8')
            print(f"📖 Using coding guidelines: {coding_guidelines_path}")
        if modules_description_path and modules_description_path.exists():
            modules_description = modules_description_path.read_text(encoding='utf-8')
            print(f"📖 Using modules description: {modules_description_path}")

        print()
        print("=" * 60)
        print("STARTING REVIEW...")
        print("=" * 60)
        print()

        # Run the review
        try:
            result = agent.review(
                spec_path=str(spec_path),
                code_root=str(code_root),
                module_name=module_name,
                data_structures=data_structures,
                coding_guidelines=coding_guidelines,
                modules_description=modules_description,
            )
        except Exception as e:
            print(f"❌ Review failed: {e}")
            if debug:
                import traceback
                traceback.print_exc()
            sys.exit(1)

        # Print results
        print()
        print("=" * 60)
        print("REVIEW RESULTS")
        print("=" * 60)
        print()

        # Print the markdown report
        report = result.to_markdown()
        print(report)

        # Save to file if requested
        if output_file:
            output_file.write_text(report)
            print()
            print(f"📄 Report saved to: {output_file}")

        # Also save JSON
        json_output = output_file.with_suffix(".json") if output_file else None
        if json_output:
            json_output.write_text(result.model_dump_json(indent=2))
            print(f"📄 JSON saved to: {json_output}")

        print()
        print("=" * 60)

        # Return exit code based on review result
        if result.passed:
            print("✅ Review PASSED")
            return 0
        else:
            print(f"❌ Review FAILED with {result.error_count} errors")
            return 1


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Code Review Agent Example",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--debug", "-d",
        action="store_true",
        help="Enable debug logging to see all agent steps and tool calls",
    )
    parser.add_argument(
        "--spec",
        type=Path,
        default=PROJECT_ROOT / "test_data" / "module_M4" / "M4.md",
        help="Path to specification file (default: test_data/module_M4/M4.md)",
    )
    parser.add_argument(
        "--code",
        type=Path,
        default=PROJECT_ROOT / "test_data" / "module_M4" / "M4_v1.1_run3_before_CR_1.zip",
        help="Path to generated code zip (default: test_data/module_M4/M4_v1.1_run3_before_CR_1.zip)",
    )
    parser.add_argument(
        "--module",
        default="VacancyService",
        help="Module name (default: VacancyService)",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=PROJECT_ROOT / "examples" / "code_review_agent" / "review_report.md",
        help="Output file for the review report",
    )
    parser.add_argument(
        "--data-structures",
        type=Path,
        default=PROJECT_ROOT / "test_data" / "api_data_structures.md",
        help="Path to API data structures file (default: test_data/api_data_structures.md)",
    )
    parser.add_argument(
        "--guidelines",
        type=Path,
        default=PROJECT_ROOT / "test_data" / "guidelines" / "Agentic Coding Hints React Best Practices.md",
        help="Path to coding guidelines file",
    )
    parser.add_argument(
        "--modules-description",
        type=Path,
        default=PROJECT_ROOT / "test_data" / "modules_description.md",
        help="Path to modules description file (default: test_data/modules_description.md)",
    )
    parser.add_argument(
        "--structured", "-s",
        action="store_true",
        help="Use StructuredCodeReviewAgent (6x faster, better F1)",
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(debug=args.debug)

    # Validate inputs
    if not args.spec.exists():
        print(f"❌ Specification file not found: {args.spec}")
        sys.exit(1)

    if not args.code.exists():
        print(f"❌ Code zip file not found: {args.code}")
        sys.exit(1)

    # Run the review
    exit_code = run_review_example(
        spec_path=args.spec,
        code_zip_path=args.code,
        module_name=args.module,
        debug=args.debug,
        output_file=args.output,
        data_structures_path=args.data_structures,
        coding_guidelines_path=args.guidelines,
        modules_description_path=args.modules_description,
        use_structured=args.structured,
    )

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
