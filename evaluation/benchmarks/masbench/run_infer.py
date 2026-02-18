import asyncio
import copy
import os
import shutil
import tempfile
from typing import Any

import pandas as pd
from datasets import load_dataset

from evaluation.utils.shared import (
    EvalMetadata,
    EvalOutput,
    codeact_user_response,
    compatibility_for_eval_history_pairs,
    get_default_sandbox_config_for_eval,
    get_metrics,
    get_openhands_config_for_eval,
    make_metadata,
    prepare_dataset,
    reset_logger_for_multiprocessing,
    run_evaluation,
)
from openhands.controller.state.state import State
from openhands.core.config import (
    OpenHandsConfig,
    get_llm_config_arg,
    load_from_toml,
    parse_arguments,
)
from openhands.core.logger import openhands_logger as logger
from openhands.core.main import create_runtime, run_controller
from openhands.events.action import CmdRunAction, MessageAction
from openhands.events.observation import CmdOutputObservation
from openhands.runtime.base import Runtime
from openhands.utils.async_utils import call_async_from_sync

# Configure visibility of unit tests to the Agent.
USE_UNIT_TESTS = os.environ.get('USE_UNIT_TESTS', 'false').lower() == 'true'
SKIP_NUM = os.environ.get('SKIP_NUM')
SKIP_NUM = (
    int(SKIP_NUM) if SKIP_NUM and SKIP_NUM.isdigit() and int(SKIP_NUM) >= 0 else None
)


def get_config(
    metadata: EvalMetadata,
) -> OpenHandsConfig:
    sandbox_config = get_default_sandbox_config_for_eval()
    sandbox_config.base_container_image = 'python:3.11-bookworm'
    config = get_openhands_config_for_eval(
        metadata=metadata,
        sandbox_config=sandbox_config,
        runtime=os.environ.get('RUNTIME', 'docker'),
    )
    config.set_llm_config(metadata.llm_config)
    agent_config = config.get_agent_config(metadata.agent_class)
    agent_config.enable_prompt_extensions = False

    # copy 'draft_editor' config if exists
    config_copy = copy.deepcopy(config)
    load_from_toml(config_copy)
    if 'draft_editor' in config_copy.llms:
        config.set_llm_config(config_copy.llms['draft_editor'], 'draft_editor')

    return config


def initialize_runtime(
    runtime: Runtime,
    instance: pd.Series,
):
    """Initialize the runtime for the agent.

    This function is called before the runtime is used to run the agent.
    """
    logger.info(f'\n{"-" * 50} BEGIN Runtime Initialization Fn {"-" * 50}\n')
    obs: CmdOutputObservation

    # INSTALL LIBS
    if os.path.exists(f"{os.path.dirname(__file__)}/inner-requirements.txt"):
        runtime.copy_to(
            f"{os.path.dirname(__file__)}/inner-requirements.txt",
            '/',
        )
        action = CmdRunAction(command='pip install -r /inner-requirements.txt')
        logger.info(action, extra={'msg_type': 'ACTION'})
        obs = runtime.run_action(action)
        assert obs.exit_code == 0, obs.content

    # Set instance id
    action = CmdRunAction(command='mkdir -p /workspace')
    logger.info(action, extra={'msg_type': 'ACTION'})
    obs = runtime.run_action(action)
    assert obs.exit_code == 0

    action = CmdRunAction(command='cd /workspace')
    logger.info(action, extra={'msg_type': 'ACTION'})
    obs = runtime.run_action(action)
    assert obs.exit_code == 0

    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = os.path.join(tmpdir, f'{instance.instance_id}.py')
        with open(file_path, 'w') as f:
            f.write(instance.instruction)
        runtime.copy_to(
            file_path,
            '/workspace',
        )
        if USE_UNIT_TESTS:
            file_path = os.path.join(tmpdir, f'{instance.instance_id}_test.py')
            with open(file_path, 'w') as f:
                f.write(instance.test)
            runtime.copy_to(
                file_path,
                '/workspace',
            )

    logger.info(f'\n{"-" * 50} END Runtime Initialization Fn {"-" * 50}\n')


