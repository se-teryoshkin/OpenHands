#!/usr/bin/env python3
"""
Compare Code Review Agent Results with Human Feedback.

This script compares the automated review agent's findings with
human code review comments extracted from trace files.

Usage:
    python compare_results.py [--output-dir RESULTS_DIR]
"""

import json
import re
import argparse
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Optional
from collections import defaultdict


# Category mapping for comparison (normalize different naming conventions)
CATEGORY_ALIASES = {
    # Agent categories -> Human categories mapping
    "signature_mismatch": ["signature_mismatch", "interface", "сигнатур"],
    "field_access_error": ["field_access_error", "nonexistent_field", "не существует"],
    "field_mapping_error": ["field_mapping_error", "mapping", "маппинг", "конверт"],
    "mapping_incomplete": ["mapping_incomplete", "mapping", "маппинг"],
    "test_quality": ["test_quality", "testing", "мок", "mock", "тест", "hasattr"],
    "structure_issue": ["structure_issue", "file", "директор", "файл", "structure"],
    "missing_implementation": ["missing_implementation", "отсутствует", "missing"],
    "type_error": ["type_error", "тип", "type"],
    "pydantic_issue": ["pydantic_issue", "pydantic", "модел", "model"],
    "general": ["general", "unknown", "other"],
}


def normalize_category(category: str) -> str:
    """Normalize category name for comparison."""
    category_lower = category.lower().replace("_", " ")

    for normalized, aliases in CATEGORY_ALIASES.items():
        for alias in aliases:
            if alias.lower() in category_lower:
                return normalized

    return "general"


def extract_key_terms(text: str) -> set:
    """Extract key technical terms from text for matching."""
    # Common patterns to extract
    patterns = [
        r'`([^`]+)`',  # Code in backticks
        r'(\w+Service)',  # Service classes
        r'(\w+Model)',  # Model classes
        r'(get_\w+|set_\w+|create_\w+)',  # Method names
        r'(area|salary|work_format|employment)',  # Common fields
        r'(pydantic|mock|hasattr|interface)',  # Technical terms
    ]

    terms = set()
    for pattern in patterns:
        for match in re.findall(pattern, text, re.IGNORECASE):
            if isinstance(match, tuple):
                terms.update(m.lower() for m in match if m)
            else:
                terms.add(match.lower())

    # Also add significant words
    words = re.findall(r'\b(\w{4,})\b', text.lower())
    significant = [
        'signature', 'interface', 'mapping', 'field', 'return', 'type',
        'pydantic', 'model', 'test', 'mock', 'missing', 'vacancy',
        'маппинг', 'сигнатур', 'модел', 'поле', 'тип', 'тест'
    ]
    for word in words:
        if word in significant:
            terms.add(word)

    return terms


@dataclass
class MatchResult:
    """Result of matching an agent issue to human comment."""
    agent_issue: dict
    human_comment: Optional[dict]
    match_score: float
    match_reason: str


@dataclass
class ComparisonResult:
    """Comparison result for a single code version."""
    module: str
    code_version: str
    cr_round: int
    agent_issue_count: int
    human_comment_count: int
    matched_count: int
    agent_only_count: int
    human_only_count: int
    precision: float  # matched / agent_issues
    recall: float  # matched / human_comments
    f1_score: float
    matched_issues: list
    agent_only_issues: list
    human_only_comments: list
    category_breakdown: dict


