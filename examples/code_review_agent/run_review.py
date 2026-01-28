#!/usr/bin/env python3
"""
Code Review Agent Example

This example demonstrates how to use the Structured Code Review Agent to review generated code against a specification.

The StructuredCodeReviewAgent uses a deterministic workflow:
1. Discovery: Read spec and discover all files
2. Validation: Run all validators in parallel
3. Analysis: Single LLM call to interpret results
4. Report: Generate structured output

Uses environment variables from .env file:
    GPT_OSS_HOST - vLLM server URL (e.g., http://localhost:8000/v1)
    GPT_OSS_KEY - API key for the server
    GPT_OSS_MODEL_NAME - Model name served by vLLM

Usage:
    # Run with StructuredCodeReviewAgent (default, recommended)
    cd /Users/ngc436/Documents/projects/OpenHands
    poetry run python examples/code_review_agent/run_review.py

    # Or with debug logging to see all agent steps
    poetry run python examples/code_review_agent/run_review.py --debug

    # Use ReAct agent instead (slower, more exploratory)
    poetry run python examples/code_review_agent/run_review.py --react

The example uses:
- Module specification: test_data/module_M4/M4.md
- Generated code: test_data/module_M4/M4_v1.1_run3_before_CR_1.zip (extracted)
- General info about the whole system modules [OPTIONAL]: test_data/modules_description.md
- Guidelines (some rules on how to write code) [OPTIONAL]: test_data/guidelines/Agentic Coding Hints React Best Practices.md
- Data Structures path [OPTIONAL]:
"""

import argparse
import logging
import os
import sys
import tempfile
import time
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


def extract_code(code_path: Path, extract_to: Path) -> Path:
    """Extract or copy the generated code from a zip file or directory.

    Args:
        code_path: Path to the zip file or directory.
        extract_to: Directory to extract/copy to.

    Returns:
        Path to the extracted/copied code root.
    """
    import shutil

    if code_path.is_dir():
        print(f"📁 Copying directory {code_path.name}...")
        # Copy the entire directory to extract_to
        dest_path = extract_to / code_path.name
        shutil.copytree(code_path, dest_path, dirs_exist_ok=True)
        file_count = len(list(dest_path.rglob('*')))
        print(f"   Copied {file_count} files")
        return dest_path
    elif code_path.suffix == '.zip':
        print(f"📦 Extracting {code_path.name}...")
        with zipfile.ZipFile(code_path, 'r') as zf:
            zf.extractall(extract_to)
        file_count = len(list(extract_to.rglob('*')))
        print(f"   Extracted {file_count} files")
        return extract_to
    else:
        raise ValueError(f"Unsupported file type: {code_path}. Expected a directory or .zip file.")


