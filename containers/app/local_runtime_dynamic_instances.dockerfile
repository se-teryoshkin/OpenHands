# Build from local_runtime.dockerfile
# docker build -f OpenHands/containers/app/local_runtime_dynamic_instance.dockerfile .
# AppFactory-components and AppFactory-agents should be located in the same dir as OpenHands
FROM base_oh_instance

RUN apt update -y && apt install build-essential -y && rm -rf /var/lib/apt/lists/*
COPY AppFactory-a2a/instructions/poetry /instructions/poetry

RUN --mount=type=bind,src=./AppFactory-components,dst=/appfactory_components,readonly \
    mkdir -p /workspace && /instructions/poetry/init.sh
