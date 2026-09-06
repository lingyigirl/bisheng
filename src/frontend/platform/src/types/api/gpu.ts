// GPU resource management types

export interface GpuNodeItem {
    id: number;
    name: string;
    host: string;
    port: number;
    username: string;
    model_dir: string;
    remark?: string;
    has_password: boolean;
    create_time?: string;
    update_time?: string;
}

export interface GpuNodeCreate {
    name: string;
    host: string;
    port: number;
    username: string;
    password: string;
    model_dir: string;
    remark?: string;
}

export interface GpuNodeUpdate {
    name?: string;
    host?: string;
    port?: number;
    username?: string;
    password?: string;
    model_dir?: string;
    remark?: string;
}

export interface GpuNodeTestResult {
    ok: boolean;
    gpu_count: number;
    error?: string;
}

export interface GpuInfo {
    index: number;
    name: string;
    memory_total_mb: number;
    memory_used_mb: number;
    memory_free_mb: number;
    util_percent: number;
    temperature_c: number;
}

export interface GpuProcess {
    pid: number;
    process_name: string;
    used_memory_mb: number;
}

export interface ContainerInfo {
    container_id: string;
    name: string;
    image: string;
    status: string;
    ports: string;
}

export interface ModelFileInfo {
    name: string;
    is_dir: boolean;
    size_bytes: number;
    mode: string;
    mtime: string;
    path: string;
}

export interface WorkerStatus {
    queue: string;
    last_beat_ts: number;
    age_seconds: number;
    alive: boolean;
}

export interface DeployTemplate {
    id: number;
    name: string;
    image: string;
    model_path: string;
    gpu_index: string;
    host_port: number;
    container_port: number;
    served_model_name: string;
    start_args?: string;
    is_builtin: boolean;
    remark?: string;
}

export interface DeployTemplateCreate {
    name: string;
    image: string;
    model_path: string;
    gpu_index: string;
    host_port: number;
    container_port: number;
    served_model_name: string;
    start_args?: string;
    remark?: string;
}

export interface DeployTemplateUpdate {
    name?: string;
    image?: string;
    model_path?: string;
    gpu_index?: string;
    host_port?: number;
    container_port?: number;
    served_model_name?: string;
    start_args?: string;
    remark?: string;
}

export interface DeployInstance {
    id: number;
    node_id: number;
    template_id: number;
    name: string;
    status: string;
    container_id?: string;
    host_port?: number;
    create_time?: string;
    update_time?: string;
}

export interface DeployInstanceCreate {
    node_id: number;
    template_id: number;
    name: string;
}
