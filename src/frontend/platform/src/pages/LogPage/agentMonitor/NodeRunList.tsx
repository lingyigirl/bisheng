import { useState } from "react";
import { useTranslation } from "react-i18next";
import { TransactionNodeRecord } from "@/controllers/API/log";
import { JsonTree } from "./JsonTree";

const statusStyle: Record<TransactionNodeRecord["status"], string> = {
    running: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
    success: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
    failed: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
};

function durationText(duration: number | null): string {
    if (duration === null) return "-";
    if (duration < 1) return "<1s";
    return `${duration}s`;
}

function NodeRow({ node }: { node: TransactionNodeRecord }) {
    const { t } = useTranslation();
    const [expanded, setExpanded] = useState(false);

    return (
        <div className="rounded-md border border-gray-200 dark:border-zinc-700">
            <button
                type="button"
                onClick={() => setExpanded(!expanded)}
                className="flex w-full items-center gap-3 px-3 py-2 text-left text-sm hover:bg-gray-50 dark:hover:bg-zinc-800"
            >
                <span className={`shrink-0 rounded px-2 py-0.5 text-xs font-medium ${statusStyle[node.status]}`}>
                    {t(`log.monitorStatusEnum.${node.status}`)}
                    {node.incomplete ? ` · ${t("log.monitorIncomplete")}` : ""}
                </span>
                <span className="max-w-56 truncate font-medium">{node.node_name}</span>
                <span className="ml-auto shrink-0 text-xs text-gray-500">
                    {node.start_time || "-"} ~ {node.end_time || "-"} · {durationText(node.duration)}
                </span>
            </button>
            {expanded && (
                <div className="space-y-3 border-t border-gray-100 px-3 py-3 dark:border-zinc-800">
                    {node.reason && (
                        <div>
                            <div className="mb-1 text-xs font-medium text-gray-500">{t("log.monitorReason")}</div>
                            <div className="break-all rounded bg-red-50 p-2 text-xs text-red-700 dark:bg-red-900/30 dark:text-red-300">
                                {node.reason}
                            </div>
                        </div>
                    )}
                    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                        <div>
                            <div className="mb-1 text-xs font-medium text-gray-500">{t("log.monitorInput")}</div>
                            <JsonTree data={node.input_data} />
                        </div>
                        <div>
                            <div className="mb-1 text-xs font-medium text-gray-500">{t("log.monitorOutput")}</div>
                            <JsonTree data={node.output_data} />
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

export function NodeRunList({ nodes }: { nodes: TransactionNodeRecord[] }) {
    const { t } = useTranslation();
    if (nodes.length === 0) {
        return <div className="py-8 text-center text-sm text-gray-400">{t("log.monitorNoNodes")}</div>;
    }
    return (
        <div className="space-y-2">
            {nodes.map((node) => (
                <NodeRow key={node.node_id} node={node} />
            ))}
        </div>
    );
}
