import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/bs-ui/sheet";
import { LoadingIcon } from "@/components/bs-icons/loading";
import {
    AgentTransactionRecord,
    ModelCallRecord,
    TransactionNodeRecord,
    getTransactionModelCallsApi,
    getTransactionNodesApi,
} from "@/controllers/API/log";
import { formatDate } from "@/util/utils";
import { NodeRunList } from "./NodeRunList";

interface TransactionDetailProps {
    transaction: AgentTransactionRecord | null;
    onClose: () => void;
}

function millisLabel(value: number | null): string {
    if (value === null || value === undefined) return "-";
    const seconds = value / 1000;
    return seconds < 1 ? `${value}ms` : `${seconds.toFixed(1)}s`;
}

function infoItem(label: string, value: string): JSX.Element {
    return (
        <div className="flex gap-2 text-sm">
            <span className="w-24 shrink-0 text-gray-500">{label}</span>
            <span className="break-all">{value || "-"}</span>
        </div>
    );
}

export function TransactionDetail({ transaction, onClose }: TransactionDetailProps) {
    const { t } = useTranslation();
    const [nodes, setNodes] = useState<TransactionNodeRecord[]>([]);
    const [modelCalls, setModelCalls] = useState<ModelCallRecord[]>([]);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        if (!transaction) return;
        setLoading(true);
        setNodes([]);
        setModelCalls([]);
        Promise.all([getTransactionNodesApi(transaction.trace_id), getTransactionModelCallsApi(transaction.trace_id)])
            .then(([nodesResp, callsResp]) => {
                setNodes(nodesResp.nodes);
                setModelCalls(callsResp.records);
            })
            .catch(() => {
                // keep the empty states; the header still shows run-level info
            })
            .finally(() => setLoading(false));
    }, [transaction]);

    const statusClass =
        transaction?.status === "success"
            ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300"
            : "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300";

    return (
        <Sheet open={!!transaction} onOpenChange={(open) => !open && onClose()}>
            {/* 2/3 screen width per user request (2026-09-05) */}
            <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-[66vw]">
                <SheetHeader>
                    <SheetTitle>{t("log.monitorDetail")}</SheetTitle>
                </SheetHeader>
                {transaction && (
                    <div className="space-y-6 px-1 pb-8">
                        {/* Run-level info */}
                        <div className="space-y-1.5 rounded-md border border-gray-200 p-3 dark:border-zinc-700">
                            <div className="mb-2 text-sm font-medium">{t("log.monitorRunInfo")}</div>
                            {infoItem(t("log.monitorTransactionId"), transaction.input_snapshot ?? "-")}
                            {infoItem(t("log.monitorApp"), transaction.app_name)}
                            {infoItem(t("log.monitorType"), transaction.app_type)}
                            {infoItem(t("log.monitorUser"), transaction.user_name)}
                            <div className="flex gap-2 text-sm">
                                <span className="w-24 shrink-0 text-gray-500">{t("log.monitorStatus")}</span>
                                <span className={`rounded px-2 py-0.5 text-xs font-medium ${statusClass}`}>
                                    {t(`log.monitorStatusEnum.${transaction.status === "success" ? "success" : "failed"}`)}
                                </span>
                                <span className="text-gray-500">{millisLabel(transaction.process_time)}</span>
                            </div>
                            {infoItem(t("log.monitorTime"), formatDate(new Date(transaction.time * 1000), "yyyy-MM-dd HH:mm:ss"))}
                        </div>

                        {/* Model calls of this run */}
                        <div>
                            <div className="mb-2 text-sm font-medium">{t("log.monitorModelCalls")}</div>
                            {modelCalls.length === 0 ? (
                                <div className="py-3 text-center text-sm text-gray-400">-</div>
                            ) : (
                                <div className="overflow-x-auto rounded-md border border-gray-200 dark:border-zinc-700">
                                    <table className="w-full text-sm">
                                        <thead className="bg-gray-50 text-left text-xs text-gray-500 dark:bg-zinc-800">
                                            <tr>
                                                <th className="px-3 py-2">{t("log.monitorModel")}</th>
                                                <th className="px-3 py-2">{t("log.monitorStatus")}</th>
                                                <th className="px-3 py-2">{t("log.monitorDuration")}</th>
                                                <th className="px-3 py-2">{t("log.monitorTokens")}</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {modelCalls.map((call, idx) => (
                                                <tr key={idx} className="border-t border-gray-100 dark:border-zinc-800">
                                                    <td className="px-3 py-2">{call.model_name || "-"}</td>
                                                    <td className="px-3 py-2">{call.status || "-"}</td>
                                                    <td className="px-3 py-2">{millisLabel(call.duration)}</td>
                                                    <td className="px-3 py-2">{call.total_token ?? "-"}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                        </div>

                        {/* Node execution view */}
                        <div>
                            <div className="mb-2 text-sm font-medium">{t("log.monitorNodeView")}</div>
                            {loading ? (
                                <div className="flex justify-center py-8">
                                    <LoadingIcon />
                                </div>
                            ) : (
                                <NodeRunList nodes={nodes} />
                            )}
                        </div>
                    </div>
                )}
            </SheetContent>
        </Sheet>
    );
}
