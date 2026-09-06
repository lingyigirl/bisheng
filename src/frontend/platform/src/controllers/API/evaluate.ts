import { ReactFlowJsonObject } from "@xyflow/react";
import { FlowStyleType, FlowType, FlowVersionItem } from "../../types/flow";
import axios from "../request";


export type Evaluation = {
    file_path: string,
    file_name: string,
    unique_id: string,
    prompt: string,
    result_score: {
        answer_f1: string,
        answer_precision: string,
        answer_recall: string,
    },
    create_time: string,
    id: number,
    user_id: number,
    exec_type: string,
    version: number,
    compare_group_id: string | null,
    status: number,
    progress?: string,
    result_file_path: string,
    is_delete: number,
    description: string,
    update_time: string,
    unique_name: string,
    version_name: string,
    user_name: string,
}

/**
 * 获取评测列表
 * @param data 
 * @returns 
 */
export const getEvaluationApi = async (page, limit): Promise<Evaluation[]> => {
    return await axios.get(`/api/v1/evaluation`, {
        params: {
            page, limit
        }
    });
};

/**
 * 创建测评任务
 */
export const createEvaluationApi = async (data): Promise<any> => {
    return await axios.post(`/api/v1/evaluation`, data,{
        headers: {
            'Content-Type':'multipart/form-data'
        }
    });
};

/**
 * 删除测评任务
 */
export const deleteEvaluationApi = async (id): Promise<any> => {
    return await axios.delete(`/api/v1/evaluation/${id}`);
};

/**
 * 获取下载链接
 */
export const getEvaluationUrlApi = async (id): Promise<{ url: string }> => {
    return await axios.get(`/api/v1/evaluation/result/file/download?file_url=${id}`);
};

// ============ F053 版本对比评测 ============

export type EvaluationCompareResp = {
    group_id: string;
    evaluations: {
        id: number;
        version: number | null;
        status: number;
        result_score: Evaluation["result_score"] | null;
        description: string;
    }[];
    comparison: {
        totals: Record<string, { v1: number | null; v2: number | null; delta: number | null }>;
        conclusion: "v1" | "v2" | "unable";
        per_question: {
            question: string;
            v1: Record<string, number | null>;
            v2: Record<string, number | null>;
            verdict: "better" | "worse" | "same" | "na";
        }[];
    } | null;
};

/**
 * 版本对比评测结果（总分差值 + 逐题对比 + 结论）
 * @param groupId 对比组 id
 */
export const getEvaluationCompareApi = async (groupId: string): Promise<EvaluationCompareResp> => {
    return await axios.get(`/api/v1/evaluation/compare/${groupId}`);
};
