import { Button } from "@/components/bs-ui/button";
import { Input } from "@/components/bs-ui/input";
import SelectSearch from "@/components/bs-ui/select/select";
import {
    getAllFeedbackEventsApi,
    getFeedbackEventsApi,
    type FeedbackEvent,
    type FeedbackEventQuery,
} from "@/controllers/API/feedbackEvent";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import { userContext } from "@/contexts/userContext";
import { useContext, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

const PAGE_SIZE = 20;

function formatTime(value: string | null) {
    if (!value) return "-";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    const pad = (num: number) => String(num).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

/**
 * Append-only rating trail (谁在何时给哪条回答改成了什么评价).
 *
 * Unlike the like/dislike counters on the message row — which only ever hold the
 * CURRENT verdict — every action is a row here, including cancels and changes of
 * mind. A user who likes, cancels and then dislikes leaves three rows, so the
 * real behaviour is recoverable after the fact (spec AC-22).
 */
export function FeedbackEventLogs() {
    const { t } = useTranslation();
    const { user } = useContext(userContext);
    // F059: the platform admin sees every tenant's ratings (AC-26); a tenant
    // admin sees only their own (AC-25). The backend scopes each path; this just
    // chooses which one to call.
    const isSuperAdmin = user?.role === "admin";
    const [rows, setRows] = useState<FeedbackEvent[]>([]);
    const [total, setTotal] = useState(0);
    const [page, setPage] = useState(1);
    const [feedbackType, setFeedbackType] = useState("");
    const [flowId, setFlowId] = useState("");
    const [loading, setLoading] = useState(false);
    // 查询条件快照：点击「查询」后生效，翻页沿用同一条件
    const [applied, setApplied] = useState<FeedbackEventQuery>({});

    const load = async (targetPage: number, query?: FeedbackEventQuery) => {
        const useQuery = query ?? applied;
        setLoading(true);
        const params = { ...useQuery, page: targetPage, page_size: PAGE_SIZE };
        const res = await captureAndAlertRequestErrorHoc(
            isSuperAdmin ? getAllFeedbackEventsApi(params) : getFeedbackEventsApi(params),
        );
        setLoading(false);
        if (res) {
            setRows(res.data || []);
            setTotal(res.total || 0);
            setPage(targetPage);
        }
    };

    useEffect(() => {
        load(1, {});
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const search = async () => {
        if (loading) return;
        const next: FeedbackEventQuery = {
            flow_id: flowId.trim() || undefined,
            feedback_type: feedbackType === "" ? undefined : Number(feedbackType),
        };
        setApplied(next);
        await load(1, next);
    };

    const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

    const valueLabel = (value: number) => {
        if (value === 1) return t("feedbackLogs.valueLiked");
        if (value === 2) return t("feedbackLogs.valueDisliked");
        return t("feedbackLogs.valueNone");
    };

    return (
        <div className="mt-6 rounded-lg border border-[#ECECEC] bg-white p-5">
            <div>
                <p className="text-lg font-bold">{t("feedbackLogs.title")}</p>
                <p className="mt-1 text-sm text-[#86909C]">{t("feedbackLogs.desc")}</p>
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-3">
                <div className="flex items-center gap-2">
                    <span className="text-sm text-[#86909C]">{t("feedbackLogs.type")}</span>
                    <SelectSearch
                        selectClass="w-[140px]"
                        value={feedbackType}
                        options={[
                            { label: t("feedbackLogs.typeAll"), value: "" },
                            { label: t("feedbackLogs.valueLiked"), value: "1" },
                            { label: t("feedbackLogs.valueDisliked"), value: "2" },
                        ]}
                        onValueChange={setFeedbackType}
                        onChange={() => undefined}
                    />
                </div>
                <div className="flex items-center gap-2">
                    <span className="text-sm text-[#86909C]">{t("guardrail.app")}</span>
                    <Input
                        className="w-[220px]"
                        value={flowId}
                        onChange={(event) => setFlowId(event.target.value)}
                        placeholder={t("guardrail.appPlaceholder")}
                    />
                </div>
                <Button size="sm" onClick={search} disabled={loading}>
                    {t("search")}
                </Button>
            </div>

            <div className="mt-4 overflow-x-auto">
                <table className="w-full min-w-[720px] text-sm">
                    <thead>
                        <tr className="border-b border-[#ECECEC] text-left text-[#86909C]">
                            <th className="py-2 pr-4 font-normal">{t("feedbackLogs.time")}</th>
                            <th className="py-2 pr-4 font-normal">{t("feedbackLogs.operator")}</th>
                            <th className="py-2 pr-4 font-normal">{t("feedbackLogs.message")}</th>
                            <th className="py-2 pr-4 font-normal">{t("guardrail.app")}</th>
                            <th className="py-2 pr-4 font-normal">{t("feedbackLogs.change")}</th>
                            <th className="py-2 pr-4 font-normal">{t("feedbackLogs.comment")}</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((row) => (
                            <tr key={row.id} className="border-b border-[#ECECEC] last:border-0">
                                <td className="py-2 pr-4">{formatTime(row.create_time)}</td>
                                <td className="py-2 pr-4">{row.user_id ?? "-"}</td>
                                <td className="py-2 pr-4">{row.message_id}</td>
                                <td className="py-2 pr-4">{row.flow_id || "-"}</td>
                                <td className="py-2 pr-4">
                                    <span className="text-[#86909C]">{valueLabel(row.from_value)}</span>
                                    <span className="mx-1">→</span>
                                    <span className="font-medium">{valueLabel(row.to_value)}</span>
                                </td>
                                <td className="max-w-[240px] break-words py-2 pr-4">{row.comment || "-"}</td>
                            </tr>
                        ))}
                        {!loading && rows.length === 0 && (
                            <tr>
                                <td colSpan={6} className="py-8 text-center text-[#86909C]">
                                    {t("feedbackLogs.empty")}
                                </td>
                            </tr>
                        )}
                    </tbody>
                </table>
            </div>

            <div className="mt-4 flex items-center justify-end gap-3 text-sm">
                <span className="text-[#86909C]">{t("guardrail.totalRecords", { total })}</span>
                <Button size="sm" variant="outline" disabled={page <= 1 || loading} onClick={() => load(page - 1)}>
                    {t("guardrail.previous")}
                </Button>
                <span>
                    {page} / {totalPages}
                </span>
                <Button
                    size="sm"
                    variant="outline"
                    disabled={page >= totalPages || loading}
                    onClick={() => load(page + 1)}
                >
                    {t("guardrail.next")}
                </Button>
            </div>
        </div>
    );
}
