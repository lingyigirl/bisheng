import { Button } from "@/components/bs-ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/bs-ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/bs-ui/table";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import {
    listRecordsApi,
    listTemplatesApi,
    type DocExtractRun,
    type DocExtractTemplate,
} from "@/controllers/API/docExtract";
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

    const [rows, setRows] = useState<DocExtractRun[]>([]);
    const [templates, setTemplates] = useState<DocExtractTemplate[]>([]);
    const [templateId, setTemplateId] = useState<string>(ALL);
    const [status, setStatus] = useState<string>(ALL);
    const [cursor, setCursor] = useState<number | null>(null);
    const [nextCursor, setNextCursor] = useState<number | null>(null);
    const [expanded, setExpanded] = useState<number | null>(null);

    const load = async (nextPage?: number | null) => {
        const res = await captureAndAlertRequestErrorHoc(
            listRecordsApi({
                templateId: templateId === ALL ? undefined : Number(templateId),
                status: status === ALL ? undefined : Number(status),
                cursor: nextPage ?? undefined,
            }),
        );
        if (res) {
            setRows(res.data ?? []);
            setNextCursor(res.next_cursor ?? null);
        }
    };

    useEffect(() => {
        captureAndAlertRequestErrorHoc(listTemplatesApi()).then((res) => {
            if (res) setTemplates(res);
        });
    }, []);

    useEffect(() => {
        setCursor(null);
        load(null);
        // `load` closes over templateId/status, which are the actual triggers;
        // listing it would refetch on every render since it is re-created each
        // time. The filter values are the real dependency.
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

            {expanded != null && (
                <pre className="max-h-64 overflow-auto rounded bg-gray-50 p-3 text-xs">
                    {JSON.stringify(rows.find((r) => r.id === expanded), null, 2)}
                </pre>
            )}

            {rows.length === 0 && (
                <p className="py-6 text-center text-sm text-gray-500">
                    {t("docExtract.noRecord", { ns: "bs" })}
                </p>
            )}

            <div className="flex justify-end gap-2">
                <Button
                    variant="outline"
                    disabled={cursor === null}
                    onClick={() => {
                        setCursor(null);
                        load(null);
                    }}
                >
                    {t("docExtract.prevPage", { ns: "bs" })}
                </Button>
                <Button
                    variant="outline"
                    disabled={nextCursor === null}
                    onClick={() => {
                        setCursor(nextCursor);
                        load(nextCursor);
                    }}
                >
                    {t("docExtract.nextPage", { ns: "bs" })}
                </Button>
            </div>
        </div>
    );
}
