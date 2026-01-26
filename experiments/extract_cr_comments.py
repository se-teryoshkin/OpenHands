#!/usr/bin/env python3
"""
Extract Code Review Comments from Trace Files.

This script parses trace files from OpenHands agent sessions and extracts
human code review feedback for comparison with automated review agent.

Usage:
    python extract_cr_comments.py [--output-dir RESULTS_DIR]
"""

import json
import re
import os
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional
from datetime import datetime
import argparse


@dataclass
class CRComment:
    """Represents a single code review comment from human reviewer."""
    module: str
    cr_round: int
    issue_number: int
    category: str
    description: str
    raw_text: str
    timestamp: str = ""
    message_id: int = 0


@dataclass
class ModuleCRData:
    """All CR data for a single module."""
    module_name: str
    spec_file: str
    total_cr_rounds: int
    comments: list
    code_versions: list  # List of available code zip files


def categorize_issue(text: str) -> str:
    """Categorize a code review issue based on its description."""
    text_lower = text.lower()

    # Signature/Interface issues (Russian and English)
    if any(kw in text_lower for kw in [
        'сигнатур', 'signature', 'интерфейс', 'interface',
        'не совпадает', 'doesn\'t match', 'return type'
    ]):
        return "signature_mismatch"

    # Data model / Pydantic issues
    if any(kw in text_lower for kw in [
        'pydantic', 'модел', 'model', 'basemodel', 'dataclass'
    ]):
        return "pydantic_issue"

    # Field mapping issues
    if any(kw in text_lower for kw in [
        'маппинг', 'mapping', 'конверт', 'convert', 'не задаются поля',
        'field mapping', 'конвертац'
    ]):
        return "field_mapping_error"

    # Nonexistent field access
    if any(kw in text_lower for kw in [
        'не существует', 'does not exist', 'нет поля', 'этого поля',
        'no such field', 'отсутствует поле'
    ]):
        return "field_access_error"

    # File organization / structure
    if any(kw in text_lower for kw in [
        'файл', 'file', 'директори', 'directory', 'корне',
        'структур', 'structure', 'организац'
    ]):
        return "structure_issue"

    # Testing with mocks (forbidden)
    if any(kw in text_lower for kw in [
        'мок', 'mock', 'мокир', 'недопустим', 'magicmock'
    ]):
        return "test_quality"

    # Test coverage / quality
    if any(kw in text_lower for kw in [
        'тест', 'test', 'покрыт', 'coverage', 'hasattr',
        'проверка существовани', 'функциональност'
    ]):
        return "test_quality"

    # Missing implementation
    if any(kw in text_lower for kw in [
        'отсутствует', 'missing', 'не реализ', 'not implemented',
        'нет реализац'
    ]):
        return "missing_implementation"

    # Type errors
    if any(kw in text_lower for kw in [
        'тип', 'type', 'typing', 'optional', 'none'
    ]):
        return "type_error"

    return "general"


def extract_individual_issues(message: str) -> list[tuple[int, str, str]]:
    """Extract individual issues from a CR message that may contain multiple issues.

    Returns list of (issue_number, description, category) tuples.
    """
    issues = []

    # Pattern for numbered issues: "1. ...", "2. ...", etc.
    numbered_pattern = r'(\d+)\.\s+([^\n\d]+(?:\n(?!\d+\.).*)*)'
    matches = re.findall(numbered_pattern, message, re.MULTILINE)

    if matches:
        for num_str, issue_text in matches:
            issue_text = issue_text.strip()
            if issue_text and len(issue_text) > 10:  # Filter out very short matches
                category = categorize_issue(issue_text)
                issues.append((int(num_str), issue_text, category))
    else:
        # No numbered issues, treat the whole message as one issue
        # Try splitting by newlines if multiple sentences
        lines = [l.strip() for l in message.split('\n') if l.strip() and len(l.strip()) > 20]
        for i, line in enumerate(lines, 1):
            category = categorize_issue(line)
            issues.append((i, line, category))

    return issues


def load_trace_file(filepath: str) -> list[dict]:
    """Load a JSONL trace file."""
    events = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return events


def is_cr_message(message: str) -> bool:
    """Check if a message is ACTUAL code review feedback (not initial instructions)."""
    message_lower = message.lower()

    # Must contain clear CR indicators
    cr_required_indicators = [
        'в ходе код-ревью',  # "during code review"
        'в ходе code-review',
        'code review feedback',
        'были выявлены следующие проблемы',  # "the following issues were found"
        'были обнаружены следующие проблемы',
        'cr feedback',
        'review comments',
    ]

    # Exclude initial instructions (not actual CR feedback)
    exclusion_patterns = [
        'ты - очень опытный',  # Initial instruction header
        'детально проанализир',  # "analyze in detail" - initial task
        'тщательно продумай',  # "think carefully" - initial task
        'реализуй по согласно',  # "implement according to plan"
        'retrieving content for',  # System message, not CR
    ]

    # Exclude if it's an initial instruction
    for pattern in exclusion_patterns:
        if pattern in message_lower:
            return False

    # Must have a clear CR indicator
    return any(indicator in message_lower for indicator in cr_required_indicators)


