"""Review tools for the LangGraph Reviewer Agent."""

from typing import Any

from openhands.core.logger import openhands_logger as logger

try:
    from langchain_core.tools import StructuredTool

    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False
    StructuredTool = None  # type: ignore


def create_reviewer_tools() -> list[Any]:
    """Create the tools for the reviewer agent.

    Returns:
        List of LangChain tools for code review
    """
    if not LANGCHAIN_AVAILABLE:
        logger.warning('LangChain not available, returning empty tools list')
        return []

    def run_command(command: str) -> dict[str, Any]:
        """Execute a shell command for review purposes.

        Args:
            command: The shell command to execute (e.g., 'pytest', 'git diff', 'pylint')

        Returns:
            Dictionary with command result information
        """
        # This is a placeholder that returns the command to be executed
        # The actual execution will be handled by the agent controller
        return {
            'action': 'run_command',
            'command': command,
            'description': f'Execute command: {command}',
        }

    def read_file(path: str, focus: str = '') -> dict[str, Any]:
        """Read and analyze a file.

        Args:
            path: Path to the file to read
            focus: Optional focus area (e.g., 'imports', 'function: foo', 'lines: 10-20')

        Returns:
            Dictionary with file reading information
        """
        return {
            'action': 'read_file',
            'path': path,
            'focus': focus,
            'description': f'Read file: {path}' + (f' (focus: {focus})' if focus else ''),
        }

    def finish_review(
        summary: str,
        critical_issues: list[str] = None,
        major_issues: list[str] = None,
        minor_issues: list[str] = None,
        test_results: str = '',
        recommendation: str = 'APPROVE',
    ) -> dict[str, Any]:
        """Complete the review with findings.

        Args:
            summary: Brief overview of the review
            critical_issues: List of critical issues found
            major_issues: List of major issues found
            minor_issues: List of minor issues found
            test_results: Results from running tests
            recommendation: Final recommendation (APPROVE, REQUEST_CHANGES, NEEDS_DISCUSSION)

        Returns:
            Dictionary with review completion information
        """
        return {
            'action': 'finish_review',
            'summary': summary,
            'critical_issues': critical_issues or [],
            'major_issues': major_issues or [],
            'minor_issues': minor_issues or [],
            'test_results': test_results,
            'recommendation': recommendation,
            'description': f'Complete review with recommendation: {recommendation}',
        }

    tools = [
        StructuredTool.from_function(
            func=run_command,
            name='run_command',
            description='Execute a shell command to run tests, linters, or other validation tools. Use this to verify code works correctly.',
        ),
        StructuredTool.from_function(
            func=read_file,
            name='read_file',
            description='Read and analyze a specific file in the repository. Use this to examine code in detail.',
        ),
        StructuredTool.from_function(
            func=finish_review,
            name='finish_review',
            description='Complete the code review with your findings, issues, and recommendation. Use this when you have finished analyzing the code.',
        ),
    ]

    return tools

