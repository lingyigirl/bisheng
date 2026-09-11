import { Label } from "@/components/bs-ui/label";
import { QuestionTooltip } from "@/components/bs-ui/tooltip";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

interface OptionalNumberInputProps {
    data: {
        key?: string;
        value: number | null;
        min?: number;
        max?: number;
        step?: number;
        label?: boolean;
        help?: boolean;
    };
    onChange: (value: number | null) => void;
    i18nPrefix: string;
}

/**
 * F055 (AC-12/AC-13): optional numeric node param — an empty input is kept as
 * null (param unset, legacy default), unlike the integer-only InputItem which
 * forces '0'. Supports floats (score_threshold) via the template's `step`.
 */
export default function OptionalNumberInput({ data, onChange, i18nPrefix }: OptionalNumberInputProps) {
    const { t } = useTranslation('flow');
    const [text, setText] = useState(data.value === null || data.value === undefined ? '' : String(data.value));

    useEffect(() => {
        setText(data.value === null || data.value === undefined ? '' : String(data.value));
    }, [data.value]);

    const handleChange = (event: React.ChangeEvent<HTMLInputElement>) => {
        const raw = event.target.value;
        setText(raw);
        if (raw === '') {
            // F055: empty = unset — the backend falls back to legacy defaults.
            onChange(null);
            return;
        }
        const number = Number(raw);
        if (!Number.isNaN(number)) {
            onChange(number);
        }
    };

    return (
        <div className="node-item mb-4" data-key={data.key}>
            <Label className="flex items-center bisheng-label">
                {data.label && t(`${i18nPrefix}label`)}
                {data.help && <QuestionTooltip content={t(`${i18nPrefix}help`)} />}
            </Label>
            <input
                type="number"
                className="nodrag mt-2 w-full h-8 px-3 py-1 border rounded-md border-gray-300 bg-gray-50 focus:border-blue-500"
                value={text}
                min={data.min}
                max={data.max}
                step={data.step ?? 1}
                onChange={handleChange}
            />
        </div>
    );
}
