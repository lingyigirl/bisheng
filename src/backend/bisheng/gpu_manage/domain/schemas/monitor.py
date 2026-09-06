from pydantic import BaseModel


class GpuInfo(BaseModel):
    index: int
    name: str
    memory_total_mb: int
    memory_used_mb: int
    memory_free_mb: int
    util_percent: int
    temperature_c: int


class GpuProcess(BaseModel):
    pid: int
    process_name: str
    used_memory_mb: int


class ContainerInfo(BaseModel):
    container_id: str
    name: str
    image: str
    status: str
    ports: str


class ModelFileInfo(BaseModel):
    name: str
    is_dir: bool
    size_bytes: int
    mode: str
    mtime: str
    path: str


class WorkerStatus(BaseModel):
    queue: str
    last_beat_ts: int
    age_seconds: int
    alive: bool
