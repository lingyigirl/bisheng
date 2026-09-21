import LoadMore from "@/components/bs-comp/loadMore";
import { Button } from "@/components/bs-ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/bs-ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/bs-ui/table";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import {
    listRecordsPageApi,
    listTemplatesApi,
    type DocExtractRun,
    type DocExtractTemplate,
} from "@/controllers/API/docExtract";
import { useInfiniteCursorTable } from "@/util/hook";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

const ALL = "__all__";

/**
 * Extraction record browser — the troubleshooting surface behind spec AC-32.
 *
 * A record carries the extracted field values, so this is admin-only. It also
 * carries `preprocess_route` and `determined_by`, which is what actually
 * answers "was this the wrong type, or did parsing return nothing?" without
 * digging through logs.
 */
export function DocExtractRecords() {
    const { t } = useTranslation();

    const [templates, setTemplates] = useState<DocExtractTemplate[]>([]);
    const [templateId, setTemplateId] = useState<string>(ALL);
    const [status, setStatus] = useState<string>(ALL);
    const [expanded, setExpanded] = useState<number | null>(null);

    // F027: cursor-based infinite scroll, matching every other list in the
    // platform (知识库 / 知识空间 / 应用). The previous implementation exposed
    // 上一页 / 下一页 buttons, but a cursor is one-directional — "上一页"
    // simply re-fetched the first page and could never walk back through
    // history, so it was a no-op beyond page 2.
    const { data: rows, loading, hasMore, loadMore, filterData } = useInfiniteCursorTable<DocExtractRun>(
        // `unInitData` suppresses the hook's own mount-time fetch: the filter
        // effect below runs on mount too and resets the cursor, so leaving the
        // hook's initial load on would fire two identical requests per mount.
        { pageSize: 20, unInitData: true },
        (param) =>
            listRecordsPageApi({
                templateId: param.template_id,
                status: param.status,
                // The hook's cursor is an opaque string; this endpoint's cursor is
                // a numeric row id, so it is parsed back here.
                cursor: param.cursor == null ? null : Number(param.cursor),
                limit: param.pageSize,
            }),
    );

    useEffect(() => {
        captureAndAlertRequestErrorHoc(listTemplatesApi()).then((res) => {
            if (res) setTemplates(res);
        });
    }, []);

    // Any filter change restarts from the first page (cursor reset to null).
    // This is also the ONLY initial fetch — see `unInitData` above.
    useEffect(() => {
        filterData({
            template_id: templateId === ALL ? undefined : Number(templateId),
            status: status === ALL ? undefined : Number(status),
        });
        setExpanded(null);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [templateId, status]);

    return (
        <div className="space-y-4">
            <div className="flex gap-3">
                <Select value={templateId} onValueChange={setTemplateId}>
                    <SelectTrigger className="w-56">
                        <SelectValue placeholder={t("docExtract.filterTemplate", { ns: "bs" })} />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value={ALL}>{t("docExtract.allTemplates", { ns: "bs" })}</SelectItem>
                        {templates.map((tpl) => (
                            <SelectItem key={tpl.id} value={String(tpl.id)}>
                                {tpl.name}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>

                <Select value={status} onValueChange={setStatus}>
                    <SelectTrigger className="w-40">
                        <SelectValue placeholder={t("docExtract.filterStatus", { ns: "bs" })} />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value={ALL}>{t("docExtract.allStatus", { ns: "bs" })}</SelectItem>
                        <SelectItem value="1">{t("docExtract.statusSuccess", { ns: "bs" })}</SelectItem>
                        <SelectItem value="0">{t("docExtract.statusFailed", { ns: "bs" })}</SelectItem>
                    </SelectContent>
                </Select>
            </div>

            <Table>
                <TableHeader>
                    <TableRow>
                        <TableHead>{t("docExtract.docType", { ns: "bs" })}</TableHead>
                        <TableHead>{t("docExtract.status", { ns: "bs" })}</TableHead>
                        <TableHead>{t("docExtract.trialDeterminedBy", { ns: "bs" })}</TableHead>
                        <TableHead>{t("docExtract.trialRoute", { ns: "bs" })}</TableHead>
                        <TableHead>{t("docExtract.operator", { ns: "bs" })}</TableHead>
                        <TableHead>{t("docExtract.time", { ns: "bs" })}</TableHead>
                        <TableHead>{t("docExtract.actions", { ns: "bs" })}</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {rows.map((row) => (
                        <TableRow key={row.id}>
                            <TableCell>{row.doc_type}</TableCell>
                            <TableCell>
                                {row.status === 1
                                    ? t("docExtract.statusSuccess", { ns: "bs" })
                                    : t("docExtract.statusFailed", { ns: "bs" })}
                            </TableCell>
                            <TableCell>
                                {row.determined_by === "auto"
                                    ? t("docExtract.trialAuto", { ns: "bs" })
                                    : t("docExtract.trialExplicit", { ns: "bs" })}
                                {row.confidence != null ? ` (${row.confidence})` : ""}
                            </TableCell>
                            <TableCell>
                                {row.preprocess_route === "ocr"
                                    ? t("docExtract.routeOcr", { ns: "bs" })
                                    : t("docExtract.routeText", { ns: "bs" })}
                            </TableCell>
                            <TableCell>{row.user_id ?? "-"}</TableCell>
                            <TableCell>{row.create_time ?? "-"}</TableCell>
                            <TableCell>
                                <Button
                                    variant="link"
                                    onClick={() => setExpanded(expanded === row.id ? null : row.id)}
                                >
                                    {t("docExtract.detail", { ns: "bs" })}
                                </Button>
                            </TableCell>
                        </TableRow>
                    ))}
                </TableBody>
            </Table>

            {/* F027 sentinel: must stay inside the same scroll container as the
                table, otherwise it never intersects on in-container scroll. */}
            {hasMore && <LoadMore onScrollLoad={loadMore} />}

            {expanded != null && (
                <pre className="max-h-64 overflow-auto rounded bg-gray-50 p-3 text-xs">
                    {JSON.stringify(rows.find((r) => r.id === expanded), null, 2)}
                </pre>
            )}

            {!loading && rows.length === 0 && (
                <p className="py-6 text-center text-sm text-gray-500">
                    {t("docExtract.noRecord", { ns: "bs" })}
                </p>
            )}
        </div>
    );
}
