import { useTranslation } from "react-i18next";
import {
    Bar,
    BarChart,
    CartesianGrid,
    Legend,
    Line,
    LineChart,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from "recharts";

import { CompareReportMetric, CompareReportQuestion } from "@/controllers/API/evaluate";

interface ReportChartsProps {
    /** the three percent effect metrics (shared % axis) */
    grouped: CompareReportMetric[];
    /** per-question rows, each carrying all six metrics for both versions */
    perQuestion: CompareReportQuestion[];
    /** the metric currently selected in the detail dropdown */
    metric: CompareReportMetric;
    v1Label: string;
    v2Label: string;
}

function value(side: CompareReportQuestion["v1"], key: string): number | null {
    if (!side) return null;
    const v = (side as unknown as Record<string, number | null>)[key];
    return typeof v === "number" ? v : null;
}

/**
 * F090 图表：分组柱状（准确率/F1/召回，同为 % 共用一根轴）+ 逐题折线（横轴题号，
 * 两版两条线）。逐题图始终只画**一个**指标 → 独立纵轴；延迟/Token 不会被塞进
 * 百分比轴（design 坑 1）。
 */
export function ReportCharts({ grouped, perQuestion, metric, v1Label, v2Label }: ReportChartsProps) {
    const { t } = useTranslation();
    const isPercent = metric.unit === "%";

    const barData = grouped.map((m) => ({ name: m.label, v1: m.v1, v2: m.v2 }));
    const lineData = perQuestion.map((row, idx) => ({
        name: `${idx + 1}`,
        v1: value(row.v1, metric.key),
        v2: value(row.v2, metric.key),
    }));

    const tick = (v: number) => (isPercent ? `${(v * 100).toFixed(0)}%` : `${v}`);

    return (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <div className="rounded-md border border-gray-200 p-3 dark:border-zinc-700">
                <div className="mb-2 text-sm font-medium">{t("evaluation.reportEffectChart")}</div>
                <ResponsiveContainer width="100%" height={260}>
                    <BarChart data={barData} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="name" />
                        <YAxis tickFormatter={(v) => `${(Number(v) * 100).toFixed(0)}%`} />
                        <Tooltip formatter={(v: number) => `${(v * 100).toFixed(2)}%`} />
                        <Legend />
                        <Bar dataKey="v1" name={v1Label} fill="#4f6ef7" radius={[3, 3, 0, 0]} />
                        <Bar dataKey="v2" name={v2Label} fill="#f59e0b" radius={[3, 3, 0, 0]} />
                    </BarChart>
                </ResponsiveContainer>
            </div>

            <div className="rounded-md border border-gray-200 p-3 dark:border-zinc-700">
                <div className="mb-2 text-sm font-medium">
                    {t("evaluation.reportPerQuestionChart")} · {metric.label}
                </div>
                <ResponsiveContainer width="100%" height={260}>
                    <LineChart data={lineData} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="name" />
                        <YAxis tickFormatter={tick} />
                        <Tooltip formatter={(v: number) => (isPercent ? `${(v * 100).toFixed(2)}%` : `${v}`)} />
                        <Legend />
                        <Line type="monotone" dataKey="v1" name={v1Label} stroke="#4f6ef7" connectNulls />
                        <Line type="monotone" dataKey="v2" name={v2Label} stroke="#f59e0b" connectNulls />
                    </LineChart>
                </ResponsiveContainer>
            </div>
        </div>
    );
}
