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

// ============ F066 评测报告 ============

export type EvaluationReportResp = {
    /** MinIO presigned download link for the generated xlsx report */
    url: string;
    /** suggested download file name */
    file_name: string;
};

/**
 * 下载评测报告（效果 + 性能，xlsx 双 sheet，后端按需生成）
 * @param id 评测任务 id
 */
export const getEvaluationReportApi = async (id: number): Promise<EvaluationReportResp> => {
    return await axios.get(`/api/v1/evaluation/${id}/report/download`);
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

// ============ F067 可视化对比报告 ============

/** one report metric card: direction + both versions' aggregate value */
export type CompareReportMetric = {
    key: string;
    label: string;
    unit: string;
    /** true = higher is better (准确率/F1/召回); false = lower is better (幻觉率/成本/延迟) */
    higher_is_better: boolean;
    v1: number | null;
    v2: number | null;
    delta: number | null;
};

/** one question's six metrics for one version */
export type CompareReportSide = {
    precision: number | null;
    f1: number | null;
    recall: number | null;
    hallucination: number | null;
    token: number | null;
    latency: number | null;
    exec_status: string | null;
    fail_reason?: string | null;
};

export type CompareReportQuestion = {
    question: string;
    failed?: boolean;
    fail_reason?: string | null;
    v1: CompareReportSide | null;
    v2: CompareReportSide | null;
};

export type CompareReportEvaluation = {
    id: number;
    version: number | null;
    version_name: string | null;
    status: number;
    result_score: Evaluation["result_score"] | null;
    description: string;
    question_count: number;
    success_count: number;
    failed_count: number;
    success_rate: number | null;
};

export type CompareReportResp = {
    group_id: string;
    evaluations: CompareReportEvaluation[];
    object_bar: {
        app_name: string;
        data_set: string;
        models: { v1: string[]; v2: string[] };
        prompt_nodes: { v1: string[]; v2: string[] };
        change_kind: "model" | "prompt" | "both" | "none";
    };
    conclusion: "v1" | "v2" | "unable";
    analysis: string;
    metrics: CompareReportMetric[];
    charts: { grouped_bar: CompareReportMetric[] };
    per_question: CompareReportQuestion[];
};

/**
 * 可视化对比报告数据（两版模型/提示词对比 + 结论 + 6 指标 + 逐题全指标）
 * @param groupId 对比组 id
 */
export const getEvaluationCompareReportApi = async (groupId: string): Promise<CompareReportResp> => {
    return await axios.get(`/api/v1/evaluation/compare/${groupId}/report`);
};

export type CompareReportExportFormat = "pdf" | "docx" | "xlsx" | "html";

/**
 * 导出对比报告（后端生成 xlsx / 完整 HTML / PDF，返回预签名下载链接）
 * @param groupId 对比组 id
 * @param format 导出格式
 */
export const exportEvaluationCompareReportApi = async (
    groupId: string,
    format: CompareReportExportFormat = "xlsx"
): Promise<EvaluationReportResp> => {
    return await axios.get(`/api/v1/evaluation/compare/${groupId}/report/export`, {
        params: { format },
    });
};
