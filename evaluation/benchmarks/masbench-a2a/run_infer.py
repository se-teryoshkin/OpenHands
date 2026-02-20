import asyncio
from io import BytesIO
import os
from typing import Any
import zipfile

import pandas as pd

import requests
import docker
from a2a.client import ClientConfig
from agents import RemoteA2AAgent, RemoteA2AAgentResponse


def _download_code(known_url, context_id) -> bytes:

    response = requests.get(
        f"{known_url}/api/conversations/{context_id}/zip-directory"
    )

    if response.status_code != 200:
        raise ValueError(f"Error downloading code: {response.status_code} status code. Content: {response.content}")

    zip_file_content: bytes = response.content
    return zip_file_content


ALL_EVENTS = []


def primitive_callback(event_message: Any):
    # TODO: Entire output in a single file
    ALL_EVENTS.append(event_message)


async def main():
    # TODO: Options on the script via command line
    DATASET_PATH = f"{os.path.dirname(os.path.abspath(__file__))}/data/MasBench.csv"
    AGENT_KNOWN_URL = "http://127.0.0.1:3000"
    OUTPUT_DIR = f""
    data = pd.read_csv(DATASET_PATH)

    openhands_codeact_agent = RemoteA2AAgent(
        well_known_url=AGENT_KNOWN_URL,
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
        events_callback=primitive_callback,
    )

    for ix, entry in data.iterrows():
        result: RemoteA2AAgentResponse = await openhands_codeact_agent.send_message(
            message_text=f"Make an implementation of the following function in a run.py file.\nIf you think you have solved the task, please finish the interaction. IMPORTANT: YOU SHOULD NOT ASK FOR HUMAN RESPONSE UNTIL USER CONTACT YOU HIMSELF. The function:\n {entry.instruction}\n",
            message_id=f"0{entry.instance_id}",
        )

        context_id = result.response.get("task", {}).get("contextId", None)
        if context_id:
            # connect to docker container
            client = docker.from_env()
            container = client.containers.get("openhands-runtime-" + context_id)

            # print(f"Connected to container: {container.name}")
            test_str = entry.test.replace('"', "'")

            container.exec_run(f'bash -c "cd /workspace && cat > testUnittest.py << \u0027EOF\u0027\n{test_str}\nEOF\n"')
            result = container.exec_run(f'bash -c "/openhands/micromamba/envs/openhands/bin/python3 -m unittest testUnittest.py 2> logUnittest.txt"', workdir='/workspace')
            container.exec_run(f'bash -c "tail -1 ./logUnittest.txt > resultUnittest.txt"', workdir='/workspace')

            zip_bytes = _download_code(known_url=AGENT_KNOWN_URL, context_id=context_id)
            local_dir = os.path.dirname(__file__)
            task_name = entry.instance_id
            zip_dest = os.path.join(
                local_dir, 'tasks', task_name, f'{task_name}.zip'
            )
            os.makedirs(os.path.dirname(zip_dest), exist_ok=True)
            with zipfile.ZipFile(BytesIO(zip_bytes)) as zip_file:
                zip_file.extractall(os.path.dirname(zip_dest))

    # TODO: single file with results without additional scripts



if __name__ == '__main__':

    asyncio.run(main())
