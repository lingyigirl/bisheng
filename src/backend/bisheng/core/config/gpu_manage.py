from pydantic import BaseModel


class GpuManageConf(BaseModel):
    """Configuration for the GPU resource management module."""

    ssh_timeout: int = 10  # SSH connect timeout (seconds)
    command_timeout: int = 60  # per-command timeout (seconds)
    log_tail_lines: int = 500  # docker logs --tail default
    worker_alive_threshold: int = 15  # platform celery heartbeat writes every 5s -> 3 misses = dead
    container_prefix: str = "bisheng-"