def run_review_example(
    spec_path: Path,
    code_zip_path: Path,  # Can be zip file or directory
    debug: bool = False,
    output_file: Path | None = None,
    data_structures_path: Path | None = None,
    coding_guidelines_path: Path | None = None,
    modules_description_path: Path | None = None,
    external_components_path: Path | None = None,
    use_react: bool = False,
    quiet: bool = False,
):
    """Run the code review example.

    Args:
        spec_path: Path to the specification file.
        code_zip_path: Path to the zip file with generated code.
        debug: Enable debug logging.
        output_file: Optional path to write the report.
        data_structures_path: Optional path to API data structures file.
        coding_guidelines_path: Optional path to coding guidelines file.
        modules_description_path: Optional path to modules description file.
        external_components_path: Optional path to external components directory (e.g., AppFactory-components).
                                  Used to resolve imports but NOT validated.
        use_react: If True, use ReAct CodeReviewAgent (slower, more exploratory).
                  Default is False, using StructuredCodeReviewAgent (faster, better quality).
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

    if not quiet:
        print("🔑 Environment variables loaded from .env:")
        print(f"   GPT_OSS_HOST: {api_host or 'not set (will use default)'}")
        print(f"   GPT_OSS_KEY: {'*' * 8}...{api_key[-4:] if len(api_key) > 4 else '****'}")
        print(f"   GPT_OSS_MODEL_NAME: {model_name or 'not set (will use default)'}")

    # Import the agent (after checking API key to fail fast)
    agent_type = "ReAct" if use_react else "Structured"
    if not quiet:
        print(f"🔧 Loading {agent_type} Code Review Agent...")

    try:
        # Always import both agents (imports are cheap, avoids type checker issues)
        from openhands.agenthub.langgraph_reviewer_agent.structured_agent import (
            StructuredCodeReviewAgent,
        )
        from openhands.agenthub.langgraph_reviewer_agent.agent import (
            CodeReviewAgent,
        )
        from openhands.agenthub.langgraph_reviewer_agent import (
            ReviewAgentConfig,
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

        # Extract or copy the code
        code_root = extract_code(code_zip_path, temp_path)

        # Show what we're reviewing
        if not quiet:
            print()
            print("=" * 60)
            print("CODE REVIEW AGENT EXAMPLE")
            print("=" * 60)
            print()
            print(f"📋 Specification: {spec_path}")
            print(f"📁 Code directory: {code_root}")
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
            max_iterations=25,  # For ReAct agent (not used by Structured)
        )

        if not quiet:
            print()
            print(f"🤖 Using model: {config.llm_model_name}")
            print(f"🌐 API endpoint: {config.llm_base_url}")
            print(f"⚡ Agent type: {agent_type} {'(6x faster, better quality)' if not use_react else '(exploratory)'}")
            print()

        # Create agent - StructuredCodeReviewAgent is the default
        if use_react:
            agent = CodeReviewAgent(config, debug=debug)
        else:
            agent = StructuredCodeReviewAgent(config, verbose=debug)

        # Load additional context documents
        data_structures = None
        coding_guidelines = None
        modules_description = None

        if data_structures_path and data_structures_path.exists():
            data_structures = data_structures_path.read_text(encoding='utf-8')
            if not quiet:
                print(f"📖 Using data structures: {data_structures_path}")
        if coding_guidelines_path and coding_guidelines_path.exists():
            coding_guidelines = coding_guidelines_path.read_text(encoding='utf-8')
            if not quiet:
                print(f"📖 Using coding guidelines: {coding_guidelines_path}")
        if modules_description_path and modules_description_path.exists():
            modules_description = modules_description_path.read_text(encoding='utf-8')
            if not quiet:
                print(f"📖 Using modules description: {modules_description_path}")

        external_components = None
        if external_components_path and external_components_path.exists():
            external_components = str(external_components_path)
            if not quiet:
                print(f"📦 Using external components: {external_components_path}")

        if not quiet:
            print()
            print("=" * 60)
            print("STARTING REVIEW...")
            print("=" * 60)
            print()

        # Run the review with timing
        start_time = time.time()
        try:
            review_kwargs = {
                "spec_path": str(spec_path),
                "code_root": str(code_root),
                "data_structures": data_structures,
                "coding_guidelines": coding_guidelines,
                "modules_description": modules_description,
            }
            # Only StructuredCodeReviewAgent supports external_components_path and module_names
            if not use_react:
                if external_components:
                    review_kwargs["external_components_path"] = external_components
                # module_names=None means auto-extract from spec (handled by agent)
                review_kwargs["module_names"] = None

            result = agent.review(**review_kwargs)
        except Exception as e:
            print(f"❌ Review failed: {e}")
            if debug:
                import traceback
                traceback.print_exc()
            sys.exit(1)
        finally:
            elapsed_time = time.time() - start_time
            if not quiet:
                print()
                print(f"⏱️  Total review time: {elapsed_time:.2f} seconds")
                print()

        # Print results
        if not quiet:
            print()
            print("=" * 60)
            print("REVIEW RESULTS")
            print("=" * 60)
            print()

            # Print the markdown report
            report = result.to_markdown()
            print(report)
        else:
            report = result.to_markdown()

        # Save to file if requested
        if output_file:
            output_file.write_text(report)
            if not quiet:
                print()
                print(f"📄 Report saved to: {output_file}")

        # Also save JSON
        json_output = output_file.with_suffix(".json") if output_file else None
        if json_output:
            json_output.write_text(result.model_dump_json(indent=2))
            if not quiet:
                print(f"📄 JSON saved to: {json_output}")

        if not quiet:
            print()
            print("=" * 60)

            # Return exit code based on review result
            if result.passed:
                print("✅ Review PASSED")
            else:
                print(f"❌ Review FAILED with {result.error_count} errors")

        return 0 if result.passed else 1


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
        default=PROJECT_ROOT / "test_data" / "module_M5" / "M5.md",
        help="Path to specification file (default: test_data/module_M4/M4.md)",
    )
    parser.add_argument(
        "--code",
        type=Path,
        default=PROJECT_ROOT / "test_data" / "module_M5" / "M5_run1_before_CR_1.zip",
        help="Path to generated code (zip file or directory) (default: test_data/module_M4/M4_v1.1_run3_after_tests_after_CR_2)",
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
        "--external-components",
        type=Path,
        default=PROJECT_ROOT / "test_data" / "AppFactory-components",
        help="Path to external components directory (default: test_data/AppFactory-components)",
    )
    parser.add_argument(
        "--react", "-r",
        action="store_true",
        help="Use ReAct CodeReviewAgent instead of StructuredCodeReviewAgent (slower, more exploratory)",
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(debug=args.debug)

    # Validate inputs
    if not args.spec.exists():
        print(f"❌ Specification file not found: {args.spec}")
        sys.exit(1)

    if not args.code.exists():
        print(f"❌ Code file or directory not found: {args.code}")
        sys.exit(1)

    # Run the review
    exit_code = run_review_example(
        spec_path=args.spec,
        code_zip_path=args.code,
        debug=args.debug,
        output_file=args.output,
        data_structures_path=args.data_structures,
        coding_guidelines_path=args.guidelines,
        modules_description_path=args.modules_description,
        external_components_path=args.external_components,
        use_react=args.react,
    )

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
