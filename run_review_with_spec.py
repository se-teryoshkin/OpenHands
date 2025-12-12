#!/usr/bin/env python3
"""Run LangGraph Reviewer Agent with specification file - Enhanced with logging."""

import argparse
import os
import sys
from pathlib import Path
from datetime import datetime
import json

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
    """Reviewer that uses specification file with comprehensive logging."""

    def __init__(
        self,
        code_dir: str,
        spec_file: str,
        model: str = 'gpt-4o',
        max_steps: int = 30,
        run_tests: bool = True,
        output_file: str = 'review_result.md',
    ):
        """Initialize the reviewer.

        Args:
            code_dir: Directory containing code to review
            spec_file: Path to specification markdown file
            model: LLM model to use
            max_steps: Maximum number of steps
            run_tests: Whether to run tests
            output_file: File to save review results
        """
        self.code_dir = Path(code_dir).resolve()
        self.spec_file = Path(spec_file).resolve()
        self.model = model
        self.max_steps = max_steps
        self.run_tests = run_tests
        self.output_file = output_file

        if not self.code_dir.exists():
            raise ValueError(f'Directory not found: {code_dir}')
        if not self.spec_file.exists():
            raise ValueError(f'Specification file not found: {spec_file}')

        # Read specification
        with open(self.spec_file, 'r', encoding='utf-8') as f:
            self.specification = f.read()

        # Initialize event log
        self.events = []
        self.start_time = datetime.now()

        logger.info(f'Code directory: {self.code_dir}')
        logger.info(f'Specification: {self.spec_file}')
        logger.info(f'Output file: {self.output_file}')

        # Change to the code directory
        os.chdir(self.code_dir)

    def log_event(self, event_type: str, data: dict):
        """Log an event with timestamp."""
        event = {
            'timestamp': datetime.now().isoformat(),
            'elapsed': (datetime.now() - self.start_time).total_seconds(),
            'type': event_type,
            'data': data
        }
        self.events.append(event)
        
        # Pretty print the event
        print(f"\n[{event['elapsed']:.2f}s] {event_type.upper()}")
        if event_type == 'tool_call':
            print(f"  Tool: {data.get('tool_name')}")
            args_str = json.dumps(data.get('args', {}), indent=4)
            if len(args_str) > 200:
                args_str = args_str[:200] + '...'
            print(f"  Args: {args_str}")
        elif event_type == 'tool_result':
            result = str(data.get('result', ''))
            preview = result[:200] + '...' if len(result) > 200 else result
            print(f"  Result: {preview}")
        elif event_type == 'message':
            content = data.get('content', '')[:200]
            print(f"  Content: {content}")
        elif event_type == 'error':
            print(f"  Error: {data.get('error')}")

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
            self.log_event('message', {'content': action.content})
            logger.info(f'Agent message: {action.content}')
            return None

        if isinstance(action, CmdRunAction):
            self.log_event('tool_call', {
                'tool_name': 'run_command',
                'args': {'command': action.command}
            })
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

                self.log_event('tool_result', {
                    'tool_name': 'run_command',
                    'exit_code': result.returncode,
                    'result': output[:1000]
                })

                return CmdOutputObservation(
                    command=action.command,
                    exit_code=result.returncode,
                    content=output[:10000],
                )
            except Exception as e:
                logger.error(f'Command error: {e}')
                self.log_event('error', {'error': str(e), 'action': 'run_command'})
                return CmdOutputObservation(
                    command=action.command,
                    exit_code=1,
                    content=f'Error: {str(e)}',
                )

        elif isinstance(action, FileReadAction):
            self.log_event('tool_call', {
                'tool_name': 'read_file',
                'args': {'path': action.path}
            })
            logger.info(f'Reading: {action.path}')
            try:
                file_path = self.code_dir / action.path
                if file_path.exists():
                    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    
                    self.log_event('tool_result', {
                        'tool_name': 'read_file',
                        'path': action.path,
                        'size': len(content),
                        'result': content[:500]
                    })
                    
                    return FileReadObservation(
                        path=action.path,
                        content=content[:50000],
                    )
                else:
                    self.log_event('error', {
                        'error': f'File not found: {action.path}',
                        'action': 'read_file'
                    })
                    return FileReadObservation(
                        path=action.path,
                        content=f'Error: File not found: {action.path}',
                    )
            except Exception as e:
                self.log_event('error', {'error': str(e), 'action': 'read_file'})
                return FileReadObservation(
                    path=action.path,
                    content=f'Error: {str(e)}',
                )

        return None

    def save_review_result(self, outputs: dict):
        """Save review result to markdown file."""
        try:
            with open(self.output_file, 'w', encoding='utf-8') as f:
                f.write(f"# Code Review Report\n\n")
                f.write(f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                f.write(f"**Repository:** `{self.code_dir}`\n\n")
                f.write(f"**Specification:** `{self.spec_file}`\n\n")
                f.write(f"**Model:** {self.model}\n\n")
                f.write(f"**Duration:** {(datetime.now() - self.start_time).total_seconds():.2f}s\n\n")
                f.write(f"---\n\n")
                
                if 'review' in outputs:
                    f.write(outputs['review'])
                else:
                    f.write(f"## Summary\n\n{outputs.get('summary', 'No summary')}\n\n")
                    
                    if outputs.get('critical_issues'):
                        f.write("## Critical Issues\n\n")
                        for issue in outputs['critical_issues']:
                            f.write(f"- {issue}\n")
                        f.write("\n")
                    
                    if outputs.get('major_issues'):
                        f.write("## Major Issues\n\n")
                        for issue in outputs['major_issues']:
                            f.write(f"- {issue}\n")
                        f.write("\n")
                    
                    if outputs.get('minor_issues'):
                        f.write("## Minor Issues\n\n")
                        for issue in outputs['minor_issues']:
                            f.write(f"- {issue}\n")
                        f.write("\n")
                    
                    if outputs.get('test_results'):
                        f.write(f"## Test Results\n\n{outputs['test_results']}\n\n")
                    
                    f.write(f"## Recommendation\n\n**{outputs.get('recommendation', 'UNKNOWN')}**\n\n")
                
                # Add event log summary
                f.write(f"---\n\n## Review Trace\n\n")
                f.write(f"**Total Steps:** {len([e for e in self.events if e['type'] in ['tool_call', 'message']])}\n\n")
                f.write(f"**Tool Calls:**\n")
                tool_counts = {}
                for event in self.events:
                    if event['type'] == 'tool_call':
                        tool = event['data'].get('tool_name', 'unknown')
                        tool_counts[tool] = tool_counts.get(tool, 0) + 1
                for tool, count in tool_counts.items():
                    f.write(f"- {tool}: {count}\n")
            
            logger.info(f'Review result saved to {self.output_file}')
            print(f"\n✅ Review result saved to: {self.output_file}")
        except Exception as e:
            logger.error(f'Failed to save review result: {e}')
            print(f"\n⚠️  Failed to save review result: {e}")

    def run_review(self):
        """Run the review."""
        print('=' * 80)
        print('LangGraph Reviewer Agent - Specification-Based Review')
        print('=' * 80)
        print(f'Repository:    {self.code_dir}')
        print(f'Specification: {self.spec_file}')
        print(f'Model:         {self.model}')
        print(f'Max Steps:     {self.max_steps}')
        print(f'Output:        {self.output_file}')
        print('=' * 80)
        print()
        
        self.log_event('review_start', {
            'code_dir': str(self.code_dir),
            'spec_file': str(self.spec_file),
            'model': self.model,
            'max_steps': self.max_steps
        })

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
                    self.log_event('review_complete', {
                        'steps': step_count,
                        'outputs': action.outputs
                    })
                    
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
                    print(f'Duration: {(datetime.now() - self.start_time).total_seconds():.2f}s')
                    print('=' * 80)
                    
                    # Save review result
                    self.save_review_result(outputs)
                    
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
        outputs = {
            'summary': 'Review incomplete - max steps reached',
            'recommendation': 'INCOMPLETE',
        }
        self.log_event('review_incomplete', {'reason': 'max_steps', 'steps': step_count})
        self.save_review_result(outputs)
        return outputs


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Run code review with specification')
    parser.add_argument('code_dir', help='Code directory to review')
    parser.add_argument('spec_file', help='Specification .md file')
    parser.add_argument('--model', default='gpt-4o', help='LLM model (default: gpt-4o)')
    parser.add_argument('--max-steps', type=int, default=30, help='Max steps (default: 30)')
    parser.add_argument('--no-tests', action='store_true', help='Skip tests')
    parser.add_argument('--output', default='review_result.md', help='Output file (default: review_result.md)')

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
            output_file=args.output,
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