def complete_runtime(
    runtime: Runtime,
    instance: pd.Series,
) -> dict[str, Any]:
    """Complete the runtime for the agent.

    This function is called before the runtime is used to run the agent.
    If you need to do something in the sandbox to get the correctness metric after
    the agent has run, modify this function.
    """
    logger.info(f'\n{"-" * 50} BEGIN Runtime Completion Fn {"-" * 50}\n')
    obs: CmdOutputObservation

    # Rewriting the test file to ignore any changes Agent may have made.
    script_name = f'{instance.instance_id}_test.py'
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = os.path.join(tmpdir, script_name)
        with open(file_path, 'w') as f:
            f.write(instance.test)
        runtime.copy_to(
            file_path,
            '/workspace',
        )
        logger.info(f'Running test file: {script_name}')

    action = CmdRunAction(command=f'python3 -m unittest {script_name}')
    logger.info(action, extra={'msg_type': 'ACTION'})
    obs = runtime.run_action(action)
    logger.info(obs, extra={'msg_type': 'OBSERVATION'})

    exit_code = 1
    if isinstance(obs, CmdOutputObservation):
        exit_code = obs.exit_code

    logger.info(f'\n{"-" * 50} END Runtime Completion Fn {"-" * 50}\n')

    # get metric from inside the container
    metric_data = [("result.txt", obs.content[: obs.content.find('\n')]), ("log.txt", obs.content)]
    for metric in metric_data:
        file_name = metric[0]
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, file_name)
            with open(file_path, 'w') as f:
                f.write(metric[1])
            runtime.copy_to(
                file_path,
                '/workspace',
            )

    # copy data to host machine
    zip_data = runtime.copy_from('/workspace')
    local_dir = os.path.dirname(__file__)
    persistent_zip = os.path.join(local_dir, f'{instance.instance_id}.zip')
    with open(zip_data, 'rb') as src, open(persistent_zip, 'wb') as dst:
        dst.write(src.read())
    zip_file = persistent_zip

    runtime.close()

    return {
        'test_output': obs.content,
        'exit_code': exit_code,
        'zip_file': zip_file,
    }


