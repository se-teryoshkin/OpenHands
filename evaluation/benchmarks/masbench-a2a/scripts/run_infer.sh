#!/usr/bin/env bash
set -eo pipefail

source "evaluation/utils/version_control.sh"

AGENT_URL=$1
DATASET_PATH=$2
OUTPUT_DIR=$3

checkout_eval_branch

if [ -z "$AGENT_URL" ]; then
  AGENT_URL="http://127.0.0.1:3000"
  echo "Agent URL not defined, use default $AGENT_URL"
fi

if [ -z "$DATASET_PATH" ]; then
  DATASET_PATH="evaluation/benchmarks/masbench-a2a/data/MasBench.csv"
  echo "Dataset path not specified, use default $DATASET_PATH"
fi

if [ -z "$OUTPUT_DIR" ]; then
  OUTPUT_DIR="evaluation/evaluation_outputs/outputs"
  echo "Output dir not specified, use default $OUTPUT_DIR"
fi


COMMAND="export PYTHONPATH=evaluation/benchmarks/masbench-a2a:\$PYTHONPATH && poetry run python evaluation/benchmarks/masbench-a2a/run_infer.py \
  --agent-url $AGENT_URL \
  --dataset-path $DATASET_PATH \
  --output-dir $OUTPUT_DIR"

# Run the command
eval $COMMAND
