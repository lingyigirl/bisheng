import { Textarea } from "@/components/bs-ui/input";
import { useRef } from "react";
import { useTranslation } from "react-i18next";

import type { DocExtractFieldDef } from "@/controllers/API/docExtract";

interface Props {
    value: string;
    fields: DocExtractFieldDef[];
    onChange: (next: string) => void;
}

/**
 * Render-form editor with an insert-a-variable hint strip.
 *
 * The hint strip is the point of this component (spec AC-40): the render form
 * can only reference keys that exist in the field schema, and that is enforced
 * server-side at save time. Showing the available keys and letting the admin
 * click to insert is what keeps them from discovering the rule by being
 * rejected. It also removes the class of typo that a free-text box invites.
 */
export function RenderTemplateEditor({ value, fields, onChange }: Props) {
    const { t } = useTranslation();
    const textareaRef = useRef<HTMLTextAreaElement | null>(null);

    const usableFields = fields.filter((field) => field.key?.trim());

    /** Insert at the caret rather than appending, so the admin can compose in
     *  place instead of always editing the tail. */
    const insertVariable = (key: string) => {
        const token = `{{${key}}}`;
        const el = textareaRef.current;
        if (!el) {
            onChange(value + token);
            return;
        }
        const start = el.selectionStart ?? value.length;
        const end = el.selectionEnd ?? value.length;
        const next = value.slice(0, start) + token + value.slice(end);
        onChange(next);
        // Restore the caret after React re-renders, so consecutive inserts land
        // in sequence rather than jumping to the end.
        requestAnimationFrame(() => {
            el.focus();
            const caret = start + token.length;
            el.setSelectionRange(caret, caret);
        });
    };

    return (
        <div className="space-y-2">
            <Textarea
                ref={textareaRef as never}
                rows={6}
                value={value}
                placeholder={"## {{company_name}}\n{{credit_code}}"}
                onChange={(e) => onChange(e.target.value)}
            />

            <div className="text-sm text-gray-500">
                {usableFields.length > 0
                    ? t("docExtract.renderTemplateHint", { ns: "bs" })
                    : t("docExtract.noFieldsYet", { ns: "bs" })}
            </div>

            {usableFields.length > 0 && (
                <div className="flex flex-wrap gap-2">
                    {usableFields.map((field) => (
                        <button
                            key={field.key}
                            type="button"
                            onClick={() => insertVariable(field.key)}
                            className="rounded border border-gray-300 px-2 py-0.5 text-xs hover:bg-gray-100"
                        >
                            {`{{${field.key}}}`}
                            {field.label ? ` ${field.label}` : ""}
                        </button>
                    ))}
                </div>
            )}
        </div>
    );
}
