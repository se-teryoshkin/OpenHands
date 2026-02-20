#!/usr/bin/env python3
"""
Stability Test Script for Code Review Agent

Runs the code review agent 10 times and saves each report separately
for stability analysis.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from collections import defaultdict

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from examples.code_review_agent.run_review import run_review_example


def main():
    """Run stability test - execute review 10 times."""
    parser = argparse.ArgumentParser(
        description="Run code review agent 10 times for stability analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--spec",
        type=Path,
        default=PROJECT_ROOT / "test_data" / "module_M5" / "M5.md",
        help="Path to specification file",
    )
    parser.add_argument(
        "--code",
        type=Path,
        default=PROJECT_ROOT / "test_data" / "module_M5" / "M5_run1_before_CR_1.zip",
        help="Path to generated code (zip file or directory)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "examples" / "code_review_agent" / "stability_reports",
        help="Directory to save all reports",
    )
    parser.add_argument(
        "--data-structures",
        type=Path,
        default=PROJECT_ROOT / "test_data" / "api_data_structures.md",
        help="Path to API data structures file",
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
        help="Path to modules description file",
    )
    parser.add_argument(
        "--external-components",
        type=Path,
        default=PROJECT_ROOT / "test_data" / "AppFactory-components",
        help="Path to external components directory",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=10,
        help="Number of runs to execute (default: 10)",
    )

    args = parser.parse_args()

    # Validate inputs
    if not args.spec.exists():
        print(f"❌ Specification file not found: {args.spec}")
        sys.exit(1)

    if not args.code.exists():
        print(f"❌ Code file or directory not found: {args.code}")
        sys.exit(1)

    # Create output directory
    args.output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 80)
    print("STABILITY TEST: Running Code Review Agent Multiple Times")
    print("=" * 80)
    print(f"📋 Specification: {args.spec}")
    print(f"📁 Code: {args.code}")
    print(f"📂 Output directory: {args.output_dir}")
    print(f"🔄 Number of runs: {args.runs}")
    print()

    # Track results
    all_results = []
    times = []
    error_counts = []
    warning_counts = []
    all_issues = defaultdict(list)  # Track issues across runs

    # Run reviews
    for run_num in range(1, args.runs + 1):
        print(f"\n{'=' * 80}")
        print(f"RUN {run_num}/{args.runs}")
        print(f"{'=' * 80}\n")

        # Set unique output files for this run
        output_md = args.output_dir / f"review_report_{run_num}.md"
        output_json = args.output_dir / f"review_report_{run_num}.json"

        # Run the review
        start_time = time.time()
        exit_code = run_review_example(
            spec_path=args.spec,
            code_zip_path=args.code,
            debug=False,
            output_file=output_md,
            data_structures_path=args.data_structures,
            coding_guidelines_path=args.guidelines,
            modules_description_path=args.modules_description,
            external_components_path=args.external_components,
            use_react=False,
            quiet=True,  # Suppress verbose output during batch runs
        )
        elapsed_time = time.time() - start_time

        # Load and analyze the JSON result
        if output_json.exists():
            with open(output_json, 'r', encoding='utf-8') as f:
                result_data = json.load(f)
                all_results.append(result_data)

                # Track statistics
                times.append(elapsed_time)
                error_count = len([c for c in result_data.get('comments', []) if c.get('severity') == 'error'])
                warning_count = len([c for c in result_data.get('comments', []) if c.get('severity') == 'warning'])
                error_counts.append(error_count)
                warning_counts.append(warning_count)

                # Track issues by category and file
                for comment in result_data.get('comments', []):
                    issue_key = (
                        comment.get('category', 'unknown'),
                        comment.get('file_path', 'unknown'),
                        comment.get('line_number'),
                        comment.get('message', '')[:100]  # First 100 chars for comparison
                    )
                    all_issues[issue_key].append(run_num)

        print(f"\n✅ Run {run_num} completed in {elapsed_time:.2f}s")
        print(f"   Saved: {output_md}")
        print(f"   Saved: {output_json}")

    # Generate summary report
    print(f"\n{'=' * 80}")
    print("STABILITY ANALYSIS SUMMARY")
    print(f"{'=' * 80}\n")

    # Calculate statistics
    avg_time = sum(times) / len(times) if times else 0
    min_time = min(times) if times else 0
    max_time = max(times) if times else 0

    # Count consistent issues (appeared in all runs)
    consistent_issues = []
    inconsistent_issues = []
    for issue_key, runs in all_issues.items():
        if len(runs) == args.runs:
            consistent_issues.append((issue_key, runs))
        else:
            inconsistent_issues.append((issue_key, runs, len(runs)))

    print("📊 Execution Statistics:")
    print(f"   Average time: {avg_time:.2f}s")
    print(f"   Min time: {min_time:.2f}s")
    print(f"   Max time: {max_time:.2f}s")
    print(f"   Time std dev: {(sum((t - avg_time)**2 for t in times) / len(times))**0.5:.2f}s" if times else "   Time std dev: 0.00s")
    print()

    print("📈 Issue Consistency:")
    print(f"   Total unique issues found: {len(all_issues)}")
    print(f"   Consistent issues (all {args.runs} runs): {len(consistent_issues)}")
    print(f"   Inconsistent issues: {len(inconsistent_issues)}")
    print()

    # Save summary report
    summary_file = args.output_dir / "stability_summary.md"
    with open(summary_file, 'w', encoding='utf-8') as f:
        f.write("# Stability Test Summary\n\n")
        f.write(f"**Test Configuration:**\n")
        f.write(f"- Specification: `{args.spec}`\n")
        f.write(f"- Code: `{args.code}`\n")
        f.write(f"- Number of runs: {args.runs}\n\n")

        f.write("## Execution Statistics\n\n")
        f.write(f"- Average time: {avg_time:.2f}s\n")
        f.write(f"- Min time: {min_time:.2f}s\n")
        f.write(f"- Max time: {max_time:.2f}s\n")
        f.write(f"- Time std dev: {(sum((t - avg_time)**2 for t in times) / len(times))**0.5:.2f}s\n\n" if times else "- Time std dev: 0.00s\n\n")

        f.write("## Issue Consistency Analysis\n\n")
        f.write(f"- Total unique issues found: {len(all_issues)}\n")
        f.write(f"- Consistent issues (appeared in all {args.runs} runs): {len(consistent_issues)}\n")
        f.write(f"- Inconsistent issues: {len(inconsistent_issues)}\n\n")

        f.write("### Consistent Issues (100% occurrence)\n\n")
        for issue_key, runs in sorted(consistent_issues):
            category, file_path, line_num, message = issue_key
            f.write(f"- **{category}** in `{file_path}`")
            if line_num:
                f.write(f" (line {line_num})")
            f.write(f": {message}\n")
        f.write("\n")

        f.write("### Inconsistent Issues\n\n")
        f.write("| Category | File | Line | Message | Occurrences |\n")
        f.write("|----------|------|------|---------|-------------|\n")
        for issue_key, runs, count in sorted(inconsistent_issues, key=lambda x: -x[2]):
            category, file_path, line_num, message = issue_key
            line_str = str(line_num) if line_num else "N/A"
            f.write(f"| {category} | `{file_path}` | {line_str} | {message[:60]}... | {count}/{args.runs} |\n")
        f.write("\n")

        f.write("## Individual Run Results\n\n")
        for i, result_data in enumerate(all_results, 1):
            passed = result_data.get('passed', True)
            comments = result_data.get('comments', [])
            error_count = len([c for c in comments if c.get('severity') == 'error'])
            warning_count = len([c for c in comments if c.get('severity') == 'warning'])
            f.write(f"### Run {i}\n\n")
            f.write(f"- Status: {'✅ PASSED' if passed else '❌ FAILED'}\n")
            f.write(f"- Errors: {error_count}\n")
            f.write(f"- Warnings: {warning_count}\n")
            f.write(f"- Time: {times[i-1]:.2f}s\n")
            f.write(f"- Report: `review_report_{i}.md`\n\n")

    print(f"📄 Summary saved to: {summary_file}")
    print(f"\n✅ Stability test completed!")
    print(f"   All reports saved in: {args.output_dir}")


if __name__ == "__main__":
    main()
