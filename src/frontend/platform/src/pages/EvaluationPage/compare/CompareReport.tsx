import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";

import { LoadingIcon } from "@/components/bs-icons/loading";
import { Button } from "@/components/bs-ui/button";
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from "@/components/bs-ui/dropdownMenu";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/bs-ui/select";
import {
    CompareReportExportFormat,
    CompareReportMetric,
    CompareReportResp,
    getEvaluationCompareReportApi,
    exportEvaluationCompareReportApi,
} from "@/controllers/API/evaluate";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import { downloadFile } from "@/util/utils";

import { checkSassUrl } from "@/components/bs-comp/FileView";
import { ReportCharts } from "./ReportCharts";

// Metric key -> i18n key. The backend also sends a Chinese label; the frontend
// localizes so en/ja users don't see Chinese (the backend label is a fallback).
const METRIC_I18N: Record<string, string> = {
    precision: "evaluation.reportMetricPrecision",
    f1: "evaluation.reportMetricF1",
    recall: "evaluation.reportMetricRecall",
    hallucination: "evaluation.reportMetricHallucination",
    token: "evaluation.reportMetricToken",
    latency: "evaluation.reportMetricLatency",
};

type Verdict = "better" | "worse" | "same" | "na";

function fmtValue(value: number | null, unit: string): string {
    if (value === null || value === undefined) return "N/A";
    return unit === "%" ? `${(value * 100).toFixed(2)}%` : `${value}`;
}

function fmtDelta(delta: number | null, unit: string): string {
    if (delta === null || delta === undefined) return "";
    if (unit === "%") return `${delta >= 0 ? "+" : ""}${(delta * 100).toFixed(2)}%`;
    return `${delta >= 0 ? "+" : ""}${delta}`;
}

/** v2 vs v1 verdict for one metric — flipped by direction (spec AC-08). */
function decideVerdict(v1: number | null, v2: number | null, higherIsBetter: boolean): Verdict {
    if (v1 === null || v2 === null || v1 === undefined || v2 === undefined) return "na";
    if (v1 === v2) return "same";
    const v2Better = higherIsBetter ? v2 > v1 : v2 < v1;
    return v2Better ? "better" : "worse";
}

function deltaTone(delta: number | null, higherIsBetter: boolean): string {
    if (delta === null || delta === undefined || delta === 0) return "text-gray-400";
    const good = higherIsBetter ? delta > 0 : delta < 0;
    return good ? "text-emerald-600" : "text-red-600";
}

function verdictClass(verdict: Verdict): string {
    switch (verdict) {
        case "better":
            return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300";
        case "worse":
            return "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300";
        case "na":
            return "bg-gray-100 text-gray-500 dark:bg-zinc-800 dark:text-zinc-400";
        default:
            return "bg-gray-100 text-gray-600 dark:bg-zinc-800 dark:text-zinc-300";
    }
}

function reportChangeKey(changeKind: string): string {
    switch (changeKind) {
        case "model":
            return "evaluation.reportChangeModel";
        case "prompt":
            return "evaluation.reportChangePrompt";
        case "both":
            return "evaluation.reportChangeBoth";
        default:
            return "evaluation.reportChangeNone";
    }
}

