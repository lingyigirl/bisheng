import { Button } from "@/components/bs-ui/button";
import { Input } from "@/components/bs-ui/input";
import { Switch } from "@/components/bs-ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/bs-ui/select";
import { Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { DocExtractFieldDef, DocExtractFieldType } from "@/controllers/API/docExtract";

const FIELD_TYPES: DocExtractFieldType[] = ["string", "number", "date", "boolean"];

const TYPE_LABEL_KEY: Record<DocExtractFieldType, string> = {
    string: "typeString",
    number: "typeNumber",
    date: "typeDate",
    boolean: "typeBoolean",
};

interface Props {
    value: DocExtractFieldDef[];
    onChange: (next: DocExtractFieldDef[]) => void;
}

/**
 * Row editor for a template's field definitions.
 *
 * A new row starts with `required: false`: for most certificate fields the
 * document genuinely may not carry the value, and defaulting to required makes
 * the first extraction attempt fail for a reason the admin did not intend.
 */
export function FieldSchemaEditor({ value, onChange }: Props) {
    const { t } = useTranslation();

    const update = (index: number, patch: Partial<DocExtractFieldDef>) => {
        onChange(value.map((row, i) => (i === index ? { ...row, ...patch } : row)));
    };

    const addRow = () => {
        onChange([...value, { key: "", label: "", type: "string", required: false }]);
    };

    const removeRow = (index: number) => {
        onChange(value.filter((_, i) => i !== index));
    };

    return (
        <div className="space-y-2">
            {value.length === 0 && (
                <p className="py-2 text-sm text-gray-500">{t("docExtract.noFieldsYet", { ns: "bs" })}</p>
            )}

            {value.map((row, index) => (
                <div key={index} className="flex items-center gap-2">
                    <Input
                        className="w-40"
                        placeholder={t("docExtract.fieldKey", { ns: "bs" })}
                        value={row.key}
                        onChange={(e) => update(index, { key: e.target.value })}
                    />
                    <Input
                        className="flex-1"
                        placeholder={t("docExtract.fieldLabel", { ns: "bs" })}
                        value={row.label}
                        onChange={(e) => update(index, { label: e.target.value })}
                    />
                    <Select
                        value={row.type}
                        onValueChange={(v) => update(index, { type: v as DocExtractFieldType })}
                    >
                        <SelectTrigger className="w-28">
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                            {FIELD_TYPES.map((type) => (
                                <SelectItem key={type} value={type}>
                                    {t(`docExtract.${TYPE_LABEL_KEY[type]}`, { ns: "bs" })}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    <div className="flex items-center gap-1">
                        <Switch
                            checked={row.required}
                            onCheckedChange={(checked) => update(index, { required: checked })}
                        />
                        <span className="text-sm text-gray-500">
                            {t("docExtract.fieldRequired", { ns: "bs" })}
                        </span>
                    </div>
                    <Button variant="ghost" size="icon" onClick={() => removeRow(index)}>
                        <Trash2 className="h-4 w-4" />
                    </Button>
                </div>
            ))}

            <Button variant="outline" size="sm" onClick={addRow}>
                <Plus className="mr-1 h-4 w-4" />
                {t("docExtract.addField", { ns: "bs" })}
            </Button>
        </div>
    );
}
