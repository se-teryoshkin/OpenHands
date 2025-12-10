#!/usr/bin/env python3
"""Standalone test script for the LangGraph Reviewer Agent.

This script allows you to test the reviewer agent on any directory
containing code. It simulates the OpenHands environment to test
the agent's review capabilities.

Usage:
    python test_reviewer_standalone.py /path/to/code/directory

Optional arguments:
    --focus AREA        Focus area for review (e.g., 'security', 'performance')
    --run-tests         Run tests during review (default: True)
    --model MODEL       LLM model to use (default: gpt-4)
    --max-steps N       Maximum number of steps (default: 10)
"""

import argparse
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parents[3]
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


class StandaloneReviewTester:
    """Standalone tester for the reviewer agent."""

    def __init__(
        self,
        code_dir: str,
        model: str = 'gpt-4',
        focus: str = '',
        run_tests: bool = True,
        max_steps: int = 10,
    ):
        """Initialize the standalone tester.

        Args:
            code_dir: Directory containing code to review
            model: LLM model to use
            focus: Focus area for review
            run_tests: Whether to run tests
            max_steps: Maximum number of review steps
        """
        self.code_dir = Path(code_dir).resolve()
        self.model = model
        self.focus = focus
        self.run_tests = run_tests
        self.max_steps = max_steps

        if not self.code_dir.exists():
            raise ValueError(f'Directory not found: {code_dir}')

        # Change to the code directory
        os.chdir(self.code_dir)
        logger.info(f'Changed directory to: {self.code_dir}')

    def create_mock_state(self) -> State:
        """Create a mock state for testing.

        Returns:
            Mock State object
        """
        state = State(
            session_id='test-session',
            agent_state=AgentState.RUNNING,
            inputs={
                'task': f'Review the code in {self.code_dir}',
                'focus': self.focus,
                'run_tests': self.run_tests,
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
        from openhands.events.action import CmdRunAction, FileReadAction

        if isinstance(action, CmdRunAction):
            logger.info(f'Executing command: {action.command}')
            try:
                import subprocess

                result = subprocess.run(
                    action.command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=self.code_dir,
                )
                output = result.stdout + result.stderr
                exit_code = result.returncode
                logger.info(f'Command exit code: {exit_code}')
                logger.info(f'Command output: {output[:500]}...')

                obs = CmdOutputObservation(
                    command=action.command,
                    exit_code=exit_code,
                    content=output,
                )
                return obs
            except Exception as e:
                logger.error(f'Error executing command: {e}')
                return CmdOutputObservation(
                    command=action.command,
                    exit_code=1,
                    content=f'Error: {str(e)}',
                )

        elif isinstance(action, FileReadAction):
            logger.info(f'Reading file: {action.path}')
            try:
                file_path = self.code_dir / action.path
                if file_path.exists():
                    with open(file_path, 'r') as f:
                        content = f.read()
                    logger.info(f'Read {len(content)} characters from {action.path}')
                    return FileReadObservation(
                        path=action.path,
                        content=content,
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

    def run_review(self):
        """Run the review using the agent.

        Returns:
            Review results
        """
        logger.info('='* 60)
        logger.info('Starting LangGraph Reviewer Agent Test')
        logger.info(f'Code Directory: {self.code_dir}')
        logger.info(f'Model: {self.model}')
        logger.info(f'Focus: {self.focus or "general"}')
        logger.info(f'Run Tests: {self.run_tests}')
        logger.info('=' * 60)

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

        # Create initial state
        state = self.create_mock_state()

        # Run the agent
        step_count = 0
        while step_count < self.max_steps:
            step_count += 1
            logger.info(f'\n--- Step {step_count} ---')

            # Get next action from agent
            action = agent.step(state)
            logger.info(f'Action: {type(action).__name__}')

            # Check if agent is done
            if isinstance(action, AgentFinishAction):
                logger.info('Agent finished review')
                logger.info('='* 60)
                logger.info('Review Results:')
                logger.info('='* 60)

                outputs = action.outputs
                if 'review' in outputs:
                    print(outputs['review'])
                else:
                    print(f"Summary: {outputs.get('summary', 'No summary')}")
                    print(f"Recommendation: {outputs.get('recommendation', 'UNKNOWN')}")

                logger.info('='* 60)
                logger.info(f'Total steps: {step_count}')
                logger.info(f'Total LLM calls: {metrics.accumulated_cost}')
                return outputs

            # Execute the action
            observation = self.execute_action(action, state)

            if observation:
                # Add observation to state history
                state.history.append(observation)

        logger.warning(f'Reached maximum steps ({self.max_steps})')
        return {'summary': 'Review incomplete - max steps reached', 'recommendation': 'INCOMPLETE'}


def main():
    """Main entry point for standalone testing."""
    parser = argparse.ArgumentParser(
        description='Test LangGraph Reviewer Agent on a code directory'
    )
    parser.add_argument(
        'code_dir',
        help='Directory containing code to review',
    )
    parser.add_argument(
        '--focus',
        default='',
        help='Focus area for review (e.g., security, performance)',
    )
    parser.add_argument(
        '--run-tests',
        action='store_true',
        default=True,
        help='Run tests during review',
    )
    parser.add_argument(
        '--model',
        default='gpt-4',
        help='LLM model to use (default: gpt-4)',
    )
    parser.add_argument(
        '--max-steps',
        type=int,
        default=10,
        help='Maximum number of review steps (default: 10)',
    )

    args = parser.parse_args()

    # Check for API key
    if not os.getenv('OPENAI_API_KEY') and 'gpt' in args.model.lower():
        logger.error('OPENAI_API_KEY not set. Please set it before running.')
        sys.exit(1)

    try:
        tester = StandaloneReviewTester(
            code_dir=args.code_dir,
            model=args.model,
            focus=args.focus,
            run_tests=args.run_tests,
            max_steps=args.max_steps,
        )
        results = tester.run_review()

        # Exit with appropriate code
        recommendation = results.get('recommendation', 'UNKNOWN')
        if recommendation == 'APPROVE':
            sys.exit(0)
        elif recommendation in ['REQUEST_CHANGES', 'NEEDS_DISCUSSION']:
            sys.exit(1)
        else:
            sys.exit(2)

    except KeyboardInterrupt:
        logger.info('\nReview interrupted by user')
        sys.exit(130)
    except Exception as e:
        logger.error(f'Error during review: {e}', exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()

