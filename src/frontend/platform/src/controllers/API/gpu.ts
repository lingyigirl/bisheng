// @ts-strict-ignore
import type {
    ContainerInfo,
    DeployInstance,
    DeployInstanceCreate,
    DeployTemplate,
    DeployTemplateCreate,
    DeployTemplateUpdate,
    GpuInfo,
    GpuNodeCreate,
    GpuNodeItem,
    GpuNodeTestResult,
    GpuNodeUpdate,
    GpuProcess,
    ModelFileInfo,
    WorkerStatus,
} from "../../types/api/gpu";
import axios from "../request";

// GPU nodes
export const getGpuNodesApi = async (): Promise<GpuNodeItem[]> =>
    await axios.get(`/api/v1/gpu_node`);

export const createGpuNodeApi = async (data: GpuNodeCreate): Promise<GpuNodeItem> =>
    await axios.post(`/api/v1/gpu_node`, data);

export const updateGpuNodeApi = async (id: number, data: GpuNodeUpdate): Promise<GpuNodeItem> =>
    await axios.put(`/api/v1/gpu_node/${id}`, data);

export const deleteGpuNodeApi = async (id: number): Promise<void> => {
    await axios.delete(`/api/v1/gpu_node/${id}`);
};

export const testGpuNodeApi = async (id: number): Promise<GpuNodeTestResult> =>
    await axios.post(`/api/v1/gpu_node/${id}/test`);

// Monitoring
export const getGpuStatusApi = async (nodeId: number): Promise<GpuInfo[]> =>
    await axios.get(`/api/v1/gpu_monitor/${nodeId}/gpus`);

export const getNodeProcessesApi = async (nodeId: number): Promise<GpuProcess[]> =>
    await axios.get(`/api/v1/gpu_monitor/${nodeId}/processes`);

export const getNodeContainersApi = async (nodeId: number): Promise<ContainerInfo[]> =>
    await axios.get(`/api/v1/gpu_monitor/${nodeId}/containers`);

export const getModelFilesApi = async (nodeId: number, subpath = ""): Promise<ModelFileInfo[]> =>
    await axios.get(`/api/v1/gpu_monitor/${nodeId}/files`, { params: { subpath } });

export const deleteModelFileApi = async (nodeId: number, path: string): Promise<void> => {
    await axios.delete(`/api/v1/gpu_monitor/${nodeId}/files`, { params: { path } });
};

export const getWorkersApi = async (): Promise<WorkerStatus[]> =>
    await axios.get(`/api/v1/gpu_monitor/workers`);

// Deploy templates & instances
export const getTemplatesApi = async (): Promise<DeployTemplate[]> =>
    await axios.get(`/api/v1/gpu_deploy/template`);

export const createTemplateApi = async (data: DeployTemplateCreate): Promise<DeployTemplate> =>
    await axios.post(`/api/v1/gpu_deploy/template`, data);

export const updateTemplateApi = async (id: number, data: DeployTemplateUpdate): Promise<DeployTemplate> =>
    await axios.put(`/api/v1/gpu_deploy/template/${id}`, data);

export const deleteTemplateApi = async (id: number): Promise<void> => {
    await axios.delete(`/api/v1/gpu_deploy/template/${id}`);
};

export const getInstancesApi = async (nodeId?: number): Promise<DeployInstance[]> =>
    await axios.get(`/api/v1/gpu_deploy/instance`, { params: { node_id: nodeId } });

export const createInstanceApi = async (data: DeployInstanceCreate): Promise<DeployInstance> =>
    await axios.post(`/api/v1/gpu_deploy/instance`, data);

export const getInstanceStatusApi = async (id: number): Promise<string> =>
    await axios.get(`/api/v1/gpu_deploy/instance/${id}/status`);

export const getInstanceLogsApi = async (id: number, tail?: number): Promise<string> => {
    const res = await axios.get(`/api/v1/gpu_deploy/instance/${id}/logs`, { params: { tail } });
    return res.logs;
};

export const startInstanceApi = async (id: number): Promise<string> => {
    const res = await axios.post(`/api/v1/gpu_deploy/instance/${id}/start`);
    return res.status;
};

export const stopInstanceApi = async (id: number): Promise<string> => {
    const res = await axios.post(`/api/v1/gpu_deploy/instance/${id}/stop`);
    return res.status;
};

export const restartInstanceApi = async (id: number): Promise<string> => {
    const res = await axios.post(`/api/v1/gpu_deploy/instance/${id}/restart`);
    return res.status;
};

export const deleteInstanceApi = async (id: number): Promise<void> => {
    await axios.delete(`/api/v1/gpu_deploy/instance/${id}`);
};