def calculate_similarity(issue1: dict, issue2: dict) -> tuple[float, str]:
    """Calculate similarity score between agent issue and human comment."""
    score = 0.0
    reasons = []

    # Category match (40%)
    cat1 = normalize_category(issue1.get("category", ""))
    cat2 = normalize_category(issue2.get("category", ""))
    if cat1 == cat2:
        score += 0.4
        reasons.append(f"category_match:{cat1}")
    elif cat1 in CATEGORY_ALIASES.get(cat2, []) or cat2 in CATEGORY_ALIASES.get(cat1, []):
        score += 0.2
        reasons.append("partial_category_match")

    # Term overlap (40%)
    text1 = issue1.get("message", "") + " " + issue1.get("description", "")
    text2 = issue2.get("description", "") + " " + issue2.get("raw_text", "")

    terms1 = extract_key_terms(text1)
    terms2 = extract_key_terms(text2)

    if terms1 and terms2:
        overlap = len(terms1 & terms2)
        union = len(terms1 | terms2)
        jaccard = overlap / union if union > 0 else 0
        score += 0.4 * jaccard
        if overlap > 0:
            reasons.append(f"term_overlap:{overlap}")

    # Specific pattern matches (20%)
    patterns = [
        (r'get_vacancy', 0.1),
        (r'signature|сигнатур', 0.05),
        (r'pydantic|модел', 0.05),
        (r'mapping|маппинг', 0.05),
        (r'area|work_format|salary|employment', 0.05),
        (r'mock|мок', 0.05),
        (r'hasattr', 0.1),
    ]

    for pattern, pts in patterns:
        if re.search(pattern, text1, re.I) and re.search(pattern, text2, re.I):
            score += pts
            reasons.append(f"pattern:{pattern.split('|')[0]}")

    return min(score, 1.0), " + ".join(reasons)


def match_issues_to_comments(
    agent_issues: list[dict],
    human_comments: list[dict],
    threshold: float = 0.3
) -> tuple[list[MatchResult], list[dict], list[dict]]:
    """Match agent issues to human comments."""
    matched = []
    agent_only = []
    used_human_indices = set()

    # Try to match each agent issue
    for issue in agent_issues:
        best_match = None
        best_score = 0
        best_reason = ""
        best_idx = -1

        for idx, comment in enumerate(human_comments):
            if idx in used_human_indices:
                continue

            score, reason = calculate_similarity(issue, comment)
            if score > best_score and score >= threshold:
                best_match = comment
                best_score = score
                best_reason = reason
                best_idx = idx

        if best_match:
            matched.append(MatchResult(
                agent_issue=issue,
                human_comment=best_match,
                match_score=best_score,
                match_reason=best_reason
            ))
            used_human_indices.add(best_idx)
        else:
            agent_only.append(issue)

    # Remaining human comments that weren't matched
    human_only = [c for i, c in enumerate(human_comments) if i not in used_human_indices]

    return matched, agent_only, human_only


def compare_module_version(
    module: str,
    code_version: str,
    agent_review: dict,
    human_data: dict,
    cr_round: int = 1
) -> ComparisonResult:
    """Compare agent review with human comments for a specific version."""
    agent_issues = agent_review.get("issues", [])

    # Filter human comments by CR round (if applicable)
    human_comments = [
        c for c in human_data.get("comments", [])
        if c.get("cr_round", 1) <= cr_round
    ]

    # Match issues
    matched, agent_only, human_only = match_issues_to_comments(
        agent_issues, human_comments
    )

    # Calculate metrics
    matched_count = len(matched)
    precision = matched_count / len(agent_issues) if agent_issues else 0
    recall = matched_count / len(human_comments) if human_comments else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    # Category breakdown
    category_breakdown = defaultdict(lambda: {"agent": 0, "human": 0, "matched": 0})

    for issue in agent_issues:
        cat = normalize_category(issue.get("category", ""))
        category_breakdown[cat]["agent"] += 1

    for comment in human_comments:
        cat = normalize_category(comment.get("category", ""))
        category_breakdown[cat]["human"] += 1

    for m in matched:
        cat = normalize_category(m.agent_issue.get("category", ""))
        category_breakdown[cat]["matched"] += 1

    return ComparisonResult(
        module=module,
        code_version=code_version,
        cr_round=cr_round,
        agent_issue_count=len(agent_issues),
        human_comment_count=len(human_comments),
        matched_count=matched_count,
        agent_only_count=len(agent_only),
        human_only_count=len(human_only),
        precision=precision,
        recall=recall,
        f1_score=f1,
        matched_issues=[asdict(m) for m in matched],
        agent_only_issues=agent_only,
        human_only_comments=human_only,
        category_breakdown=dict(category_breakdown)
    )