def extract_cr_comments(trace_file: Path, module_name: str) -> list[CRComment]:
    """Extract code review feedback comments from a trace file."""
    events = load_trace_file(str(trace_file))
    comments = []
    cr_round = 0

    for event in events:
        if event.get('source') == 'user':
            message = event.get('message', '')

            # Check if this is a CR feedback message
            if is_cr_message(message):
                cr_round += 1
                message_id = event.get('id', 0)
                timestamp = event.get('timestamp', '')

                # Extract individual issues from the message
                individual_issues = extract_individual_issues(message)

                for issue_num, description, category in individual_issues:
                    comment = CRComment(
                        module=module_name,
                        cr_round=cr_round,
                        issue_number=issue_num,
                        category=category,
                        description=description,
                        raw_text=message[:500],  # Truncate for storage
                        timestamp=timestamp,
                        message_id=message_id
                    )
                    comments.append(comment)

    return comments


def find_code_versions(module_dir: Path) -> list[dict]:
    """Find all code versions (zip files and directories) in module directory."""
    versions = []

    # Find zip files
    for zip_file in sorted(module_dir.glob("*.zip")):
        version_info = {
            "path": str(zip_file),
            "name": zip_file.stem,
            "type": "zip"
        }

        # Determine the stage of this version
        name_lower = zip_file.stem.lower()
        if 'before_cr' in name_lower:
            version_info["stage"] = "before_cr"
            # Extract CR round number
            match = re.search(r'before_cr_(\d+)', name_lower)
            version_info["cr_round"] = int(match.group(1)) if match else 1
        elif 'after_cr' in name_lower:
            version_info["stage"] = "after_cr"
            match = re.search(r'after_cr_(\d+)', name_lower)
            version_info["cr_round"] = int(match.group(1)) if match else 1
        elif 'after_tests' in name_lower:
            version_info["stage"] = "after_tests"
            # Check if before or after CR
            if 'before_cr' in name_lower:
                version_info["substage"] = "before_cr"
            elif 'after_cr' in name_lower:
                version_info["substage"] = "after_cr"
                match = re.search(r'after_cr_(\d+)', name_lower)
                version_info["cr_round"] = int(match.group(1)) if match else 1
        elif 'final' in name_lower:
            version_info["stage"] = "final"
        else:
            version_info["stage"] = "unknown"

        versions.append(version_info)

    # Find extracted directories (already unzipped code)
    for subdir in module_dir.iterdir():
        if subdir.is_dir() and not subdir.name.startswith('.'):
            # Check if it's a code directory
            if any(subdir.glob('**/*.py')):
                version_info = {
                    "path": str(subdir),
                    "name": subdir.name,
                    "type": "directory"
                }
                name_lower = subdir.name.lower()
                if 'before_cr' in name_lower:
                    version_info["stage"] = "before_cr"
                    match = re.search(r'before_cr_(\d+)', name_lower)
                    version_info["cr_round"] = int(match.group(1)) if match else 1
                elif 'after_cr' in name_lower:
                    version_info["stage"] = "after_cr"
                    match = re.search(r'after_cr_(\d+)', name_lower)
                    version_info["cr_round"] = int(match.group(1)) if match else 1
                else:
                    version_info["stage"] = "unknown"
                versions.append(version_info)

    return versions


def analyze_module(module_dir: Path) -> Optional[ModuleCRData]:
    """Analyze a single module directory and extract all CR data."""
    module_name = module_dir.name

    # Find trace files - prefer run trace over specs_analysis
    trace_files = list(module_dir.glob("*_trace.json")) + list(module_dir.glob("*.json"))

    # Look for the main run trace (not specs_analysis)
    main_trace = None
    specs_trace = None
    for tf in trace_files:
        if 'specs_analysis' in tf.name:
            specs_trace = tf
        elif tf.suffix == '.json':
            main_trace = tf

    # Use main trace if available, otherwise specs trace
    trace_to_use = main_trace or specs_trace

    if not trace_to_use:
        print(f"No trace file found for {module_name}")
        return None

    # Extract CR comments
    comments = extract_cr_comments(trace_to_use, module_name)

    # Count CR rounds
    cr_rounds = len(set(c.cr_round for c in comments)) if comments else 0

    # Find spec file
    spec_files = list(module_dir.glob("M*.md"))
    spec_file = str(spec_files[0]) if spec_files else ""

    # Find code versions
    code_versions = find_code_versions(module_dir)

    return ModuleCRData(
        module_name=module_name,
        spec_file=spec_file,
        total_cr_rounds=cr_rounds,
        comments=[asdict(c) for c in comments],
        code_versions=code_versions
    )


