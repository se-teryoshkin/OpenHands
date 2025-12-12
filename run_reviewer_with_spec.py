#!/usr/bin/env python3
"""Standalone script to run LangGraph Reviewer Agent with specification file.

This script runs the LangGraph Reviewer Agent on a repository with a
specification file to guide the review process. The agent will:
- Read the specification from the provided .md file
- Review the codebase in the repository folder
- Compare implementation against the specification
- Provide detailed feedback and recommendations

Usage:
    python run_reviewer_with_spec.py <repo_folder> <spec_file.md> [options]

Example:
    python run_reviewer_with_spec.py ./my-project ./spec.md --model gpt-4o

Requirements:
    - OPENAI_API_KEY or appropriate LLM API key in environment
    - openhands-ai package with langgraph_agent extras installed
"""

import argparse
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from openhands.agenthub.langgraph_reviewer_agent.langgraph_reviewer_agent import (
    LangGraphReviewerAgent,
)
from openhands.controller.state.state import State
from openhands.core.config import AgentConfig, LLMConfig
from openhands.core.logger import openhands_logger as logger
from openhands.core.schema import AgentState
from openhands.events.action import AgentFinishAction
from openhands.events.observation import CmdOutputObservation, FileReadObservation
from openhands.llm.llm_registry import LLMRegistry
from openhands.llm.metrics import Metrics