def load_results(results_dir: Path) -> dict:
    """Load all results from the results directory."""
    data = {
        "extracted": {},
        "reviews": {}
    }

    # Load extracted CR data
    extracted_dir = results_dir / "extracted"
    if extracted_dir.exists():
        for f in extracted_dir.glob("module_*_cr_extracted.json"):
            with open(f, 'r', encoding='utf-8') as fp:
                module_data = json.load(fp)
                module_name = module_data.get("module_name", f.stem)
                data["extracted"][module_name] = module_data

    # Load review results
    reviews_dir = results_dir / "reviews"
    if reviews_dir.exists():
        for f in reviews_dir.glob("module_*_review.json"):
            with open(f, 'r', encoding='utf-8') as fp:
                review_data = json.load(fp)
                key = f.stem.replace("_review", "")
                data["reviews"][key] = review_data

    return data


def run_comparison(results_dir: Path, output_dir: Path) -> dict:
    """Run comparison between agent reviews and human feedback."""
    data = load_results(results_dir)

    if not data["extracted"]:
        print("No extracted CR data found. Run extract_cr_comments.py first.")
        return {}

    if not data["reviews"]:
        print("No review results found. Run run_batch_review.py first.")
        return {}

    comparisons = []

    # Match reviews to extracted data
    for review_key, review_data in data["reviews"].items():
        module_name = review_data.get("module", "")

        # Find corresponding extracted data
        human_data = data["extracted"].get(module_name, {})

        if not human_data:
            print(f"No human data for {module_name}, skipping")
            continue

        # Determine CR round from code version
        code_version = review_data.get("code_version", "")
        cr_round = review_data.get("cr_round", 1) or 1

        comparison = compare_module_version(
            module=module_name,
            code_version=code_version,
            agent_review=review_data,
            human_data=human_data,
            cr_round=cr_round
        )
        comparisons.append(comparison)

    # Aggregate results
    total_agent = sum(c.agent_issue_count for c in comparisons)
    total_human = sum(c.human_comment_count for c in comparisons)
    total_matched = sum(c.matched_count for c in comparisons)

    overall_precision = total_matched / total_agent if total_agent > 0 else 0
    overall_recall = total_matched / total_human if total_human > 0 else 0
    overall_f1 = 2 * overall_precision * overall_recall / (overall_precision + overall_recall) if (overall_precision + overall_recall) > 0 else 0

    results = {
        "comparison_timestamp": datetime.now().isoformat(),
        "summary": {
            "total_reviews_compared": len(comparisons),
            "total_agent_issues": total_agent,
            "total_human_comments": total_human,
            "total_matched": total_matched,
            "overall_precision": overall_precision,
            "overall_recall": overall_recall,
            "overall_f1": overall_f1
        },
        "comparisons": [asdict(c) for c in comparisons]
    }

    # Save results
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "comparison_results.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"Saved comparison results to: {output_file}")

    return results


