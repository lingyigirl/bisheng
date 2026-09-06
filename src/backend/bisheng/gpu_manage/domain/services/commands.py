"""Fixed remote command templates for GPU node management.

Every command executed on a remote GPU node is one of these templates with
only validated values interpolated — never free-form user shell input.
"""

import re

CONTAINER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
GPU_INDEX_RE = re.compile(r"^\d+(,\d+)*$")
PORT_RANGE = (1, 65535)
# Shell metacharacters that must never appear in interpolated values.
REJECT_CHARS = re.compile(r'[\s;&|$()`"\']')
START_ARG_KEY_RE = re.compile(r"^--[a-zA-Z0-9-]+$")

TEST_CMD = "nvidia-smi -L"

GPU_STATS_CMD = (
    "nvidia-smi --query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu "
    "--format=csv,noheader,nounits"
)

GPU_APPS_CMD = "nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits"

DOCKER_PS_CMD = "docker ps -a --format '{{.ID}}\\t{{.Names}}\\t{{.Image}}\\t{{.Status}}\\t{{.Ports}}'"

DOCKER_INSPECT_STATUS_CMD = "docker inspect -f '{{{{.State.Status}}}}' {name}"

DOCKER_RUN_TMPL = (
    "docker run -d --name {name} --restart unless-stopped --gpus=all "
    "-e NVIDIA_VISIBLE_DEVICES={gpu_index} "
    "-p {host_port}:{container_port} "
    "-v {model_path}:{model_path} "
    "{image} --model {model_path} --served-model-name {served_model_name} "
    "--port {container_port} {args}"
)

DOCKER_START_TMPL = "docker start {name}"
DOCKER_STOP_TMPL = "docker stop -t 30 {name}"
DOCKER_RESTART_TMPL = "docker restart -t 30 {name}"
DOCKER_RM_TMPL = "docker rm -f {name}"
DOCKER_LOGS_TMPL = "docker logs --tail {tail} {name}"
DOCKER_PS_FILTER_NAME_CMD = "docker ps -q --filter name=^{name}$"

LS_DIR_TMPL = "ls -la --block-size=1 '{path}'"
DU_DIR_TMPL = "du -sh -- '{path}'"
RM_PATH_TMPL = "rm -rf -- '{path}'"


def validate_container_name(name: str) -> bool:
    return bool(CONTAINER_NAME_RE.match(name))


def validate_gpu_index(value: str) -> bool:
    return bool(GPU_INDEX_RE.match(value))


def validate_port(value: int) -> bool:
    return PORT_RANGE[0] <= value <= PORT_RANGE[1]


def validate_safe_value(value: str) -> bool:
    return not REJECT_CHARS.search(value)
