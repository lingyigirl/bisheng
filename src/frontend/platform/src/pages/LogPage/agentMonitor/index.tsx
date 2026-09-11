import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import FilterByApp from "@/components/bs-comp/filterTableDataComponent/FilterByApp";
import FilterByDate from "@/components/bs-comp/filterTableDataComponent/FilterByDate";
import { Button } from "@/components/bs-ui/button";
import { Input } from "@/components/bs-ui/input";
import AutoPagination from "@/components/bs-ui/pagination/autoPagination";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/bs-ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/bs-ui/table";
import { LoadingIcon } from "@/components/bs-icons/loading";
import { AgentTransactionRecord, getAgentTransactionsApi } from "@/controllers/API/log";
import { formatDate } from "@/util/utils";
import { TransactionDetail } from "./TransactionDetail";

const APP_TYPES = ["workflow", "assistant", "workstation", "linsight"] as const;

interface MonitorFilters {
    appId: string;
    appType: string;
    status: string;
    keyword: string;
    dateRange: [Date | null, Date | null];
}

const INIT_FILTERS: MonitorFilters = { appId: "", appType: "", status: "", keyword: "", dateRange: [null, null] };

function toEpochSeconds(date: Date | null, endOfDay: boolean): number | undefined {
    if (!date) return undefined;
    const target = new Date(date);
    if (endOfDay) {
        target.setHours(23, 59, 59, 999);
    } else {
        target.setHours(0, 0, 0, 0);
    }
    return Math.floor(target.getTime() / 1000);
}

function durationLabel(processTimeMs: number | null | undefined): string {
    if (processTimeMs === null || processTimeMs === undefined) return "-";
    const seconds = processTimeMs / 1000;
    return seconds < 1 ? `${processTimeMs}ms` : `${seconds.toFixed(1)}s`;
}

