import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/bs-ui/sheet";
import { LoadingIcon } from "@/components/bs-icons/loading";
import { Evaluation, EvaluationCompareResp, getEvaluationCompareApi } from "@/controllers/API/evaluate";

interface CompareSheetProps {
    groupId: string | null;
    rows: Evaluation[];
    onClose: () => void;
}

type MetricKey = "f1" | "recall" | "precision";

const METRIC_KEYS: MetricKey[] = ["f1", "recall", "precision"];

function pct(value: number | null): string {
    return value === null ? "N/A" : `${(value * 100).toFixed(2)}%`;
}

function deltaClass(delta: number | null): string {
    if (delta === null) return "text-gray-400";
    if (delta > 0) return "text-emerald-600";
    if (delta < 0) return "text-red-600";
    return "text-gray-400";
}

function verdictClass(verdict: string): string {
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

export function CompareSheet({ groupId, rows, onClose }: CompareSheetProps) {
    const { t } = useTranslation();
    const [data, setData] = useState<EvaluationCompareResp | null>(null);
    const [loading, setLoading] = useState(false);
    const [metric, setMetric] = useState<MetricKey>("f1");

    useEffect(() => {
        if (!groupId) return;
        setLoading(true);
        setData(null);
        getEvaluationCompareApi(groupId)
            .then(setData)
            .catch(() => setData(null))
            .finally(() => setLoading(false));
    }, [groupId]);

    const versionLabel = (index: number): string => {
        const row = rows[index];
        if (!row) return `${t("evaluation.compareVersion")} ${index + 1}`;
        return `${row.version_name || t("evaluation.compareVersion")} ${row.version}`;
    };

    const conclusion = data?.comparison?.conclusion;
    const conclusionText =
        conclusion === "v1"
            ? `${versionLabel(0)} ${t("evaluation.compareBetter")}`
            : conclusion === "v2"
              ? `${versionLabel(1)} ${t("evaluation.compareBetter")}`
              : t("evaluation.compareUnable");

    return (
        <Sheet open={!!groupId} onOpenChange={(open) => !open && onClose()}>
            <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-[66vw]">
                <SheetHeader>
                    <SheetTitle>{t("evaluation.compareTitle")}</SheetTitle>
                </SheetHeader>
                <div className="space-y-6 px-1 pb-8">
                    {loading && (
                        <div className="flex justify-center py-10">
                            <LoadingIcon />
                        </div>
                    )}
                    {!loading && (!data || !data.comparison) && (
                        <div className="rounded-md border border-gray-200 p-4 text-sm text-gray-500 dark:border-zinc-700">
                            {t("evaluation.compareNotReady")}
                            {rows.map((row, idx) => (
                                <div key={row.id} className="mt-2 flex gap-2 text-xs">
                                    <span className="w-40 shrink-0">{versionLabel(idx)}</span>
                                    <span>
                                        {row.status === 1
                                            ? t("evaluation.compareRunning")
                                            : row.status === 2
                                              ? `${t("evaluation.compareFailed")}：${row.description || "-"}`
                                              : "-"}
                                    </span>
                                </div>
                            ))}
                        </div>
                    )}
                    {!loading && data?.comparison && (
                        (() => {
                            const comparison = data.comparison;
                            return (
                        <>
                            {/* Totals + conclusion */}
                            <div>
                                <div className="mb-2 rounded-md border border-blue-100 bg-blue-50 p-3 text-sm dark:border-blue-900 dark:bg-blue-950/40">
                                    {t("evaluation.compareConclusion")}：<span className="font-medium">{conclusionText}</span>
                                </div>
                                <div className="grid grid-cols-3 gap-3">
                                    {METRIC_KEYS.map((key) => {
                                        const total = comparison.totals[key];
                                        return (
                                            <div key={key} className="rounded-md border border-gray-200 p-3 dark:border-zinc-700">
                                                <div className="mb-1 text-xs font-medium text-gray-500">
                                                    {t(`evaluation.compareMetric${key[0].toUpperCase()}${key.slice(1)}`)}
                                                </div>
                                                <div className="flex items-baseline justify-between text-sm">
                                                    <span>{versionLabel(0)}</span>
                                                    <span className="font-medium">{pct(total?.v1 ?? null)}</span>
                                                </div>
                                                <div className="flex items-baseline justify-between text-sm">
                                                    <span>{versionLabel(1)}</span>
                                                    <span className="font-medium">{pct(total?.v2 ?? null)}</span>
                                                </div>
                                                <div className="mt-1 text-right text-xs">
                                                    <span className={deltaClass(total?.delta ?? null)}>
                                                        {total?.delta === null ? "" : `${total.delta >= 0 ? "+" : ""}${(total.delta * 100).toFixed(2)}%`}
                                                    </span>
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>
                            </div>

                            {/* Per-question table with metric switch */}
                            <div>
                                <div className="mb-2 flex items-center gap-2">
                                    <span className="text-sm font-medium">{t("evaluation.comparePerQuestion")}</span>
                                    <div className="flex gap-1">
                                        {METRIC_KEYS.map((key) => (
                                            <button
                                                key={key}
                                                type="button"
                                                onClick={() => setMetric(key)}
                                                className={`rounded px-2 py-0.5 text-xs ${
                                                    metric === key
                                                        ? "bg-blue-600 text-white"
                                                        : "bg-gray-100 text-gray-600 dark:bg-zinc-800 dark:text-zinc-300"
                                                }`}
                                            >
                                                {t(`evaluation.compareMetric${key[0].toUpperCase()}${key.slice(1)}`)}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                                <div className="overflow-x-auto rounded-md border border-gray-200 dark:border-zinc-700">
                                    <table className="w-full text-sm">
                                        <thead className="bg-gray-50 text-left text-xs text-gray-500 dark:bg-zinc-800">
                                            <tr>
                                                <th className="w-16 px-3 py-2">#</th>
                                                <th className="px-3 py-2">{t("evaluation.compareQuestion")}</th>
                                                <th className="w-28 px-3 py-2">{versionLabel(0)}</th>
                                                <th className="w-28 px-3 py-2">{versionLabel(1)}</th>
                                                <th className="w-20 px-3 py-2">{t("evaluation.compareVerdict")}</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {comparison.per_question.map((row, idx) => (
                                                <tr key={idx} className="border-t border-gray-100 dark:border-zinc-800">
                                                    <td className="px-3 py-2 text-gray-400">{idx + 1}</td>
                                                    <td className="max-w-96 px-3 py-2">
                                                        <div className="truncate" title={row.question}>
                                                            {row.question}
                                                        </div>
                                                    </td>
                                                    <td className="px-3 py-2">{pct(row.v1[metric] ?? null)}</td>
                                                    <td className="px-3 py-2">{pct(row.v2[metric] ?? null)}</td>
                                                    <td className="px-3 py-2">
                                                        <span className={`rounded px-2 py-0.5 text-xs ${verdictClass(row.verdict)}`}>
                                                            {t(`evaluation.compareVerdict${row.verdict[0].toUpperCase()}${row.verdict.slice(1)}`)}
                                                        </span>
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        </>
                            );
                        })()
                    )}
                </div>
            </SheetContent>
        </Sheet>
    );
}
