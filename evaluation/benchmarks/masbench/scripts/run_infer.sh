#!/usr/bin/env bash
set -eo pipefail

source "evaluation/utils/version_control.sh"

MODEL_CONFIG=$1
AGENT=$2
MAX_ITERATIONS=$3

checkout_eval_branch

if [ -z "$MAX_ITERATIONS" ]; then
  MAX_ITERATIONS=10
  echo "Number of iterations not specified, use default $MAX_ITERATIONS"
fi

if [ -z "$AGENT" ]; then
  echo "Agent not specified, use default CodeActAgent"
  AGENT="CodeActAgent"
fi

get_openhands_version

echo "AGENT: $AGENT"
echo "OPENHANDS_VERSION: $OPENHANDS_VERSION"
echo "MODEL_CONFIG: $MODEL_CONFIG"

EVAL_NOTE=$OPENHANDS_VERSION

# Default to NOT use unit tests.
if [ -z "$USE_UNIT_TESTS" ]; then
  export USE_UNIT_TESTS=false
fi
echo "USE_UNIT_TESTS: $USE_UNIT_TESTS"
# If use unit tests, set EVAL_NOTE to the commit hash
if [ "$USE_UNIT_TESTS" = true ]; then
  EVAL_NOTE=$EVAL_NOTE-w-test
fi

COMMAND="export PYTHONPATH=evaluation/benchmarks/masbench:\$PYTHONPATH && poetry run python evaluation/benchmarks/masbench/run_infer.py \
  --agent-cls $AGENT \
  --llm-config $MODEL_CONFIG \
  --max-iterations $MAX_ITERATIONS"

# Run the command
eval $COMMAND