class SpecBasedReviewRunner:
    """Runner for specification-based code review."""

    def __init__(
        self,
        repo_dir: str,
        spec_file: str,
        model: str = 'gpt-4o',
        max_steps: int = 20,
        run_tests: bool = True,
        verbose: bool = False,
    ):
        """Initialize the review runner.

        Args:
            repo_dir: Repository directory to review
            spec_file: Path to specification markdown file
            model: LLM model to use
            max_steps: Maximum number of agent steps
            run_tests: Whether to run tests during review
            verbose: Enable verbose logging
        """
        self.repo_dir = Path(repo_dir).resolve()
        self.spec_file = Path(spec_file).resolve()
        self.model = model
        self.max_steps = max_steps
        self.run_tests = run_tests
        self.verbose = verbose

        # Validate inputs
        if not self.repo_dir.exists():
            raise ValueError(f'Repository directory not found: {repo_dir}')
        if not self.repo_dir.is_dir():
            raise ValueError(f'Path is not a directory: {repo_dir}')
        if not self.spec_file.exists():
            raise ValueError(f'Specification file not found: {spec_file}')
        if not self.spec_file.suffix == '.md':
            raise ValueError(f'Specification file must be a .md file: {spec_file}')

        # Read specification
        with open(self.spec_file, 'r', encoding='utf-8') as f:
            self.specification = f.read()

        if not self.specification.strip():
            raise ValueError(f'Specification file is empty: {spec_file}')

        logger.info(f'Loaded specification from {self.spec_file} ({len(self.specification)} characters)')

        # Change to the repository directory
        os.chdir(self.repo_dir)
        logger.info(f'Changed working directory to: {self.repo_dir}')

    def create_review_state(self) -> State:
        """Create the initial state for the review.

        Returns:
            State object with specification context
        """
        task_description = f"""Review the codebase in this repository and compare it against the provided specification.

SPECIFICATION:
{self.specification}

REVIEW REQUIREMENTS:
1. Verify that all components mentioned in the specification are implemented
2. Check that the implementation matches the specified requirements
3. Identify any missing features or components
4. Look for bugs, code quality issues, and security concerns
5. {'Run tests to verify functionality' if self.run_tests else 'Skip running tests'}
6. Provide actionable recommendations for improvements

Focus on ensuring the codebase correctly implements the specification.
"""

        state = State(
            session_id=f'review-{self.repo_dir.name}',
            agent_state=AgentState.RUNNING,
            inputs={
                'task': task_description,
                'focus': 'specification compliance and code quality',
                'run_tests': self.run_tests,
                'specification': self.specification,
            },
        )
        return state

    def execute_action(self, action, state: State):
        """Execute an action and return an observation.

        Args:
            action: Action to execute
            state: Current state

        Returns:
            Observation from executing the action
        """
        from openhands.events.action import CmdRunAction, FileReadAction, MessageAction

        if isinstance(action, MessageAction):
            if self.verbose:
                logger.info(f'Agent message: {action.content}')
            return None

        if isinstance(action, CmdRunAction):
            logger.info(f'📝 Executing command: {action.command}')
            try:
                import subprocess

                result = subprocess.run(
                    action.command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    cwd=self.repo_dir,
                )
                output = result.stdout + result.stderr
                exit_code = result.returncode

                if self.verbose:
                    logger.info(f'Command exit code: {exit_code}')
                    logger.info(f'Command output preview: {output[:200]}...')

                obs = CmdOutputObservation(
                    command=action.command,
                    exit_code=exit_code,
                    content=output[:10000],  # Limit output size
                )
                return obs
            except subprocess.TimeoutExpired:
                logger.warning(f'Command timed out: {action.command}')
                return CmdOutputObservation(
                    command=action.command,
                    exit_code=124,
                    content='Error: Command timed out after 60 seconds',
                )
            except Exception as e:
                logger.error(f'Error executing command: {e}')
                return CmdOutputObservation(
                    command=action.command,
                    exit_code=1,
                    content=f'Error: {str(e)}',
                )

        elif isinstance(action, FileReadAction):
            logger.info(f'📄 Reading file: {action.path}')
            try:
                file_path = self.repo_dir / action.path
                if file_path.exists():
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()

                    if self.verbose:
                        logger.info(f'Read {len(content)} characters from {action.path}')

                    return FileReadObservation(
                        path=action.path,
                        content=content[:50000],  # Limit content size
                    )
                else:
                    logger.warning(f'File not found: {file_path}')
                    return FileReadObservation(
                        path=action.path,
                        content=f'Error: File not found: {action.path}',
                    )
            except Exception as e:
                logger.error(f'Error reading file: {e}')
                return FileReadObservation(
                    path=action.path,
                    content=f'Error: {str(e)}',
                )

        return None

    def run_review(self) -> dict:
        """Run the review using the agent.

        Returns:
            Review results dictionary
        """
        print('=' * 80)
        print('🔍 LangGraph Reviewer Agent - Specification-Based Review')
        print('=' * 80)
        print(f'Repository:      {self.repo_dir}')
        print(f'Specification:   {self.spec_file}')
        print(f'Model:           {self.model}')
        print(f'Run Tests:       {self.run_tests}')
        print(f'Max Steps:       {self.max_steps}')
        print('=' * 80)
        print()

        # Create LLM config
        llm_config = LLMConfig(model=self.model)

        # Create agent config
        agent_config = AgentConfig()

        # Create LLM registry
        metrics = Metrics()
        llm_registry = LLMRegistry({'llm': llm_config}, 'llm', metrics)

        # Create the reviewer agent
        logger.info('Creating LangGraphReviewerAgent...')
        agent = LangGraphReviewerAgent(config=agent_config, llm_registry=llm_registry)

        # Create initial state with specification
        state = self.create_review_state()

        # Run the agent
        step_count = 0
        print('Starting review process...\n')

        while step_count < self.max_steps:
            step_count += 1
            print(f'--- Step {step_count}/{self.max_steps} ---')

            try:
                # Get next action from agent
                action = agent.step(state)

                if self.verbose:
                    logger.info(f'Action type: {type(action).__name__}')

                # Check if agent is done
                if isinstance(action, AgentFinishAction):
                    logger.info('✅ Agent finished review')
                    print()
                    print('=' * 80)
                    print('📋 REVIEW RESULTS')
                    print('=' * 80)
                    print()

                    outputs = action.outputs
                    if 'review' in outputs:
                        print(outputs['review'])
                    else:
                        print(f"**Summary:** {outputs.get('summary', 'No summary')}")
                        print(f"\n**Recommendation:** {outputs.get('recommendation', 'UNKNOWN')}")

                        if outputs.get('critical_issues'):
                            print("\n**Critical Issues:**")
                            for issue in outputs['critical_issues']:
                                print(f"  - {issue}")

                        if outputs.get('major_issues'):
                            print("\n**Major Issues:**")
                            for issue in outputs['major_issues']:
                                print(f"  - {issue}")

                        if outputs.get('minor_issues'):
                            print("\n**Minor Issues:**")
                            for issue in outputs['minor_issues']:
                                print(f"  - {issue}")

                    print()
                    print('=' * 80)
                    print(f'📊 Steps completed: {step_count}')
                    print(f'💰 Total cost: ${metrics.accumulated_cost:.4f}')
                    print('=' * 80)
                    return outputs

                # Execute the action
                observation = self.execute_action(action, state)

                if observation:
                    # Add observation to state history
                    state.history.append(observation)

            except KeyboardInterrupt:
                print("\n\n⚠️  Review interrupted by user")
                raise
            except Exception as e:
                logger.error(f'Error in step {step_count}: {e}', exc_info=True)
                print(f"❌ Error: {e}")
                return {
                    'summary': f'Review failed at step {step_count}: {str(e)}',
                    'recommendation': 'ERROR',
                }

        print()
        logger.warning(f'⚠️  Reached maximum steps ({self.max_steps})')
        return {
            'summary': 'Review incomplete - maximum steps reached',
            'recommendation': 'INCOMPLETE',
        }


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Run LangGraph Reviewer Agent with specification file',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage
  python run_reviewer_with_spec.py ./my-repo ./spec.md

  # With custom model and options
  python run_reviewer_with_spec.py ./my-repo ./spec.md --model gpt-4o --max-steps 30

  # Skip tests and enable verbose mode
  python run_reviewer_with_spec.py ./my-repo ./spec.md --no-tests --verbose

