import { Button } from "@/components/bs-ui/button";
import { Input, Textarea } from "@/components/bs-ui/input";
import { Label } from "@/components/bs-ui/label";
import { useToast } from "@/components/bs-ui/toast/use-toast";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import {
    createTemplateApi,
    updateTemplateApi,
    type DocExtractFieldDef,
    type DocExtractTemplate,
} from "@/controllers/API/docExtract";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { FieldSchemaEditor } from "./DocExtractComponents/FieldSchemaEditor";
import { RenderTemplateEditor } from "./DocExtractComponents/RenderTemplateEditor";
import { TrialExtractDialog } from "./DocExtractComponents/TrialExtractDialog";

interface Props {
    /** Null = creating a new template. */
    template: DocExtractTemplate | null;
    onSaved: () => void;
    onCancel: () => void;
}

interface FormState {
    name: string;
    doc_type: string;
    description: string;
    field_schema: DocExtractFieldDef[];
    render_template: string;
}

function toForm(template: DocExtractTemplate | null): FormState {
    if (!template) {
        return { name: "", doc_type: "", description: "", field_schema: [], render_template: "" };
    }
    return {
        name: template.name ?? "",
        doc_type: template.doc_type ?? "",
        description: template.description ?? "",
        field_schema: template.field_schema ?? [],
        render_template: template.render_template ?? "",
    };
}

/**
 * Template create/edit form.
 *
 * The trial dialog is mounted next to the form rather than behind a save: its
 * whole purpose is to check a definition *before* committing to it, so it has
 * to be reachable with unsaved edits in place (spec AC-41).
 */
export function DocExtractTemplateEdit({ template, onSaved, onCancel }: Props) {
    const { t } = useTranslation();
    const { toast } = useToast();
    const [form, setForm] = useState<FormState>(() => toForm(template));
    const [saving, setSaving] = useState(false);
    const [trialOpen, setTrialOpen] = useState(false);

    const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
        setForm((prev) => ({ ...prev, [key]: value }));

    const save = async () => {
        setSaving(true);
        try {
            const payload = {
                name: form.name,
                doc_type: form.doc_type,
                description: form.description,
                field_schema: form.field_schema,
                render_template: form.render_template,
            };
            const res = template
                ? await captureAndAlertRequestErrorHoc(updateTemplateApi(template.id, payload))
                : await captureAndAlertRequestErrorHoc(createTemplateApi(payload));
            if (res) {
                toast({ variant: "success", description: t("docExtract.saveSuccess", { ns: "bs" }) });
                onSaved();
            }
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="space-y-5">
            <div className="grid grid-cols-2 gap-4">
                <div className="space-y-1">
                    <Label>{t("docExtract.name", { ns: "bs" })}</Label>
                    <Input
                        value={form.name}
                        placeholder={t("docExtract.namePlaceholder", { ns: "bs" })}
                        onChange={(e) => set("name", e.target.value)}
                    />
                </div>
                <div className="space-y-1">
                    <Label>{t("docExtract.docType", { ns: "bs" })}</Label>
                    <Input
                        value={form.doc_type}
                        placeholder={t("docExtract.docTypePlaceholder", { ns: "bs" })}
                        onChange={(e) => set("doc_type", e.target.value)}
                    />
                </div>
            </div>

            <div className="space-y-1">
                <Label>{t("docExtract.description", { ns: "bs" })}</Label>
                <Textarea
                    rows={3}
                    value={form.description}
                    placeholder={t("docExtract.descriptionHint", { ns: "bs" })}
                    onChange={(e) => set("description", e.target.value)}
                />
            </div>

            <div className="space-y-1">
                <Label>{t("docExtract.fieldSchema", { ns: "bs" })}</Label>
                <FieldSchemaEditor
                    value={form.field_schema}
                    onChange={(next) => set("field_schema", next)}
                />
            </div>

            <div className="space-y-1">
                <Label>{t("docExtract.renderTemplate", { ns: "bs" })}</Label>
                <RenderTemplateEditor
                    value={form.render_template}
                    fields={form.field_schema}
                    onChange={(next) => set("render_template", next)}
                />
            </div>

            <div className="flex gap-3">
                <Button disabled={saving} onClick={save}>
                    {t("docExtract.save", { ns: "bs" })}
                </Button>
                <Button variant="outline" onClick={() => setTrialOpen(true)}>
                    {t("docExtract.trialExtract", { ns: "bs" })}
                </Button>
                <Button variant="ghost" onClick={onCancel}>
                    {t("docExtract.cancel", { ns: "bs" })}
                </Button>
            </div>

            <TrialExtractDialog
                open={trialOpen}
                onOpenChange={setTrialOpen}
                templateId={template?.id ?? null}
                definition={{
                    description: form.description,
                    field_schema: form.field_schema,
                    render_template: form.render_template,
                }}
            />
        </div>
    );
}
