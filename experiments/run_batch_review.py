#!/usr/bin/env python3
"""
Batch Code Review Runner.

This script runs the code review agent on all available code versions
from test_data/module_M* and saves the results for comparison.

Usage:
    python run_batch_review.py [--modules M4 M5] [--stages before_cr after_cr]
"""

import json
import os
import sys
import zipfile
import tempfile
import shutil
import argparse
import logging
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Optional

# Add project root to path
script_dir = Path(__file__).parent
project_root = script_dir.parent
sys.path.insert(0, str(project_root))

# Load environment variables from .env
from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from openhands.agenthub.langgraph_reviewer_agent.config import ReviewAgentConfig
from openhands.agenthub.langgraph_reviewer_agent.structured_agent import StructuredCodeReviewAgent


@dataclass
class ReviewRun:
    """Result of a single review run."""
    module: str
    code_version: str
    stage: str
    cr_round: Optional[int]
    timestamp: str
    duration_seconds: float
    success: bool
    error_message: str
    issue_count: int
    issues: list
    summary: str


def setup_logging(verbose: bool = False):
    """Setup logging configuration."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
    return logging.getLogger(__name__)


def extract_zip_to_temp(zip_path: Path) -> Path:
    """Extract zip file to temporary directory and return the path."""
    temp_dir = tempfile.mkdtemp(prefix=f"review_{zip_path.stem}_")
    with zipfile.ZipFile(zip_path, 'r') as zf:
        zf.extractall(temp_dir)
    return Path(temp_dir)


def find_python_source_dir(extracted_dir: Path) -> Optional[Path]:
    """Find the main Python source directory in extracted code."""
    # Look for common source patterns
    patterns = [
        "src/*",
        "*/src/*",
        "*",
    ]

    for pattern in patterns:
        for path in extracted_dir.glob(pattern):
            if path.is_dir():
                py_files = list(path.glob("**/*.py"))
                if py_files:
                    return path

    # Fallback: just return the extracted dir
    py_files = list(extracted_dir.glob("**/*.py"))
    if py_files:
        return extracted_dir

    return None


def load_context_file(file_path: Path | None) -> str | None:
    """Load content from a context file if it exists."""
    if file_path and file_path.exists():
        try:
            return file_path.read_text(encoding='utf-8')
        except Exception:
            return None
    return None


def run_review(
    code_path: Path,
    spec_path: Path,
    module_name: str,
    logger: logging.Logger,
    max_iterations: int = 30,
    data_structures_path: Path | None = None,
    guidelines_path: Path | None = None,
    modules_description_path: Path | None = None,
) -> dict:
    """Run the code review agent on given code (StructuredCodeReviewAgent)."""
    start_time = datetime.now()

    try:
        # Load additional context documents
        data_structures = load_context_file(data_structures_path)
        coding_guidelines = load_context_file(guidelines_path)
        modules_description = load_context_file(modules_description_path)

        config = ReviewAgentConfig.from_env()
        config.max_iterations = max_iterations
        config.verbose = True

        agent = StructuredCodeReviewAgent(config, verbose=True)

        result = agent.review(
            spec_path=str(spec_path),
            code_root=str(code_path),
            module_names=[module_name] if module_name else None,
            data_structures=data_structures,
            coding_guidelines=coding_guidelines,
            modules_description=modules_description,
        )

        duration = (datetime.now() - start_time).total_seconds()

        return {
            "success": True,
            "duration_seconds": duration,
            "issue_count": len(result.comments),
            "issues": [
                {
                    "file": c.file_path,
                    "line": c.line_number,
                    "category": c.category.value if hasattr(c.category, 'value') else str(c.category),
                    "severity": c.severity.value if hasattr(c.severity, 'value') else str(c.severity),
                    "message": c.message,
                    "suggestion": c.suggestion
                }
                for c in result.comments
            ],
            "summary": result.summary,
            "error_message": ""
        }

    except Exception as e:
        duration = (datetime.now() - start_time).total_seconds()
        logger.error(f"Review failed: {e}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "duration_seconds": duration,
            "issue_count": 0,
            "issues": [],
            "summary": "",
            "error_message": str(e)
        }


def process_code_version(
    version_info: dict,
    spec_path: Path,
    module_name: str,
    logger: logging.Logger,
    max_iterations: int = 30,
    data_structures_path: Path | None = None,
    guidelines_path: Path | None = None,
    modules_description_path: Path | None = None,
) -> ReviewRun:
    """Process a single code version and run review."""
    version_path = Path(version_info["path"])
    version_name = version_info["name"]
    version_type = version_info["type"]
    stage = version_info.get("stage", "unknown")
    cr_round = version_info.get("cr_round")

    logger.info(f"Processing: {version_name} (stage: {stage})")

    # Extract if zip, or use directory directly
    if version_type == "zip":
        temp_dir = extract_zip_to_temp(version_path)
        code_dir = find_python_source_dir(temp_dir)
        cleanup_needed = True
    else:
        code_dir = find_python_source_dir(version_path)
        temp_dir = None
        cleanup_needed = False

    if not code_dir:
        logger.warning(f"No Python source found in {version_name}")
        return ReviewRun(
            module=module_name,
            code_version=version_name,
            stage=stage,
            cr_round=cr_round,
            timestamp=datetime.now().isoformat(),
            duration_seconds=0,
            success=False,
            error_message="No Python source found",
            issue_count=0,
            issues=[],
            summary=""
        )

    logger.info(f"Source directory: {code_dir}")

    result = run_review(
        code_dir, spec_path, module_name, logger, max_iterations,
        data_structures_path=data_structures_path,
        guidelines_path=guidelines_path,
        modules_description_path=modules_description_path,
    )

    # Cleanup temp directory
    if cleanup_needed and temp_dir:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return ReviewRun(
        module=module_name,
        code_version=version_name,
        stage=stage,
        cr_round=cr_round,
        timestamp=datetime.now().isoformat(),
        duration_seconds=result["duration_seconds"],
        success=result["success"],
        error_message=result["error_message"],
        issue_count=result["issue_count"],
        issues=result["issues"],
        summary=result["summary"]
    )


def load_extraction_data(extraction_dir: Path, module_name: str) -> Optional[dict]:
    """Load previously extracted CR data for a module."""
    extracted_file = extraction_dir / f"{module_name}_cr_extracted.json"
    if extracted_file.exists():
        with open(extracted_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


def find_context_documents(test_data_dir: Path) -> tuple[Path | None, Path | None, Path | None]:
    """Find additional context documents in test_data folder."""
    data_structures_path = None
    guidelines_path = None
    modules_description_path = None

    # Look for api_data_structures.md
    ds_path = test_data_dir / "api_data_structures.md"
    if ds_path.exists():
        data_structures_path = ds_path

    # Look for guidelines (take first .md in guidelines folder)
    guidelines_dir = test_data_dir / "guidelines"
    if guidelines_dir.exists():
        guideline_files = list(guidelines_dir.glob("*.md"))
        if guideline_files:
            guidelines_path = guideline_files[0]

    # Look for modules_description.md
    mod_path = test_data_dir / "modules_description.md"
    if mod_path.exists():
        modules_description_path = mod_path

    return data_structures_path, guidelines_path, modules_description_path


def run_module_reviews(
    module_dir: Path,
    output_dir: Path,
    extraction_dir: Path,
    logger: logging.Logger,
    stages_filter: Optional[list] = None,
    max_iterations: int = 30,
) -> list[ReviewRun]:
    """Run reviews for all code versions in a module."""
    module_name = module_dir.name

    # Find spec file
    spec_files = list(module_dir.glob("M*.md"))
    if not spec_files:
        logger.warning(f"No spec file found for {module_name}")
        return []
    spec_path = spec_files[0]

    # Find additional context documents in test_data folder
    test_data_dir = module_dir.parent
    data_structures_path, guidelines_path, modules_description_path = find_context_documents(test_data_dir)

    if data_structures_path:
        logger.info(f"Using data structures: {data_structures_path}")
    if guidelines_path:
        logger.info(f"Using guidelines: {guidelines_path}")
    if modules_description_path:
        logger.info(f"Using modules description: {modules_description_path}")

    # Load extraction data to get code versions
    extraction_data = load_extraction_data(extraction_dir, module_name)

    if extraction_data and extraction_data.get("code_versions"):
        code_versions = extraction_data["code_versions"]
    else:
        # Fallback: find code versions directly
        from experiments.extract_cr_comments import find_code_versions
        code_versions = find_code_versions(module_dir)

    if not code_versions:
        logger.warning(f"No code versions found for {module_name}")
        return []

    # Filter by stage if specified
    if stages_filter:
        code_versions = [
            v for v in code_versions
            if v.get("stage") in stages_filter
        ]

    logger.info(f"Found {len(code_versions)} code versions for {module_name}")

    results = []
    for version in code_versions:
        logger.info(f"\n{'='*60}")
        logger.info(f"Reviewing: {version['name']}")
        logger.info(f"{'='*60}")

        run_result = process_code_version(
            version, spec_path, module_name, logger, max_iterations,
            data_structures_path=data_structures_path,
            guidelines_path=guidelines_path,
            modules_description_path=modules_description_path,
        )
        results.append(run_result)

        # Save individual result immediately
        version_output = output_dir / f"{module_name}_{version['name']}_review.json"
        with open(version_output, 'w', encoding='utf-8') as f:
            json.dump(asdict(run_result), f, indent=2, ensure_ascii=False)

        logger.info(f"Result: {'✅ Success' if run_result.success else '❌ Failed'}")
        logger.info(f"Issues found: {run_result.issue_count}")
        logger.info(f"Duration: {run_result.duration_seconds:.1f}s")

    return results


def save_batch_results(results: list[ReviewRun], output_dir: Path):
    """Save batch review results."""
    # Save all results
    all_results = {
        "batch_timestamp": datetime.now().isoformat(),
        "total_reviews": len(results),
        "successful_reviews": sum(1 for r in results if r.success),
        "total_issues_found": sum(r.issue_count for r in results),
        "reviews": [asdict(r) for r in results]
    }

    output_file = output_dir / "batch_review_results.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Saved batch results to: {output_file}")

    # Print summary
    print("\n" + "=" * 70)
    print("BATCH REVIEW SUMMARY")
    print("=" * 70)
    print(f"Total reviews: {len(results)}")
    print(f"Successful: {all_results['successful_reviews']}")
    print(f"Failed: {len(results) - all_results['successful_reviews']}")
    print(f"Total issues found: {all_results['total_issues_found']}")

    # Per-module summary
    modules = {}
    for r in results:
        if r.module not in modules:
            modules[r.module] = {"success": 0, "failed": 0, "issues": 0}
        if r.success:
            modules[r.module]["success"] += 1
        else:
            modules[r.module]["failed"] += 1
        modules[r.module]["issues"] += r.issue_count

    print("\n📁 Per-module:")
    for mod, stats in modules.items():
        print(f"   {mod}: {stats['success']} success, {stats['failed']} failed, {stats['issues']} issues")

    return all_results


def main():
    parser = argparse.ArgumentParser(description="Batch run code reviews")
    parser.add_argument(
        "--modules",
        nargs="+",
        default=None,
        help="Specific modules to review (e.g., module_M4 module_M5)"
    )
    parser.add_argument(
        "--stages",
        nargs="+",
        default=["before_cr"],  # Focus on before_cr by default
        help="Stages to review: before_cr, after_cr, after_tests, final"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="results/reviews",
        help="Output directory for review results"
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=30,
        help="Maximum iterations for the review agent"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )
    args = parser.parse_args()

    logger = setup_logging(args.verbose)

    # Setup paths
    script_dir = Path(__file__).parent
    test_data_dir = script_dir.parent / "test_data"
    output_dir = script_dir / args.output_dir
    extraction_dir = script_dir / "results" / "extracted"

    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Test data: {test_data_dir}")
    logger.info(f"Output: {output_dir}")
    logger.info(f"Stages: {args.stages}")

    # Check if extraction was done
    if not extraction_dir.exists():
        logger.warning("Extraction data not found. Running extraction first...")
        from experiments.extract_cr_comments import main as run_extraction
        run_extraction()

    # Find modules
    if args.modules:
        module_dirs = [test_data_dir / m for m in args.modules]
        module_dirs = [m for m in module_dirs if m.exists()]
    else:
        module_dirs = sorted([
            d for d in test_data_dir.iterdir()
            if d.is_dir() and d.name.startswith('module_')
        ])

    if not module_dirs:
        logger.error("No module directories found")
        return

    logger.info(f"Processing {len(module_dirs)} modules")

    # Run reviews
    all_results = []
    for module_dir in module_dirs:
        logger.info(f"\n{'#'*70}")
        logger.info(f"MODULE: {module_dir.name}")
        logger.info(f"{'#'*70}")

        results = run_module_reviews(
            module_dir,
            output_dir,
            extraction_dir,
            logger,
            stages_filter=args.stages,
            max_iterations=args.max_iterations,
        )
        all_results.extend(results)

    # Save batch results
    save_batch_results(all_results, output_dir)

    logger.info("\n✅ Batch review complete!")


if __name__ == "__main__":
    main()
