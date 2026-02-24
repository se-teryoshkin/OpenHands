import asyncio
import argparse
from io import BytesIO
import os
from typing import Any
import zipfile

import pandas as pd
import requests
import docker
from a2a.client import ClientConfig
from agents import RemoteA2AAgent, RemoteA2AAgentResponse


class InferenceRunner:

    def __init__(self, dataset_path: str, agent_url: str, output_dir: str):
        self.dataset_path = dataset_path
        self.agent_url = agent_url
        self.output_dir = output_dir
        self.data = None
        self.agent = None
        self.events = []

    def pre_run_functions(self) -> None:
        pass

    def post_run_functions(self) -> None:
        pass

    def _callback(self, event_message: Any) -> None:
        self.events.append(event_message)

    def load_data(self) -> None:
        self.data = pd.read_csv(self.dataset_path)

    def setup_agent(self) -> None:
        self.agent = RemoteA2AAgent(
            well_known_url=self.agent_url,
            agent_id="OpenHands-CodeActAgent",
            default_metadata={
                "openhands/agent": "CodeActAgent",
                "openhands/show-all-events": True,
                "openhands/auto-continue": True,
            },
            config=ClientConfig(
                streaming=False,
                polling=True,
            ),
            events_callback=self._callback,
        )

    def _download_code(self, context_id: str) -> bytes:
        response = requests.get(
            f"{self.agent_url}/api/conversations/{context_id}/zip-directory"
        )
        if response.status_code != 200:
            raise ValueError(
                f"Error downloading code: {response.status_code} status code. "
                f"Content: {response.content}"
            )
        return response.content

    async def process_task(self, entry: pd.Series) -> None:
        result: RemoteA2AAgentResponse = await self.agent.send_message(
            message_text=(
                f"Make an implementation of the following function in a run.py file.\n"
                f"If you think you have solved the task, please finish the interaction. "
                f"IMPORTANT: YOU SHOULD NOT ASK FOR HUMAN RESPONSE UNTIL USER CONTACT YOU HIMSELF. "
                f"The function:\n {entry.instruction}\n"
            ),
            message_id=f"0{entry.instance_id}",
        )

        context_id = result.response.get("task", {}).get("contextId", None)
        if not context_id:
            print(f"Предупреждение: для задачи {entry.instance_id} не получен context_id")
            return

        client = docker.from_env()
        container = client.containers.get("openhands-runtime-" + context_id)

        test_str = entry.test.replace('"', "'")

        container.exec_run(
            f'bash -c "cd /workspace && cat > testUnittest.py << \'EOF\'\n{test_str}\nEOF\n"'
        )

        container.exec_run(
            'bash -c "/openhands/micromamba/envs/openhands/bin/python3 -m unittest testUnittest.py 2> logUnittest.txt"',
            workdir='/workspace'
        )

        container.exec_run(
            'bash -c "tail -1 ./logUnittest.txt > resultUnittest.txt"',
            workdir='/workspace'
        )

        zip_bytes = self._download_code(context_id)

        task_name = entry.instance_id
        zip_dest = os.path.join(
            self.output_dir, 'MasBench-A2A', 'tasks', task_name, f'{task_name}.zip'
        )
        os.makedirs(os.path.dirname(zip_dest), exist_ok=True)

        with zipfile.ZipFile(BytesIO(zip_bytes)) as zip_file:
            zip_file.extractall(os.path.dirname(zip_dest))

    def evaluate_result(self) -> None:
        results = {}
        out_dest = os.path.join(
            self.output_dir, 'MasBench-A2A', 'tasks'
        )
        dir_list = os.listdir(out_dest)
        for dir in dir_list:
            with open(os.path.join(out_dest, dir, "resultUnittest.txt"), 'r') as f:
                result = f.readlines()
            results[dir] = result[0]

        passed = sum([results[k] == "OK\n" for k in results])
        with open(os.path.join(out_dest, "result.txt"), 'w') as f:
            f.write(f"{passed/len(results)}\n\n")
            for k in results:
                f.write(f"{k}: {results[k]}")

    async def run(self) -> None:
        self.pre_run_functions()
        self.load_data()
        self.setup_agent()
        for _, entry in self.data.iterrows():
            await self.process_task(entry)
        self.evaluate_result()
        self.post_run_functions()


async def main():
    parser = argparse.ArgumentParser(description="Run inference on MasBench dataset using OpenHands agent.")
    parser.add_argument("--agent-url", default="http://127.0.0.1:3000", help="Base URL of the A2A agent (default: http://127.0.0.1:3000)")
    parser.add_argument("--dataset-path", help="Path to the CSV dataset file. If not provided, defaults to 'data/MasBench.csv' relative to this script.")
    parser.add_argument("--output-dir", default="evaluation/evaluation_outputs/outputs", help="Directory to store evaluation outputs (default: evaluation/evaluation_outputs/outputs)")
    args = parser.parse_args()

    if args.dataset_path is None:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        dataset_path = os.path.join(base_dir, "data", "MasBench.csv")
    else:
        dataset_path = args.dataset_path

    runner = InferenceRunner(
        dataset_path=dataset_path,
        agent_url=args.agent_url,
        output_dir=args.output_dir
    )
    await runner.run()


if __name__ == '__main__':
    asyncio.run(main())
