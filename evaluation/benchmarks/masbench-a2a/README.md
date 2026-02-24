# MasBench Evaluation

## Setup Environment and LLM Configuration

Please follow instruction [here](../../README.md#setup) to setup your local
development environment and LLM.

## Start the evaluation

```bash
./evaluation/benchmarks/masbench-a2a/scripts/run_infer.sh [agent_url] [dataset_path] [output_dir]
```

- `agent_url`, e.g. `"http://127.0.0.1:3000"`, is the address where the agent's
    API is accessable. Defaults to `"http://127.0.0.1:3000"`.
- `dataset_path`, e.g. `/home/user/dataset.csv`, is the path to the dataset for testing.
    Defaults to `evaluation/evaluation_outputs/outputs`
- `output_dir`, is the path to the output directory. Defaults to `evaluation/evaluation_outputs/outputs`.

Following is the basic command to start the evaluation.

```bash
./evaluation/benchmarks/masbench-a2a/scripts/run_infer.sh "http://127.0.0.1:3000" "evaluation/benchmarks/masbench-a2a/data/MasBench.csv" "evaluation/evaluation_outputs/outputs"
```
