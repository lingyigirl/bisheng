import { Badge } from "@/components/bs-ui/badge";
import { Button } from "@/components/bs-ui/button";
import { Input } from "@/components/bs-ui/input";
import SelectSearch from "@/components/bs-ui/select/select";
import {
    getAllSensitiveWordHitLogsApi,
    getSensitiveWordHitLogsApi,
    type SensitiveWordHitLogItem,
} from "@/controllers/API/sensitiveWordHitLog";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import { userContext } from "@/contexts/userContext";
import { useContext, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

const PAGE_SIZE = 20;

/**
 * The value the daily-chat entry writes into `flow_id`.
 *
 * That entry point has no application (its request carries no app id), so it
 * records its entry TYPE instead — see `_WORKSTATION_FLOW_ID` in
 * `workstation/domain/services/chat_service.py`. The ws entries put a real
 * assistant/workflow id in the same column, so this is matched exactly rather
 * than pattern-guessed: anything else renders as the raw id.
 */
const DAILY_CHAT_FLOW_ID = "daily_chat";

function formatTime(value: string) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    const pad = (num: number) => String(num).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

export function LLMGuardrailHitLogs() {
    const { t } = useTranslation();
    const { user } = useContext(userContext);
    // F059: a global super admin owns the platform library, so they get the
    // cross-tenant view (AC-20). Everyone else stays tenant-scoped and sees
    // platform hit words redacted (AC-18) — the backend enforces both; this only
    // picks which endpoint to call.
    const isSuperAdmin = user?.role === "admin";
    const [rows, setRows] = useState<SensitiveWordHitLogItem[]>([]);
    const [total, setTotal] = useState(0);
    const [page, setPage] = useState(1);
    const [direction, setDirection] = useState("");
    const [flowId, setFlowId] = useState("");
    const [source, setSource] = useState("");
    const [loading, setLoading] = useState(false);
    // 查询条件快照：点击「查询」后生效，翻页沿用同一条件
    const [appliedDirection, setAppliedDirection] = useState("");
    const [appliedFlowId, setAppliedFlowId] = useState("");
    const [appliedSource, setAppliedSource] = useState("");

    const load = async (
        targetPage: number,
        nextDirection?: string,
        nextFlowId?: string,
        nextSource?: string,
    ) => {
        const useDirection = nextDirection !== undefined ? nextDirection : appliedDirection;
        const useFlowId = nextFlowId !== undefined ? nextFlowId : appliedFlowId;
        const useSource = nextSource !== undefined ? nextSource : appliedSource;
        setLoading(true);
        const query = {
            direction: useDirection || undefined,
            flow_id: useFlowId || undefined,
            source: useSource || undefined,
            page: targetPage,
            page_size: PAGE_SIZE,
        };
        const res = await captureAndAlertRequestErrorHoc(
            isSuperAdmin ? getAllSensitiveWordHitLogsApi(query) : getSensitiveWordHitLogsApi(query),
        );
        setLoading(false);
        if (res) {
            setRows(res.data || []);
            setTotal(res.total || 0);
            setPage(targetPage);
        }
    };

    useEffect(() => {
        load(1);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const search = async () => {
        if (loading) return;
        setAppliedDirection(direction);
        setAppliedFlowId(flowId.trim());
        setAppliedSource(source);
        await load(1, direction, flowId.trim(), source);
    };

    const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

    return (
        <div className="rounded-lg border border-[#ECECEC] bg-white p-5">
            <div>
                <p className="text-lg font-bold">{t("guardrail.hitLogs")}</p>
                <p className="mt-1 text-sm text-[#86909C]">{t("guardrail.hitLogsDesc")}</p>
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-3">
                <div className="flex items-center gap-2">
                    <span className="text-sm text-[#86909C]">{t("guardrail.direction")}</span>
                    <SelectSearch
                        selectClass="w-[140px]"
                        value={direction}
                        options={[
                            { label: t("guardrail.directionAll"), value: "" },
                            { label: t("guardrail.directionInput"), value: "input" },
                            { label: t("guardrail.directionOutput"), value: "output" },
                        ]}
                        onValueChange={setDirection}
                        onChange={() => undefined}
                    />
                </div>
                <div className="flex items-center gap-2">
                    <span className="text-sm text-[#86909C]">{t("guardrail.source")}</span>
                    <SelectSearch
                        selectClass="w-[140px]"
                        value={source}
                        options={[
                            { label: t("guardrail.sourceAll"), value: "" },
                            { label: t("guardrail.sourceTenant"), value: "tenant" },
                            { label: t("guardrail.sourcePlatform"), value: "platform" },
                        ]}
                        onValueChange={setSource}
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
                <table className="w-full min-w-[640px] text-sm">
                    <thead>
                        <tr className="border-b border-[#ECECEC] text-left text-[#86909C]">
                            <th className="py-2 pr-4 font-normal">{t("guardrail.hitTime")}</th>
                            <th className="py-2 pr-4 font-normal">{t("guardrail.source")}</th>
                            <th className="py-2 pr-4 font-normal">{t("guardrail.direction")}</th>
                            <th className="py-2 pr-4 font-normal">{t("guardrail.app")}</th>
                            <th className="py-2 pr-4 font-normal">{t("guardrail.words")}</th>
                            <th className="py-2 pr-4 font-normal">{t("guardrail.count")}</th>
                            <th className="py-2 pr-4 font-normal">{t("guardrail.action")}</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((row) => (
                            <tr key={row.id} className="border-b border-[#ECECEC] last:border-0">
                                <td className="py-2 pr-4">{formatTime(row.create_time)}</td>
                                <td className="py-2 pr-4">
                                    {row.source === "platform"
                                        ? t("guardrail.sourcePlatform")
                                        : t("guardrail.sourceTenant")}
                                </td>
                                <td className="py-2 pr-4">
                                    {row.direction === "input"
                                        ? t("guardrail.directionInput")
                                        : t("guardrail.directionOutput")}
                                </td>
                                <td className="py-2 pr-4">
                                    {row.flow_id === DAILY_CHAT_FLOW_ID
                                        ? t("guardrail.appDailyChat")
                                        : row.flow_id || "-"}
                                </td>
                                <td className="py-2 pr-4">
                                    <div className="flex flex-wrap gap-1">
                                        {(row.hits || []).map((hit) => (
                                            <Badge key={hit.word} variant="secondary">
                                                {hit.word}
                                            </Badge>
                                        ))}
                                        {/* Platform rows arrive with no words — the server
                                            strips them (INV-10). Say so, rather than
                                            rendering an empty cell that reads as a bug. */}
                                        {row.source === "platform" && (row.hits || []).length === 0 && (
                                            <span className="text-[#86909C]">{t("guardrail.wordsHidden")}</span>
                                        )}
                                    </div>
                                </td>
                                <td className="py-2 pr-4">
                                    {(row.hits || []).length > 0
                                        ? (row.hits || []).map((hit) => <div key={hit.word}>{hit.count}</div>)
                                        : row.hit_count || 0}
                                </td>
                                <td className="py-2 pr-4">
                                    {row.action === "block" ? t("guardrail.actionBlock") : t("guardrail.actionMask")}
                                </td>
                            </tr>
                        ))}
                        {!loading && rows.length === 0 && (
                            <tr>
                                <td colSpan={7} className="py-8 text-center text-[#86909C]">
                                    {t("guardrail.empty")}
                                </td>
                            </tr>
                        )}
                    </tbody>
                </table>
            </div>

            <div className="mt-4 flex items-center justify-end gap-3 text-sm">
                <span className="text-[#86909C]">{t("guardrail.totalRecords", { total })}</span>
                <Button
                    size="sm"
                    variant="outline"
                    disabled={page <= 1 || loading}
                    onClick={() => load(page - 1)}
                >
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
