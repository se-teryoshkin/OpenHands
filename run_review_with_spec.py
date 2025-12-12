#!/usr/bin/env python3
"""Run LangGraph Reviewer Agent with specification file."""

import argparse
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

# Import directly to avoid browsing_agent dependency issues
import importlib.util
spec = importlib.util.spec_from_file_location(
    "langgraph_reviewer_agent",
    project_root / "openhands/agenthub/langgraph_reviewer_agent/langgraph_reviewer_agent.py"
)
langgraph_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(langgraph_module)
LangGraphReviewerAgent = langgraph_module.LangGraphReviewerAgent

from openhands.controller.state.state import State
from openhands.core.config import AgentConfig, LLMConfig
from openhands.core.logger import openhands_logger as logger
from openhands.core.schema import AgentState
from openhands.events.action import AgentFinishAction
from openhands.events.observation import CmdOutputObservation, FileReadObservation
from openhands.llm.llm_registry import LLMRegistry
from openhands.llm.metrics import Metrics


class SpecBasedReviewer:
    """Reviewer that uses specification file."""

    def __init__(
        self,
        code_dir: str,
        spec_file: str,
        model: str = 'gpt-4o',
        max_steps: int = 30,
        run_tests: bool = True,
    ):
        """Initialize the reviewer.

        Args:
            code_dir: Directory containing code to review
            spec_file: Path to specification markdown file
            model: LLM model to use
            max_steps: Maximum number of steps
            run_tests: Whether to run tests
        """
        self.code_dir = Path(code_dir).resolve()
        self.spec_file = Path(spec_file).resolve()
        self.model = model
        self.max_steps = max_steps
        self.run_tests = run_tests

        if not self.code_dir.exists():
            raise ValueError(f'Directory not found: {code_dir}')
        if not self.spec_file.exists():
            raise ValueError(f'Specification file not found: {spec_file}')

        # Read specification
        with open(self.spec_file, 'r', encoding='utf-8') as f:
            self.specification = f.read()

        logger.info(f'Code directory: {self.code_dir}')
        logger.info(f'Specification: {self.spec_file}')

        # Change to the code directory
        os.chdir(self.code_dir)

    def create_review_state(self) -> State:
        """Create state with specification."""
        task = f"""Review the codebase and compare it against the specification.

SPECIFICATION:
{self.specification}

REQUIREMENTS:
1. Verify all modules from specification are implemented
2. Check implementation matches specified requirements
3. Identify missing features or components
4. Look for bugs and code quality issues
5. {'Run tests if available' if self.run_tests else 'Skip tests'}

Focus on specification compliance and code quality."""

        state = State(
            session_id='review-session',
            agent_state=AgentState.RUNNING,
            inputs={
                'task': task,
                'focus': 'specification compliance and code quality',
                'run_tests': self.run_tests,
                'specification': self.specification,
            },
        )
        return state

    def execute_action(self, action, state: State):
        """Execute action and return observation."""
        from openhands.events.action import CmdRunAction, FileReadAction, MessageAction

        if isinstance(action, MessageAction):
            logger.info(f'Agent message: {action.content}')
            return None

        if isinstance(action, CmdRunAction):
            logger.info(f'Executing: {action.command}')
            try:
                import subprocess

                result = subprocess.run(
                    action.command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    cwd=self.code_dir,
                )
                output = result.stdout + result.stderr
                logger.info(f'Exit code: {result.returncode}')

                return CmdOutputObservation(
                    command=action.command,
                    exit_code=result.returncode,
                    content=output[:10000],
                )
            except Exception as e:
                logger.error(f'Command error: {e}')
                return CmdOutputObservation(
                    command=action.command,
                    exit_code=1,
                    content=f'Error: {str(e)}',
                )

        elif isinstance(action, FileReadAction):
            logger.info(f'Reading: {action.path}')
            try:
                file_path = self.code_dir / action.path
                if file_path.exists():
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    return FileReadObservation(
                        path=action.path,
                        content=content[:50000],
                    )
                else:
                    return FileReadObservation(
                        path=action.path,
                        content=f'Error: File not found: {action.path}',
                    )
            except Exception as e:
                return FileReadObservation(
                    path=action.path,
                    content=f'Error: {str(e)}',
                )

        return None

    def run_review(self):
        """Run the review."""
        print('=' * 80)
        print('LangGraph Reviewer Agent - Specification-Based Review')
        print('=' * 80)
        print(f'Repository:    {self.code_dir}')
        print(f'Specification: {self.spec_file}')
        print(f'Model:         {self.model}')
        print(f'Max Steps:     {self.max_steps}')
        print('=' * 80)
        print()

        # Import OpenHandsConfig
        from openhands.core.config import OpenHandsConfig

        # Create LLM config
        llm_config = LLMConfig(model=self.model)

        # Create OpenHands config
        config = OpenHandsConfig(
            llms={'llm': llm_config},
            agents={'agent': AgentConfig()},
            default_agent='langgraph_reviewer_agent',
        )

        # Create LLM registry
        llm_registry = LLMRegistry(config)

        # Create agent
        logger.info('Creating LangGraphReviewerAgent...')
        agent = LangGraphReviewerAgent(config=config.agents['agent'], llm_registry=llm_registry)

        # Create state
        state = self.create_review_state()

        # Run review
        step_count = 0
        print('Starting review...\n')

        while step_count < self.max_steps:
            step_count += 1
            print(f'--- Step {step_count}/{self.max_steps} ---')

            try:
                # Get action
                action = agent.step(state)
                logger.info(f'Action: {type(action).__name__}')

                # Debug: Print action details
                if hasattr(action, 'outputs'):
                    logger.info(f'Action outputs: {action.outputs}')
                if hasattr(action, 'content'):
                    logger.info(f'Action content: {action.content}')

                # Check if done
                if isinstance(action, AgentFinishAction):
                    print('\n' + '=' * 80)
                    print('REVIEW RESULTS')
                    print('=' * 80 + '\n')

                    outputs = action.outputs
                    if 'review' in outputs:
                        print(outputs['review'])
                    else:
                        print(f"Summary: {outputs.get('summary', 'No summary')}")
                        print(f"\nRecommendation: {outputs.get('recommendation', 'UNKNOWN')}")

                        if outputs.get('critical_issues'):
                            print("\nCritical Issues:")
                            for issue in outputs['critical_issues']:
                                print(f"  - {issue}")

                        if outputs.get('major_issues'):
                            print("\nMajor Issues:")
                            for issue in outputs['major_issues']:
                                print(f"  - {issue}")

                        if outputs.get('minor_issues'):
                            print("\nMinor Issues:")
                            for issue in outputs['minor_issues']:
                                print(f"  - {issue}")

                    print('\n' + '=' * 80)
                    print(f'Steps: {step_count}')
                    print('=' * 80)
                    return outputs

                # Execute action
                observation = self.execute_action(action, state)

                if observation:
                    state.history.append(observation)

            except KeyboardInterrupt:
                print("\n\nInterrupted by user")
                raise
            except Exception as e:
                logger.error(f'Error in step {step_count}: {e}', exc_info=True)
                return {
                    'summary': f'Review failed: {str(e)}',
                    'recommendation': 'ERROR',
                }

        print(f'\nReached max steps ({self.max_steps})')
        return {
            'summary': 'Review incomplete - max steps reached',
            'recommendation': 'INCOMPLETE',
        }


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Run code review with specification')
    parser.add_argument('code_dir', help='Code directory to review')
    parser.add_argument('spec_file', help='Specification .md file')
    parser.add_argument('--model', default='gpt-4o', help='LLM model (default: gpt-4o)')
    parser.add_argument('--max-steps', type=int, default=30, help='Max steps (default: 30)')
    parser.add_argument('--no-tests', action='store_true', help='Skip tests')

    args = parser.parse_args()

    # Check API key
    if not os.getenv('OPENAI_API_KEY') and 'gpt' in args.model.lower():
        print('Error: OPENAI_API_KEY not set')
        sys.exit(1)

    try:
        reviewer = SpecBasedReviewer(
            code_dir=args.code_dir,
            spec_file=args.spec_file,
            model=args.model,
            max_steps=args.max_steps,
            run_tests=not args.no_tests,
        )

        results = reviewer.run_review()

        # Exit based on recommendation
        recommendation = results.get('recommendation', 'UNKNOWN')
        if recommendation == 'APPROVE':
            sys.exit(0)
        elif recommendation in ['REQUEST_CHANGES', 'NEEDS_DISCUSSION']:
            sys.exit(1)
        else:
            sys.exit(2)

    except KeyboardInterrupt:
        print('\nInterrupted')
        sys.exit(130)
    except Exception as e:
        print(f'\nError: {e}')
        sys.exit(1)


if __name__ == '__main__':
    main()
