# MasBench Evaluation

## Setup Environment and LLM Configuration

Please follow instruction [here](../../README.md#setup) to setup your local
development environment and LLM.

## Start the evaluation

```bash
./evaluation/benchmarks/masbench/scripts/run_infer.sh [model_config] [agent] [max_iterations]
```

- `model_config`, e.g. `eval_gpt4_1106_preview`, is the config group name for
    your LLM settings, as defined in your `config.toml`.
- `agent`, e.g. `CodeActAgent`, is the name of the agent for benchmarks,
    defaulting to `CodeActAgent`.
- `max_iterations`, e.g. `10`, limits the number of iterations that the agent has
    to improve their code.

There are also following optional environment variables you can set:

```bash
export USE_UNIT_TESTS=true # if you want to allow the Agent to verify correctness using unittests. Default to false.
```

Following is the basic command to start the evaluation.

You can update the arguments in the script
`evaluation/benchmarks/masbench/scripts/run_infer.sh`, such as `--max-iterations`,
`--eval-num-workers` and so on:

- `--agent-cls`, the agent to use. For example, `CodeActAgent`.
- `--llm-config`: the LLM configuration to use. For example, `eval_gpt4_1106_preview`.
- `--max-iterations`: the number of iterations that the agent has to improve their code.

```bash
./evaluation/benchmarks/masbench/scripts/run_infer.sh eval_gpt35_turbo CodeActAgent 10
```

## Summarize Results

```bash
poetry run python ./evaluation/benchmarks/masbench/scripts/summarize_results.py [path_to_output_jsonl_file]
```

Full example:

```bash
poetry run python ./evaluation/benchmarks/masbench/scripts/summarize_results.py evaluation/evaluation_outputs/outputs/MAS_bench/CodeActAgent/claude-3-5-sonnet@20240620_maxiter_30_N_v1.9/output.jsonl
```

This will list the instances that passed and the instances that failed. For each
instance, the corresponding set of test cases (which can vary for each instance)
are run on the file edited by the agent. We consider an instance to be passed
only if ALL test cases are passed. Sometimes even a single failed test case will
cause the entire instance to be marked as failed.

You can inspect the `test_results` field in the `output.jsonl` file to find the exact
outcome of the tests. If there are no syntax or indentation errors, you can
expect to see something like "`..F...EF..`", where "`.`" means the test case
passed, "`E`" means there was an error while executing the test case and "`F`"
means some assertion failed and some returned output was not as expected.