def print_comparison_report(results: dict):
    """Print a human-readable comparison report."""
    print("\n" + "=" * 70)
    print("CODE REVIEW AGENT vs HUMAN FEEDBACK COMPARISON")
    print("=" * 70)

    summary = results.get("summary", {})
    print(f"\n📊 OVERALL METRICS:")
    print(f"   Reviews compared: {summary.get('total_reviews_compared', 0)}")
    print(f"   Agent issues: {summary.get('total_agent_issues', 0)}")
    print(f"   Human comments: {summary.get('total_human_comments', 0)}")
    print(f"   Matched: {summary.get('total_matched', 0)}")
    print(f"\n   Precision: {summary.get('overall_precision', 0):.2%}")
    print(f"   Recall: {summary.get('overall_recall', 0):.2%}")
    print(f"   F1 Score: {summary.get('overall_f1', 0):.2%}")

    print("\n📁 PER-VERSION BREAKDOWN:")
    for comp in results.get("comparisons", []):
        print(f"\n   {comp['module']} / {comp['code_version']} (CR round {comp['cr_round']}):")
        print(f"      Agent: {comp['agent_issue_count']} | Human: {comp['human_comment_count']} | Matched: {comp['matched_count']}")
        print(f"      Precision: {comp['precision']:.2%} | Recall: {comp['recall']:.2%} | F1: {comp['f1_score']:.2%}")

        if comp['matched_issues']:
            print(f"      ✅ Matched issues:")
            for m in comp['matched_issues'][:3]:  # Show first 3
                score = m['match_score']
                reason = m['match_reason']
                print(f"         - {m['agent_issue'].get('category', 'N/A')} (score: {score:.2f}, {reason})")

        if comp['agent_only_issues']:
            print(f"      🔵 Agent-only ({len(comp['agent_only_issues'])}):")
            for issue in comp['agent_only_issues'][:2]:  # Show first 2
                msg = issue.get('message', '')[:60]
                print(f"         - [{issue.get('category', 'N/A')}] {msg}...")

        if comp['human_only_comments']:
            print(f"      🟡 Human-only ({len(comp['human_only_comments'])}):")
            for comment in comp['human_only_comments'][:2]:  # Show first 2
                desc = comment.get('description', '')[:60]
                print(f"         - [{comment.get('category', 'N/A')}] {desc}...")

    # Category analysis
    print("\n🏷️  CATEGORY ANALYSIS:")
    category_totals = defaultdict(lambda: {"agent": 0, "human": 0, "matched": 0})
    for comp in results.get("comparisons", []):
        for cat, counts in comp.get("category_breakdown", {}).items():
            category_totals[cat]["agent"] += counts.get("agent", 0)
            category_totals[cat]["human"] += counts.get("human", 0)
            category_totals[cat]["matched"] += counts.get("matched", 0)

    for cat, counts in sorted(category_totals.items()):
        precision = counts["matched"] / counts["agent"] if counts["agent"] > 0 else 0
        recall = counts["matched"] / counts["human"] if counts["human"] > 0 else 0
        print(f"   {cat:25} A:{counts['agent']:2} H:{counts['human']:2} M:{counts['matched']:2} P:{precision:.0%} R:{recall:.0%}")

    print("\n" + "=" * 70)
    print("INTERPRETATION:")
    print("=" * 70)

    precision = summary.get('overall_precision', 0)
    recall = summary.get('overall_recall', 0)

    if precision > 0.7:
        print("✅ High Precision: Agent issues are usually valid (low false positives)")
    elif precision > 0.4:
        print("⚠️ Medium Precision: Some agent issues may be false positives")
    else:
        print("❌ Low Precision: Many agent issues are false positives")

    if recall > 0.7:
        print("✅ High Recall: Agent catches most human-identified issues")
    elif recall > 0.4:
        print("⚠️ Medium Recall: Agent misses some issues humans catch")
    else:
        print("❌ Low Recall: Agent misses many issues humans catch")

    # Specific recommendations
    print("\n💡 RECOMMENDATIONS:")

    # Check which categories have low recall
    for cat, counts in category_totals.items():
        if counts["human"] > 0:
            recall = counts["matched"] / counts["human"]
            if recall < 0.5 and counts["human"] >= 2:
                print(f"   - Improve detection for '{cat}' (only {recall:.0%} recall)")

    # Check which categories agent over-reports
    for cat, counts in category_totals.items():
        if counts["agent"] > counts["human"] * 2 and counts["agent"] >= 3:
            print(f"   - Reduce false positives for '{cat}' (agent: {counts['agent']}, human: {counts['human']})")


def main():
    parser = argparse.ArgumentParser(description="Compare agent vs human code reviews")
    parser.add_argument(
        "--results-dir",
        type=str,
        default="results",
        help="Directory containing extracted and review results"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="results/comparison",
        help="Output directory for comparison results"
    )
    args = parser.parse_args()

    script_dir = Path(__file__).parent
    results_dir = script_dir / args.results_dir
    output_dir = script_dir / args.output_dir

    print(f"Results directory: {results_dir}")
    print(f"Output directory: {output_dir}")

    results = run_comparison(results_dir, output_dir)

    if results:
        print_comparison_report(results)


if __name__ == "__main__":
    main()