def process_instance(
    instance: pd.Series,
    metadata: EvalMetadata,
    reset_logger: bool = True,
) -> EvalOutput:
    config = get_config(metadata)

    # Setup the logger properly, so you can run multi-processing to parallelize the evaluation
    if reset_logger:
        log_dir = os.path.join(metadata.eval_output_dir, 'infer_logs')
        reset_logger_for_multiprocessing(logger, str(instance.instance_id), log_dir)
    else:
        logger.info(
            f'\nStarting evaluation for instance {str(instance.instance_id)}.\n'
        )

    # =============================================
    # build instruction
    # =============================================

    # Prepare instruction
    logger.info(instance)
    instruction = instance.instruction
    instruction += (
        '\n'
        '####\n'
        '\n'
        f'Use the above instructions to modify the supplied files: {instance.instance_id}.py\n'
        "Don't change the names of existing functions or classes, as they may be referenced from other code like unit tests, etc.\n"
        '\n'
        'Use standard python libraries if possible, or use libraries provided in the task statement.\n'
    )

    if USE_UNIT_TESTS:
        logger.info(
            f'\nInstruction to run test_file: {instance.instance_id}_test.py\n'
        )
        instruction += (
            f'Use `python -m unittest {instance.instance_id}_test.py` to run the test_file '
            'and verify the correctness of your solution. DO NOT EDIT the test file.\n\n'
        )

    instruction += (
        'IMPORTANT: You should ONLY interact with the environment provided '
        'to you AND NEVER ASK FOR HUMAN HELP.\n'
    )
    # NOTE: You can actually set slightly different instruction for different agents
    instruction += (
        'REMEMBER: All edits must be made directly in the files. '
        'Do NOT send the edited file as output to the user.\n'
    )

    # =============================================
    # create sandbox and run the agent
    # =============================================

    runtime: Runtime = create_runtime(config)
    call_async_from_sync(runtime.connect)
    initialize_runtime(runtime, instance=instance)

    # Here's how you can run the agent (similar to the `main` function) and get the final task state
    state: State | None = asyncio.run(
        run_controller(
            config=config,
            initial_user_action=MessageAction(content=instruction),
            runtime=runtime,
            fake_user_response_fn=codeact_user_response,
        )
    )
    if state is None:
        raise ValueError('State should not be None.')

    # # =============================================
    # # result evaluation
    # # =============================================

    return_val = complete_runtime(runtime, instance)
    exit_code = return_val['exit_code']
    test_output = return_val['test_output']

    # Copy data to host
    zip_file = return_val['zip_file']

    instance_name = instance.instance_id
    zip_dest = os.path.join(
        metadata.eval_output_dir, 'tasks', instance_name, f'{instance_name}.zip'
    )

    os.makedirs(os.path.dirname(zip_dest), exist_ok=True)
    os.rename(zip_file, zip_dest)
    shutil.unpack_archive(zip_dest, os.path.dirname(zip_dest))
    os.remove(zip_dest)

    errors = []
    test_cases = None
    if test_output.find('SyntaxError') != -1:
        errors += 'SyntaxError'
    elif test_output.find('IndentationError') != -1:
        errors += 'IndentationError'
    else:
        test_cases = test_output[: test_output.find('\r')]

    test_result = {
        'exit_code': exit_code,
        'test_cases': test_cases,
        'errors': errors,
    }

    # history is now available as a stream of events, rather than list of pairs of (Action, Observation)
    # for compatibility with the existing output format, we can remake the pairs here
    # remove when it becomes unnecessary
    histories = compatibility_for_eval_history_pairs(state.history)
    metrics = get_metrics(state)

    # Save the output
    output = EvalOutput(
        instance_id=str(instance.instance_id),
        instance=instance.to_dict(),
        instruction=instruction,
        metadata=metadata,
        history=histories,
        metrics=metrics,
        error=state.last_error if state and state.last_error else None,
        test_result=test_result,
    )

    return output


if __name__ == '__main__':
    args = parse_arguments()
    dataset = load_dataset('csv', data_files=f'{os.path.dirname(os.path.abspath(__file__))}/data/MasBench.csv')
    dataset = dataset.data["train"].to_pandas()

    llm_config = None
    if args.llm_config:
        llm_config = get_llm_config_arg(args.llm_config)
        # modify_params must be False for evaluation purpose, for reproducibility and accurancy of results
        llm_config.modify_params = False

    if llm_config is None:
        raise ValueError(f'Could not find LLM config: --llm_config {args.llm_config}')

    metadata = make_metadata(
        llm_config,
        'MAS_bench',
        args.agent_cls,
        args.max_iterations,
        getattr(args, 'eval_note', None),
        getattr(args, 'eval_output_dir', 'evaluation/evaluation_outputs/outputs'),
    )
    output_file = os.path.join(metadata.eval_output_dir, 'output.jsonl')

    # Parse dataset IDs if provided
    eval_ids = None
    if getattr(args, 'eval_ids', None):
        eval_ids = str(args.eval_ids).split(',')
        logger.info(f'\nUsing specific dataset IDs: {eval_ids}\n')

    instances = prepare_dataset(
        dataset,
        output_file,
        getattr(args, 'eval_n_limit', 100),
        eval_ids=eval_ids,
        skip_num=SKIP_NUM,
    )

    run_evaluation(
        instances,
        metadata,
        output_file,
        getattr(args, 'eval_num_workers', 1),
        process_instance,
    )