export function AgentMonitor() {
    const { t } = useTranslation();
    const [records, setRecords] = useState<AgentTransactionRecord[]>([]);
    const [total, setTotal] = useState(0);
    const [page, setPage] = useState(1);
    const [loading, setLoading] = useState(false);
    const [filters, setFilters] = useState<MonitorFilters>(INIT_FILTERS);
    const [applied, setApplied] = useState<MonitorFilters>(INIT_FILTERS);
    const [current, setCurrent] = useState<AgentTransactionRecord | null>(null);

    const load = useCallback(
        (p: number, f: MonitorFilters) => {
            setLoading(true);
            const [start, end] = f.dateRange;
            getAgentTransactionsApi({
                page: p,
                page_size: 20,
                app_id: f.appId || undefined,
                app_type: f.appType || undefined,
                status: f.status || undefined,
                keyword: f.keyword || undefined,
                start_time: toEpochSeconds(start, false),
                end_time: toEpochSeconds(end, true),
            })
                .then((res) => {
                    setRecords(res.records);
                    setTotal(res.total);
                })
                .catch(() => {
                    setRecords([]);
                    setTotal(0);
                })
                .finally(() => setLoading(false));
        },
        []
    );

    useEffect(() => {
        load(page, applied);
    }, [page, applied, load]);

    const handleSearch = () => {
        setPage(1);
        setApplied(filters);
    };

    const handleReset = () => {
        setFilters(INIT_FILTERS);
        setPage(1);
        setApplied(INIT_FILTERS);
    };

    const statusClass = (status: string) =>
        status === "success"
            ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300"
            : "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300";

    return (
        <div className="relative">
            {loading && (
                <div className="absolute left-0 top-0 z-10 flex h-full w-full items-center justify-center bg-white/80 dark:bg-zinc-900/80">
                    <LoadingIcon />
                </div>
            )}
            <div className="h-[calc(100vh-128px-var(--license-banner-h,0px))] overflow-y-auto px-2 py-4 pb-10">
                <div className="flex flex-wrap items-center gap-4">
                    <FilterByApp
                        value={filters.appId ? [filters.appId] : []}
                        placeholder={t("log.monitorApp")}
                        onChange={(value: string[]) => setFilters({ ...filters, appId: value[0] || "" })}
                    />
                    <Select
                        value={filters.appType}
                        onValueChange={(value) => setFilters({ ...filters, appType: value })}
                    >
                        <SelectTrigger className="w-[160px]">
                            <SelectValue placeholder={t("log.monitorType")} />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectGroup>
                                {APP_TYPES.map((tp) => (
                                    <SelectItem value={tp} key={tp}>
                                        {tp}
                                    </SelectItem>
                                ))}
                            </SelectGroup>
                        </SelectContent>
                    </Select>
                    <Select value={filters.status} onValueChange={(value) => setFilters({ ...filters, status: value })}>
                        <SelectTrigger className="w-[140px]">
                            <SelectValue placeholder={t("log.monitorStatus")} />
                        </SelectTrigger>
                        <SelectContent>
                            <SelectGroup>
                                <SelectItem value="success">{t("log.monitorStatusEnum.success")}</SelectItem>
                                <SelectItem value="failed">{t("log.monitorStatusEnum.failed")}</SelectItem>
                            </SelectGroup>
                        </SelectContent>
                    </Select>
                    <FilterByDate
                        value={filters.dateRange}
                        placeholders={[t("log.startDate"), t("log.endDate")]}
                        onChange={(value: [Date | null, Date | null]) => setFilters({ ...filters, dateRange: value })}
                    />
                    <div className="w-[220px]">
                        <Input
                            value={filters.keyword}
                            placeholder={t("log.monitorKeywordPlaceholder")}
                            onChange={(e) => setFilters({ ...filters, keyword: e.target.value })}
                            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
                        />
                    </div>
                    <Button className="px-6" onClick={handleSearch}>
                        {t("log.searchButton")}
                    </Button>
                    <Button variant="outline" onClick={handleReset}>
                        {t("log.resetButton")}
                    </Button>
                    <Button variant="outline" onClick={() => load(page, applied)}>
                        {t("log.monitorRefresh")}
                    </Button>
                </div>
                <Table className="mb-[50px]">
                    <TableHeader>
                        <TableRow>
                            <TableHead className="min-w-[240px]">{t("log.monitorTransactionId")}</TableHead>
                            <TableHead className="w-[200px]">{t("log.monitorApp")}</TableHead>
                            <TableHead className="w-[120px]">{t("log.monitorType")}</TableHead>
                            <TableHead className="w-[120px]">{t("log.monitorUser")}</TableHead>
                            <TableHead className="w-[100px]">{t("log.monitorStatus")}</TableHead>
                            <TableHead className="w-[100px]">{t("log.monitorDuration")}</TableHead>
                            <TableHead className="w-[180px]">{t("log.monitorTime")}</TableHead>
                            <TableHead className="w-[100px]">{t("log.monitorDetails")}</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {records.map((record) => (
                            <TableRow key={record.trace_id} className="cursor-pointer" onClick={() => setCurrent(record)}>
                                <TableCell>
                                    <div className="max-w-[400px] break-all">{record.input_snapshot || "-"}</div>
                                    <div className="mt-1 max-w-[400px] break-all text-xs text-muted-foreground">
                                        {t("log.monitorTraceId")}: {record.trace_id || "-"}
                                    </div>
                                </TableCell>
                                <TableCell>{record.app_name}</TableCell>
                                <TableCell>{record.app_type}</TableCell>
                                <TableCell>{record.user_name}</TableCell>
                                <TableCell>
                                    <span className={`rounded px-2 py-0.5 text-xs font-medium ${statusClass(record.status)}`}>
                                        {t(
                                            `log.monitorStatusEnum.${record.status === "success" ? "success" : "failed"}`
                                        )}
                                    </span>
                                </TableCell>
                                <TableCell>{durationLabel(record.process_time)}</TableCell>
                                <TableCell>{formatDate(new Date(record.time * 1000), "yyyy-MM-dd HH:mm:ss")}</TableCell>
                                <TableCell className="text-blue-600">{t("log.monitorDetails")}</TableCell>
                            </TableRow>
                        ))}
                        {records.length === 0 && !loading && (
                            <TableRow>
                                <TableCell colSpan={8} className="h-40 text-center text-gray-400">
                                    {t("build.empty")}
                                </TableCell>
                            </TableRow>
                        )}
                    </TableBody>
                </Table>
            </div>
            <div className="bisheng-table-footer px-6 bg-background-login">
                <div className="flex items-center gap-2">
                    <p className="desc">{t("log.auditManagement")}</p>
                </div>
                <AutoPagination
                    className="float-right justify-end w-full mr-6"
                    page={page}
                    pageSize={20}
                    total={total}
                    showTotal={true}
                    onChange={(newPage) => setPage(newPage)}
                />
            </div>
            <TransactionDetail transaction={current} onClose={() => setCurrent(null)} />
        </div>
    );
}