export default function CompareReport() {
    const { t } = useTranslation();
    const navigate = useNavigate();
    const { groupId } = useParams<{ groupId: string }>();

    const [data, setData] = useState<CompareReportResp | null>(null);
    const [loading, setLoading] = useState(false);
    const [exporting, setExporting] = useState(false);
    const [metricKey, setMetricKey] = useState<string>("precision");

    useEffect(() => {
        if (!groupId) return;
        setLoading(true);
        setData(null);
        captureAndAlertRequestErrorHoc(
            getEvaluationCompareReportApi(groupId)
                .then(setData)
                .catch(() => setData(null))
        ).finally(() => setLoading(false));
    }, [groupId]);

    const versionLabel = (index: number): string => {
        const ev = data?.evaluations[index];
        if (!ev) return `${t("evaluation.compareVersion")} ${index + 1}`;
        return ev.version_name || `${t("evaluation.compareVersion")} ${ev.version}`;
    };

    // Localize metric labels by key; the backend label (Chinese) is only a fallback.
    const metrics: CompareReportMetric[] = useMemo(
        () => (data?.metrics ?? []).map((m) => ({ ...m, label: METRIC_I18N[m.key] ? t(METRIC_I18N[m.key]) : m.label })),
        [data, t]
    );
    const grouped = useMemo(
        () => (data?.charts?.grouped_bar ?? []).map((m) => ({ ...m, label: METRIC_I18N[m.key] ? t(METRIC_I18N[m.key]) : m.label })),
        [data, t]
    );
    const selected = metrics.find((m) => m.key === metricKey) ?? metrics[0];

    const handleExport = (format: CompareReportExportFormat) => {
        if (!groupId) return;
        setExporting(true);
        captureAndAlertRequestErrorHoc(
            exportEvaluationCompareReportApi(groupId, format).then(async ({ url, file_name }) => {
                await downloadFile(checkSassUrl(url), file_name || `evaluation_compare_${groupId}.${format}`);
            })
        ).finally(() => setExporting(false));
    };

    const conclusionText = (): string => {
        if (!data || data.conclusion === "unable") return t("evaluation.reportConclusionUnable");
        const label = data.conclusion === "v2" ? versionLabel(1) : versionLabel(0);
        return `${label} ${t("evaluation.reportConclusionBetter")}`;
    };

    return (
        <div className="relative h-full w-full overflow-y-auto px-4 py-4">
            {loading && (
                <div className="absolute left-0 top-0 z-10 flex h-full w-full items-center justify-center bg-[rgba(255,255,255,0.6)]">
                    <LoadingIcon />
                </div>
            )}
            <div className="mb-4 flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <Button variant="outline" className="h-8 px-3 text-xs" onClick={() => navigate("/evaluation")}>
                        {t("evaluation.reportBack")}
                    </Button>
                    <h2 className="text-lg font-medium">{t("evaluation.reportTitle")}</h2>
                </div>
                <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                        <Button className="px-6 text-[#fff]" disabled={!data || exporting}>
                            {t("evaluation.reportExport")}
                        </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="w-52">
                        <DropdownMenuItem onSelect={() => handleExport("pdf")}>
                            {t("evaluation.reportExportPdf")}
                        </DropdownMenuItem>
                        <DropdownMenuItem onSelect={() => handleExport("docx")}>
                            {t("evaluation.reportExportDocx")}
                        </DropdownMenuItem>
                    </DropdownMenuContent>
                </DropdownMenu>
            </div>

            {!loading && !data && (
                <div className="rounded-md border border-gray-200 p-6 text-sm text-gray-500 dark:border-zinc-700">
                    {t("evaluation.reportNotReady")}
                </div>
            )}

            {data && (
                <div className="space-y-5 pb-10">
                    {/* 对比对象条 (spec AC-02) */}
                    <div className="rounded-md border border-blue-100 bg-blue-50 p-3 text-sm dark:border-blue-900 dark:bg-blue-950/40">
                        <div className="mb-2 flex flex-wrap items-center gap-x-6 gap-y-1 text-xs text-gray-600 dark:text-gray-300">
                            <span>
                                {t("evaluation.reportApp")}: <span className="font-medium text-gray-800 dark:text-gray-100">{data.object_bar.app_name}</span>
                            </span>
                            <span>
                                {t("evaluation.reportDataset")}: <span className="font-medium text-gray-800 dark:text-gray-100">{data.object_bar.data_set}</span>
                            </span>
                            <span>
                                {t("evaluation.reportChangeKind")}:{" "}
                                <span className="font-medium text-gray-800 dark:text-gray-100">{t(reportChangeKey(data.object_bar.change_kind))}</span>
                                {data.object_bar.change_kind === "both" && (
                                    <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300">
                                        {t("evaluation.reportMultiVariable")}
                                    </span>
                                )}
                            </span>
                        </div>
                        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                            {(["v1", "v2"] as const).map((side, idx) => (
                                <div key={side} className="rounded border border-blue-100 bg-white/70 p-2 dark:border-blue-900 dark:bg-zinc-900/40">
                                    <div className="mb-1 text-xs font-medium text-gray-500">{versionLabel(idx)}</div>
                                    <div className="text-xs text-gray-700 dark:text-gray-200">
                                        {t("evaluation.reportModels")}: {(data.object_bar.models[side] || []).join(", ") || "--"}
                                    </div>
                                    <div className="text-xs text-gray-700 dark:text-gray-200">
                                        {t("evaluation.reportPromptNodes")}: {(data.object_bar.prompt_nodes[side] || []).join(", ") || "--"}
                                    </div>
                                </div>
                            ))}
                        </div>
                        {data.object_bar.change_kind === "none" && (
                            <div className="mt-2 rounded border border-amber-200 bg-amber-50 px-2 py-1.5 text-xs text-amber-700 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
                                {t("evaluation.reportNoVariableWarning")}
                            </div>
                        )}
                    </div>

                    {/* 一句话结论 + 对比分析 (spec AC-03/AC-05) */}
                    <div className="rounded-md border border-gray-200 p-3 dark:border-zinc-700">
                        <div className="text-sm">
                            <span className="text-gray-500">{t("evaluation.reportConclusionTitle")}: </span>
                            <span className="font-medium">{conclusionText()}</span>
                        </div>
                        <div className="mt-1 whitespace-pre-line text-sm leading-6 text-gray-600 dark:text-gray-300">
                            <span className="text-gray-500">{t("evaluation.reportAnalysis")}: </span>
                            {data.analysis}
                        </div>
                    </div>

                    {/* 指标卡 6 项，不判达标 (spec AC-06) */}
                    <div>
                        <div className="mb-2 text-sm font-medium">{t("evaluation.reportMetricCards")}</div>
                        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
                            {metrics.map((m) => (
                                <div key={m.key} className="rounded-md border border-gray-200 p-3 dark:border-zinc-700">
                                    <div className="mb-1 text-xs font-medium text-gray-500">{m.label}</div>
                                    <div className="flex items-baseline justify-between text-sm">
                                        <span className="text-gray-400">{versionLabel(0)}</span>
                                        <span className="font-medium">{fmtValue(m.v1, m.unit)}</span>
                                    </div>
                                    <div className="flex items-baseline justify-between text-sm">
                                        <span className="text-gray-400">{versionLabel(1)}</span>
                                        <span className="font-medium">{fmtValue(m.v2, m.unit)}</span>
                                    </div>
                                    <div className={`mt-1 text-right text-xs ${deltaTone(m.delta, m.higher_is_better)}`}>
                                        {fmtDelta(m.delta, m.unit)}
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>

                    {/* 图表 (spec AC-07) */}
                    {selected && (
                        <ReportCharts
                            grouped={grouped}
                            perQuestion={data.per_question}
                            metric={selected}
                            v1Label={versionLabel(0)}
                            v2Label={versionLabel(1)}
                        />
                    )}

                    {/* 逐题明细：下拉切指标，更好/更差按方向翻转 (spec AC-08/09/10) */}
                    <div>
                        <div className="mb-2 flex items-center justify-between">
                            <div className="flex items-center gap-2">
                                <span className="text-sm font-medium">{t("evaluation.reportPerQuestion")}</span>
                                <Select value={metricKey} onValueChange={setMetricKey}>
                                    <SelectTrigger className="h-8 w-[160px]">
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {metrics.map((m) => (
                                            <SelectItem value={m.key} key={m.key}>
                                                {m.label}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="flex gap-4 text-xs text-gray-500">
                                {data.evaluations.map((ev, idx) => (
                                    <span key={ev.id}>
                                        {versionLabel(idx)} ·{" "}
                                        {t("evaluation.reportStats", {
                                            count: ev.question_count,
                                            rate: ev.success_rate === null ? "N/A" : `${(ev.success_rate * 100).toFixed(0)}%`,
                                            failed: ev.failed_count,
                                        })}
                                    </span>
                                ))}
                            </div>
                        </div>
                        <div className="overflow-x-auto rounded-md border border-gray-200 dark:border-zinc-700">
                            <table className="w-full text-sm">
                                <thead className="bg-gray-50 text-left text-xs text-gray-500 dark:bg-zinc-800">
                                    <tr>
                                        <th className="w-14 px-3 py-2">#</th>
                                        <th className="px-3 py-2">{t("evaluation.compareQuestion")}</th>
                                        <th className="w-28 px-3 py-2">{versionLabel(0)}</th>
                                        <th className="w-28 px-3 py-2">{versionLabel(1)}</th>
                                        <th className="w-24 px-3 py-2">{t("evaluation.compareVerdict")}</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.per_question.map((row, idx) => {
                                        const v1 = row.v1 && selected ? (row.v1 as unknown as Record<string, number | null>)[selected.key] : null;
                                        const v2 = row.v2 && selected ? (row.v2 as unknown as Record<string, number | null>)[selected.key] : null;
                                        const verdict = selected ? decideVerdict(v1, v2, selected.higher_is_better) : "na";
                                        return (
                                            <tr key={idx} className="border-t border-gray-100 dark:border-zinc-800">
                                                <td className="px-3 py-2 text-gray-400">{idx + 1}</td>
                                                <td className="max-w-96 px-3 py-2">
                                                    <div className="truncate" title={row.question}>
                                                        {row.question}
                                                    </div>
                                                    {row.failed && (
                                                        <div className="truncate text-xs text-red-500" title={row.fail_reason ?? ""}>
                                                            {t("evaluation.compareFailed")}
                                                            {row.fail_reason ? `：${row.fail_reason}` : ""}
                                                        </div>
                                                    )}
                                                </td>
                                                <td className="px-3 py-2">{selected ? fmtValue(v1, selected.unit) : "N/A"}</td>
                                                <td className="px-3 py-2">{selected ? fmtValue(v2, selected.unit) : "N/A"}</td>
                                                <td className="px-3 py-2">
                                                    <span className={`rounded px-2 py-0.5 text-xs ${verdictClass(verdict)}`}>
                                                        {t(
                                                            verdict === "better"
                                                                ? "evaluation.compareVerdictBetter"
                                                                : verdict === "worse"
                                                                  ? "evaluation.compareVerdictWorse"
                                                                  : verdict === "same"
                                                                    ? "evaluation.compareVerdictSame"
                                                                    : "evaluation.compareVerdictNa"
                                                        )}
                                                    </span>
                                                </td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
