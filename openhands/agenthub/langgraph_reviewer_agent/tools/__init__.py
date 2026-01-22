"""LangChain tools for the Code Review Agent."""

from openhands.agenthub.langgraph_reviewer_agent.tools.code_analyzer import (
    extract_signatures_tool,
    extract_field_accesses_tool,
    extract_test_info_tool,
)
from openhands.agenthub.langgraph_reviewer_agent.tools.validators import (
    validate_signatures_tool,
    validate_field_access_tool,
    validate_mapping_tool,
    validate_test_quality_tool,
    validate_structure_tool,
)
from openhands.agenthub.langgraph_reviewer_agent.tools.file_tools import (
    read_file_tool,
    list_files_tool,
    find_python_files_tool,
)
from openhands.agenthub.langgraph_reviewer_agent.tools.report_tool import (
    create_review_comment_tool,
    finalize_review_tool,
)

# All available tools for the agent
ALL_TOOLS = [
    # File operations
    read_file_tool,
    list_files_tool,
    find_python_files_tool,
    # Code analysis
    extract_signatures_tool,
    extract_field_accesses_tool,
    extract_test_info_tool,
    # Validators
    validate_signatures_tool,
    validate_field_access_tool,
    validate_mapping_tool,
    validate_test_quality_tool,
    validate_structure_tool,
    # Reporting
    create_review_comment_tool,
    finalize_review_tool,
]

__all__ = [
    "ALL_TOOLS",
    # File tools
    "read_file_tool",
    "list_files_tool",
    "find_python_files_tool",
    # Analysis tools
    "extract_signatures_tool",
    "extract_field_accesses_tool",
    "extract_test_info_tool",
    # Validators
    "validate_signatures_tool",
    "validate_field_access_tool",
    "validate_mapping_tool",
    "validate_test_quality_tool",
    "validate_structure_tool",
    # Reporting
    "create_review_comment_tool",
    "finalize_review_tool",
]
