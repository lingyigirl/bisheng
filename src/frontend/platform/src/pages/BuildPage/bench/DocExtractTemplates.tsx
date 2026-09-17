import { bsConfirm } from "@/components/bs-ui/alertDialog/useConfirm";
import { Button } from "@/components/bs-ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/bs-ui/table";
import { useToast } from "@/components/bs-ui/toast/use-toast";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import {
    deleteTemplateApi,
    listTemplatesApi,
    setTemplateEnabledApi,
    type DocExtractTemplate,
} from "@/controllers/API/docExtract";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { DocExtractTemplateEdit } from "./DocExtractTemplateEdit";

/**
 * Template list + create/edit switch.
 *
 * A simple two-mode view rather than a router: the whole feature lives inside
 * one tab of the workbench page, and a nested route would add a URL concept the
 * surrounding page does not have.
 */
export function DocExtractTemplates() {
    const { t } = useTranslation();
    const { toast } = useToast();

    const [templates, setTemplates] = useState<DocExtractTemplate[]>([]);
    const [editing, setEditing] = useState<DocExtractTemplate | null>(null);
    const [creating, setCreating] = useState(false);

    const load = async () => {
        const res = await captureAndAlertRequestErrorHoc(listTemplatesApi());
        if (res) setTemplates(res);
    };

    useEffect(() => {
        load();
    }, []);

    const toggle = async (row: DocExtractTemplate) => {
        const res = await captureAndAlertRequestErrorHoc(
            setTemplateEnabledApi(row.id, row.enabled !== 1),
        );
        if (res) load();
    };

    const remove = (row: DocExtractTemplate) => {
        bsConfirm({
            title: t("docExtract.delete", { ns: "bs" }),
            desc: t("docExtract.confirmDelete", { ns: "bs" }),
            showClose: true,
            okTxt: t("docExtract.delete", { ns: "bs" }),
            canelTxt: t("docExtract.cancel", { ns: "bs" }),
            async onOk(next) {
                const res = await captureAndAlertRequestErrorHoc(deleteTemplateApi(row.id));
                if (res !== undefined) {
                    toast({ variant: "success", description: t("docExtract.deleteSuccess", { ns: "bs" }) });
                    load();
                }
                next?.();
            },
        });
    };

    if (creating || editing) {
        return (
            <DocExtractTemplateEdit
                template={editing}
                onSaved={() => {
                    setCreating(false);
                    setEditing(null);
                    load();
                }}
                onCancel={() => {
                    setCreating(false);
                    setEditing(null);
                }}
            />
        );
    }

    return (
        <div className="space-y-4">
            <div className="flex justify-end">
                <Button onClick={() => setCreating(true)}>
                    {t("docExtract.newTemplate", { ns: "bs" })}
                </Button>
            </div>

            <Table>
                <TableHeader>
                    <TableRow>
                        <TableHead>{t("docExtract.name", { ns: "bs" })}</TableHead>
                        <TableHead>{t("docExtract.docType", { ns: "bs" })}</TableHead>
                        <TableHead>{t("docExtract.columns", { ns: "bs" })}</TableHead>
                        <TableHead>{t("docExtract.status", { ns: "bs" })}</TableHead>
                        <TableHead>{t("docExtract.actions", { ns: "bs" })}</TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {templates.map((row) => (
                        <TableRow key={row.id}>
                            <TableCell>{row.name}</TableCell>
                            <TableCell>{row.doc_type}</TableCell>
                            <TableCell>{(row.field_schema ?? []).length}</TableCell>
                            <TableCell>
                                {row.enabled === 1
                                    ? t("docExtract.enabled", { ns: "bs" })
                                    : t("docExtract.disabled", { ns: "bs" })}
                            </TableCell>
                            <TableCell className="space-x-2">
                                <Button variant="link" onClick={() => setEditing(row)}>
                                    {t("docExtract.edit", { ns: "bs" })}
                                </Button>
                                <Button variant="link" onClick={() => toggle(row)}>
                                    {row.enabled === 1
                                        ? t("docExtract.disable", { ns: "bs" })
                                        : t("docExtract.enable", { ns: "bs" })}
                                </Button>
                                <Button variant="link" onClick={() => remove(row)}>
                                    {t("docExtract.delete", { ns: "bs" })}
                                </Button>
                            </TableCell>
                        </TableRow>
                    ))}
                </TableBody>
            </Table>

            {templates.length === 0 && (
                <p className="py-6 text-center text-sm text-gray-500">
                    {t("docExtract.noTemplates", { ns: "bs" })}
                </p>
            )}
        </div>
    );
}