def save_extraction_results(data: list[ModuleCRData], output_dir: Path):
    """Save extraction results to JSON files."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save per-module data
    for module_data in data:
        output_file = output_dir / f"{module_data.module_name}_cr_extracted.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(asdict(module_data), f, indent=2, ensure_ascii=False)
        print(f"Saved: {output_file}")

    # Save combined summary
    summary = {
        "extraction_timestamp": datetime.now().isoformat(),
        "total_modules": len(data),
        "total_cr_comments": sum(len(m.comments) for m in data),
        "total_cr_rounds": sum(m.total_cr_rounds for m in data),
        "modules": [
            {
                "name": m.module_name,
                "cr_rounds": m.total_cr_rounds,
                "comment_count": len(m.comments),
                "code_versions_count": len(m.code_versions),
                "categories": list(set(c["category"] for c in m.comments))
            }
            for m in data
        ],
        "category_distribution": {}
    }

    # Count categories across all modules
    category_counts = {}
    for m in data:
        for c in m.comments:
            cat = c["category"]
            category_counts[cat] = category_counts.get(cat, 0) + 1
    summary["category_distribution"] = category_counts

    summary_file = output_dir / "extraction_summary.json"
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\nSaved summary: {summary_file}")

    return summary


def print_summary(data: list[ModuleCRData]):
    """Print extraction summary to console."""
    print("\n" + "=" * 70)
    print("CODE REVIEW COMMENTS EXTRACTION SUMMARY")
    print("=" * 70)

    total_comments = sum(len(m.comments) for m in data)
    total_rounds = sum(m.total_cr_rounds for m in data)

    print(f"\nModules analyzed: {len(data)}")
    print(f"Total CR rounds: {total_rounds}")
    print(f"Total CR comments: {total_comments}")

    print("\n📁 Per-module breakdown:")
    for m in data:
        print(f"   {m.module_name}:")
        print(f"      CR rounds: {m.total_cr_rounds}")
        print(f"      Comments: {len(m.comments)}")
        print(f"      Code versions: {len(m.code_versions)}")

        # Show code versions
        for v in m.code_versions:
            stage = v.get("stage", "unknown")
            cr_round = v.get("cr_round", "")
            print(f"        - {v['name']} [{stage}] {f'(CR #{cr_round})' if cr_round else ''}")

    # Category distribution
    print("\n🏷️  Category distribution:")
    category_counts = {}
    for m in data:
        for c in m.comments:
            cat = c["category"]
            category_counts[cat] = category_counts.get(cat, 0) + 1

    for cat, count in sorted(category_counts.items(), key=lambda x: -x[1]):
        pct = (count / total_comments * 100) if total_comments > 0 else 0
        bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        print(f"   {cat:25} {bar} {count:3} ({pct:5.1f}%)")


def main():
    parser = argparse.ArgumentParser(description="Extract CR comments from trace files")
    parser.add_argument(
        "--output-dir",
        type=str,
        default="results/extracted",
        help="Output directory for extracted data (relative to experiments/)"
    )
    parser.add_argument(
        "--test-data-dir",
        type=str,
        default=None,
        help="Path to test_data directory"
    )
    args = parser.parse_args()

    # Find directories
    script_dir = Path(__file__).parent

    if args.test_data_dir:
        test_data_dir = Path(args.test_data_dir)
    else:
        test_data_dir = script_dir.parent / "test_data"

    output_dir = script_dir / args.output_dir

    print(f"Test data directory: {test_data_dir}")
    print(f"Output directory: {output_dir}")

    # Find module directories
    module_dirs = sorted([
        d for d in test_data_dir.iterdir()
        if d.is_dir() and d.name.startswith('module_')
    ])

    if not module_dirs:
        print(f"No module directories found in {test_data_dir}")
        return

    print(f"\nFound {len(module_dirs)} module directories")

    # Analyze each module
    results = []
    for module_dir in module_dirs:
        print(f"\nAnalyzing {module_dir.name}...")
        data = analyze_module(module_dir)
        if data:
            results.append(data)

    # Save results
    summary = save_extraction_results(results, output_dir)

    # Print summary
    print_summary(results)

    print("\n✅ Extraction complete!")


if __name__ == "__main__":
    main()