Environment:
  Set OPENAI_API_KEY or appropriate LLM API key before running.
        """
    )

    parser.add_argument(
        'repo_dir',
        help='Path to repository directory to review',
    )
    parser.add_argument(
        'spec_file',
        help='Path to specification markdown (.md) file',
    )
    parser.add_argument(
        '--model',
        default='gpt-4o',
        help='LLM model to use (default: gpt-4o)',
    )
    parser.add_argument(
        '--max-steps',
        type=int,
        default=20,
        help='Maximum number of agent steps (default: 20)',
    )
    parser.add_argument(
        '--no-tests',
        action='store_true',
        help='Skip running tests during review',
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging',
    )

    args = parser.parse_args()

    # Check for API key
    if not os.getenv('OPENAI_API_KEY') and 'gpt' in args.model.lower():
        print('❌ Error: OPENAI_API_KEY not set')
        print('Please set your OpenAI API key:')
        print('  export OPENAI_API_KEY=your-api-key')
        sys.exit(1)

    try:
        runner = SpecBasedReviewRunner(
            repo_dir=args.repo_dir,
            spec_file=args.spec_file,
            model=args.model,
            max_steps=args.max_steps,
            run_tests=not args.no_tests,
            verbose=args.verbose,
        )

        results = runner.run_review()

        # Exit with appropriate code based on recommendation
        recommendation = results.get('recommendation', 'UNKNOWN')
        if recommendation == 'APPROVE':
            sys.exit(0)
        elif recommendation in ['REQUEST_CHANGES', 'NEEDS_DISCUSSION']:
            sys.exit(1)
        else:
            sys.exit(2)

    except KeyboardInterrupt:
        print('\n\n⚠️  Review interrupted by user')
        sys.exit(130)
    except Exception as e:
        print(f'\n❌ Error: {e}')
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
