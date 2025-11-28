from litellm import ChatCompletionToolParam, ChatCompletionToolParamFunctionChunk

from openhands.llm.tool_names import TASK_TRACKER_TOOL_NAME

_DETAILED_TASK_TRACKER_DESCRIPTION = """This tool provides structured task management capabilities for development workflows.
It enables systematic tracking of work items, progress monitoring, and efficient
organization of complex development activities.

The tool maintains visibility into project status and helps communicate
progress effectively to users.

## Application Guidelines

Utilize this tool in ALL situations:

1. Task list creation is MANDATORY at the start of every interaction.
   Before performing any other action, you MUST immediately issue a
   `task_tracker` `plan` call that captures the scope of the request.
2. Multi-phase development work - When projects involve multiple sequential or
   parallel activities
3. Complex implementation tasks - Work requiring systematic planning and
   coordination across multiple components
4. Project initiation - Capture and organize user requirements at project start
5. Work commencement - Update task status to in_progress before beginning
   implementation. Maintain focus by limiting active work to one task
6. Task completion - Update status to done and identify any additional work
   that emerged during implementation

## Usage Scenarios

**Scenario A: Feature Development with Validation**
User request: "Build a user authentication system with login/logout functionality.
Don't forget to include input validation and error handling!"

Response approach: I'll implement a user authentication system with comprehensive
validation. Let me organize this work systematically.

*Task breakdown includes:*
1. Design authentication API endpoints and data models
2. Implement user login/logout controllers with session management
3. Add client-side form validation and error display
4. Create backend input sanitization and security checks
5. Implement comprehensive error handling for edge cases
6. Perform integration testing and fix any issues

**Scenario B: Codebase Refactoring**
User request: "I need to update all instances of the method 'fetchData' to
'retrieveInformation' throughout the entire project"

Response approach: Let me first analyze your codebase to identify all locations
where 'fetchData' appears.

*After code analysis*

I've located 12 occurrences of 'fetchData' across 6 files in your project.
I'll create a systematic plan to update these references.

*Organized task list includes specific file-by-file updates*

**Scenario C: Multi-feature Development**
User request: "Set up a blog platform with these components: admin dashboard,
article management, comment system, and user profiles."

Response approach: I'll help build your blog platform. Let me break down these
components into manageable implementation phases.

*Creates structured plan with each feature decomposed into specific development tasks*

## Status Management and Workflow

1. **Status Values**: Track work using these states:
   - todo: Not yet initiated
   - in_progress: Currently active (maintain single focus)
   - done: Successfully completed

2. **Workflow Practices**:
   - Update status dynamically as work progresses
   - Mark completion immediately upon task finish
   - Limit active work to ONE task at any given time
   - Complete current activities before initiating new ones
   - Remove obsolete tasks from tracking entirely

3. **Completion Criteria**:
   - Mark tasks as done only when fully achieved
   - Keep status as in_progress if errors, blocks, or partial completion exist
   - Create new tasks for discovered issues or dependencies
   - Never mark done when:
       - Test suites are failing
       - Implementation remains incomplete
       - Unresolved errors persist
       - Required resources are unavailable

4. **Task Organization**:
   - Write precise, actionable descriptions
   - Decompose complex work into manageable units
   - Use descriptive, clear naming conventions

When uncertain, favor using this tool. Proactive task management demonstrates
systematic approach and ensures comprehensive requirement fulfillment.
"""

_SHORT_TASK_TRACKER_DESCRIPTION = """Provides structured task management for development workflows, enabling progress
tracking and systematic organization of complex coding activities.

* Apply to multi-phase projects (3+ distinct steps) or when managing multiple user requirements
* Update status (todo/in_progress/done) dynamically throughout work
* Maintain single active task focus at any time
* Mark completion immediately upon task finish
* Decompose complex work into manageable, actionable units
"""


def create_task_tracker_tool(
    use_short_description: bool = False,
) -> ChatCompletionToolParam:
    description = (
        _SHORT_TASK_TRACKER_DESCRIPTION
        if use_short_description
        else _DETAILED_TASK_TRACKER_DESCRIPTION
    )
    return ChatCompletionToolParam(
        type='function',
        function=ChatCompletionToolParamFunctionChunk(
            name=TASK_TRACKER_TOOL_NAME,
            description=description,
            parameters={
                'type': 'object',
                'properties': {
                    'command': {
                        'type': 'string',
                        'enum': ['view', 'plan'],
                        'description': 'The command to execute. `view` shows the current task list. `plan` creates or updates the task list based on provided requirements and progress. Always `view` the current list before making changes.',
                    },
                    'task_list': {
                        'type': 'array',
                        'description': 'The full task list. Required parameter of `plan` command.',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'id': {
                                    'type': 'string',
                                    'description': 'Unique task identifier',
                                },
                                'title': {
                                    'type': 'string',
                                    'description': 'Brief task description',
                                },
                                'status': {
                                    'type': 'string',
                                    'description': 'Current task status',
                                    'enum': ['todo', 'in_progress', 'done'],
                                },
                                'notes': {
                                    'type': 'string',
                                    'description': 'Optional additional context or details',
                                },
                            },
                            'required': ['title', 'status', 'id'],
                            'additionalProperties': False,
                        },
                    },
                },
                'required': ['command'],
                'additionalProperties': False,
            },
        ),
    )
