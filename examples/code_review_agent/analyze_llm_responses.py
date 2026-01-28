#!/usr/bin/env python3
"""
Diagnostic script to analyze LLM responses and identify why file_path is None.

This script analyzes the actual LLM responses to understand:
1. What format the LLM is returning
2. Why file_path might be null
3. What validators are producing issues without file paths
"""

import json
import sys
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def analyze_stability_reports():
    """Analyze stability reports to find patterns in file_path issues."""
    reports_dir = PROJECT_ROOT / "examples" / "code_review_agent" / "stability_reports"

    if not reports_dir.exists():
        print(f"❌ Reports directory not found: {reports_dir}")
        return

    # Find all JSON reports
    json_reports = sorted(reports_dir.glob("review_report_*.json"))

    print("=" * 80)
    print("ANALYZING LLM RESPONSE ISSUES")
    print("=" * 80)
    print()

    failed_parses = []
    successful_parses = []
    file_path_issues = defaultdict(int)

    for report_file in json_reports:
        with open(report_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        summary = data.get('summary', '')

        # Check if this is a failed parse
        if 'Failed to parse review output' in summary:
            failed_parses.append((report_file.name, summary))
            print(f"❌ {report_file.name}: Parse failed")
            print(f"   Error: {summary[:200]}...")
            print()
        else:
            successful_parses.append(report_file.name)
            # Check for issues with null file_path
            comments = data.get('comments', [])
            for i, comment in enumerate(comments):
                if comment.get('file_path') is None:
                    file_path_issues[comment.get('category', 'unknown')] += 1
                    print(f"⚠️  {report_file.name}: Issue {i} has null file_path")
                    print(f"   Category: {comment.get('category')}")
                    print(f"   Message: {comment.get('message', '')[:100]}...")
                    print()

    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total reports: {len(json_reports)}")
    print(f"Failed parses: {len(failed_parses)}")
    print(f"Successful parses: {len(successful_parses)}")
    print(f"Issues with null file_path: {sum(file_path_issues.values())}")
    print()

    if file_path_issues:
        print("Categories with null file_path:")
        for category, count in sorted(file_path_issues.items(), key=lambda x: -x[1]):
            print(f"  - {category}: {count}")
        print()

    if failed_parses:
        print("Failed parse details:")
        for report_name, error in failed_parses:
            print(f"  - {report_name}")
            # Extract issue indices from error
            import re
            issue_indices = re.findall(r'issues\.(\d+)\.file_path', error)
            if issue_indices:
                print(f"    Issues with null file_path: {', '.join(issue_indices)}")
        print()

    return failed_parses, file_path_issues


def analyze_validator_outputs():
    """Analyze what validators produce and check for missing file paths."""
    print("=" * 80)
    print("ANALYZING VALIDATOR OUTPUTS")
    print("=" * 80)
    print()

    # Check validators that might not always have file_path
    validators_to_check = [
        'validate_signatures_tool',
        'validate_test_quality_tool',
        'validate_code_quality_tool',
        'validate_pydantic_usage_tool',
        'validate_project_structure_tool',
        'validate_cross_file_usage_tool',
    ]

    print("Checking validator implementations for file_path handling...")
    print()

    validators_file = PROJECT_ROOT / "openhands" / "agenthub" / "langgraph_reviewer_agent" / "tools" / "validators.py"

    if not validators_file.exists():
        print(f"❌ Validators file not found: {validators_file}")
        return

    with open(validators_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # Check each validator for how it structures issues
    print("Validator issue structure analysis:")
    print()

    # Look for issue dictionaries
    import re

    # Check for issues that might not have 'file' field
    patterns = [
        (r'"file":\s*([^,\n}]+)', 'Has "file" field'),
        (r'"file_path":\s*([^,\n}]+)', 'Has "file_path" field'),
        (r'"type":\s*"([^"]+)"', 'Issue type'),
    ]

    # Check signature validator
    sig_match = re.search(r'def validate_signatures_tool.*?return json\.dumps\(({.*?})\)', content, re.DOTALL)
    if sig_match:
        print("validate_signatures_tool:")
        result_str = sig_match.group(1)
        if '"file"' in result_str or '"file_path"' in result_str:
            print("  ✓ Returns file information")
        else:
            print("  ⚠️  May not return file information")
        print()

    # Check test quality validator
    test_match = re.search(r'def validate_test_quality_tool.*?return json\.dumps\(({.*?})\)', content, re.DOTALL)
    if test_match:
        print("validate_test_quality_tool:")
        result_str = test_match.group(1)
        if '"file"' in result_str or '"file_path"' in result_str:
            print("  ✓ Returns file information")
        else:
            print("  ⚠️  May not return file information")
        print()

    print("Note: Full analysis requires examining actual validator outputs.")
    print()


def check_prompt_instructions():
    """Check what the prompt tells the LLM about file_path."""
    print("=" * 80)
    print("CHECKING PROMPT INSTRUCTIONS")
    print("=" * 80)
    print()

    agent_file = PROJECT_ROOT / "openhands" / "agenthub" / "langgraph_reviewer_agent" / "structured_agent.py"

    if not agent_file.exists():
        print(f"❌ Agent file not found: {agent_file}")
        return

    with open(agent_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # Find the prompt section about file_path
    import re
    prompt_match = re.search(r'3\. \*\*file_path\*\*:.*?(?=\n4\.|\n\*\*CATEGORY)', content, re.DOTALL)

    if prompt_match:
        print("Current prompt instruction for file_path:")
        print(prompt_match.group(0))
        print()

    # Check if file_path is marked as required
    if 'file_path.*str.*Field' in content or 'file_path: str' in content:
        print("✓ file_path is defined as required (str, not Optional[str])")
    else:
        print("⚠️  file_path definition not found or may be optional")
    print()


if __name__ == "__main__":
    print()
    print("🔍 DIAGNOSTIC ANALYSIS: file_path = None Issue")
    print()

    # Run analyses
    failed_parses, file_path_issues = analyze_stability_reports()
    analyze_validator_outputs()
    check_prompt_instructions()

    print("=" * 80)
    print("RECOMMENDATIONS")
    print("=" * 80)
    print()
    print("Based on the analysis above, consider:")
    print("1. Making file_path optional in ReviewIssue model (with fallback)")
    print("2. Adding validation/sanitization in _parse_llm_response")
    print("3. Improving prompt instructions to emphasize file_path is required")
    print("4. Adding fallback logic to extract file_path from validator output")
    print("5. Logging actual LLM responses for debugging")
    print()
