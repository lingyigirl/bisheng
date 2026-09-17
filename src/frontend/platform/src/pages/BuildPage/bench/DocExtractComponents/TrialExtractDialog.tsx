import { Button } from "@/components/bs-ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/bs-ui/dialog";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import {
    extractDocumentApi,
    type DocExtractFieldDef,
    type DocExtractResult,
} from "@/controllers/API/docExtract";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

interface Props {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    /** The template being edited. Null while creating a new one. */
    templateId: number | null;
    /** The editor's *unsaved* definition — this is what gets run (AC-41). */
    definition: {
        description: string;
        field_schema: DocExtractFieldDef[];
        render_template: string;
    };
}

/** Failure reason → i18n key. Mirrors the `reason` tokens the backend emits. */
const REASON_KEY: Record<string, string> = {
    doc_unreadable: "reasonDocUnreadable",
    over_size: "reasonOverSize",
    no_parser: "reasonNoParser",
    model_unconfigured: "reasonModelUnconfigured",
    model_failed: "reasonModelFailed",
    field_invalid: "reasonFieldInvalid",
    template_missing: "reasonTemplateMissing",
};

/**
 * Trial extraction (spec M-1b).
 *
 * Sends the editor's current definition rather than saving first, so the admin
 * can iterate on a template without publishing each attempt. The backend
 * validates that definition and refuses to run an invalid one; nothing is
 * written back to the template either way.
 */
export function TrialExtractDialog({ open, onOpenChange, templateId, definition }: Props) {
    const { t } = useTranslation();
    const fileRef = useRef<HTMLInputElement | null>(null);

    const [file, setFile] = useState<File | null>(null);
    const [running, setRunning] = useState(false);
    const [result, setResult] = useState<DocExtractResult | null>(null);

    const reset = () => {
        setFile(null);
        setResult(null);
    };

    const run = async () => {
        if (!file) return;
        setRunning(true);
        setResult(null);
        try {
            const res = await captureAndAlertRequestErrorHoc(
                extractDocumentApi({
                    file,
                    dryRun: true,
                    templateId: templateId ?? undefined,
                    trialDefinition: definition,
                }),
            );
            if (res) setResult(res);
        } finally {
            setRunning(false);
        }
    };

    const reasonText = (reason?: string | null) => {
        const key = REASON_KEY[reason ?? ""] ?? "reasonUnknown";
        return t(`docExtract.${key}`, { ns: "bs" });
    };

    return (
        <Dialog
            open={open}
            onOpenChange={(next) => {
                if (!next) reset();
                onOpenChange(next);
            }}
        >
            <DialogContent className="max-w-3xl">
                <DialogHeader>
                    <DialogTitle>{t("docExtract.trialTitle", { ns: "bs" })}</DialogTitle>
                </DialogHeader>

                <p className="text-sm text-gray-500">{t("docExtract.trialHint", { ns: "bs" })}</p>

                <div className="flex items-center gap-3">
                    <input
                        ref={fileRef}
                        type="file"
                        className="hidden"
                        onChange={(e) => {
                            setFile(e.target.files?.[0] ?? null);
                            setResult(null);
                        }}
                    />
                    <Button variant="outline" onClick={() => fileRef.current?.click()}>
                        {t("docExtract.trialUpload", { ns: "bs" })}
                    </Button>
                    <span className="truncate text-sm text-gray-600">{file?.name ?? ""}</span>
                    <Button disabled={!file || running} onClick={run}>
                        {running
                            ? t("docExtract.trialRunning", { ns: "bs" })
                            : t("docExtract.trialRun", { ns: "bs" })}
                    </Button>
                </div>

                {result && !result.ok && (
                    <div className="rounded border border-red-200 bg-red-50 p-3 text-sm">
                        <p className="font-medium text-red-700">
                            {t("docExtract.trialFailed", { ns: "bs" })}
                        </p>
                        <p className="mt-1 text-red-600">
                            {reasonText(result.reason)}
                            {result.error_code ? ` (${result.error_code})` : ""}
                        </p>
                        {result.error_message && (
                            <p className="mt-1 break-all text-xs text-red-500">
                                {result.error_message}
                            </p>
                        )}
                    </div>
                )}

                {result?.ok && (
                    <div className="space-y-3">
                        <div className="flex gap-6 text-sm text-gray-600">
                            <span>
                                {t("docExtract.trialRoute", { ns: "bs" })}:{" "}
                                {result.route === "ocr"
                                    ? t("docExtract.routeOcr", { ns: "bs" })
                                    : t("docExtract.routeText", { ns: "bs" })}
                            </span>
                            <span>
                                {t("docExtract.trialDeterminedBy", { ns: "bs" })}:{" "}
                                {result.determined_by === "auto"
                                    ? t("docExtract.trialAuto", { ns: "bs" })
                                    : t("docExtract.trialExplicit", { ns: "bs" })}
                            </span>
                            {result.confidence != null && (
                                <span>
                                    {t("docExtract.trialConfidence", { ns: "bs" })}:{" "}
                                    {result.confidence}
                                </span>
                            )}
                        </div>

                        <div>
                            <p className="mb-1 text-sm font-medium">
                                {t("docExtract.trialFields", { ns: "bs" })}
                            </p>
                            <table className="w-full text-sm">
                                <tbody>
                                    {(result.template?.field_schema ?? []).map((field) => {
                                        const missing = result.missing_fields?.includes(field.key);
                                        return (
                                            <tr key={field.key} className="border-b last:border-0">
                                                <td className="w-48 py-1 text-gray-600">
                                                    {field.label || field.key}
                                                </td>
                                                <td className={`py-1 ${missing ? "text-amber-600" : ""}`}>
                                                    {missing
                                                        ? t("docExtract.trialMissing", { ns: "bs" })
                                                        : String(result.fields?.[field.key] ?? "")}
                                                </td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>

                        <div>
                            <p className="mb-1 text-sm font-medium">
                                {t("docExtract.trialMarkdown", { ns: "bs" })}
                            </p>
                            <pre className="max-h-48 overflow-auto rounded bg-gray-50 p-2 text-xs">
                                {result.markdown}
                            </pre>
                        </div>
                    </div>
                )}
            </DialogContent>
        </Dialog>
    );
}
