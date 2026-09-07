import { Checkbox } from "@/components/bs-ui/checkBox";
import { Button } from "@/components/bs-ui/button";
import { Input, Textarea } from "@/components/bs-ui/input";
import { Label } from "@/components/bs-ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/bs-ui/radio-group";
import { Switch } from "@/components/bs-ui/switch";
import { useToast } from "@/components/bs-ui/toast/use-toast";
import {
    CHAT_INPUT_POLICY,
    CHAT_OUTPUT_POLICY,
    getSensitiveWordPolicyApi,
    updateSensitiveWordPolicyApi,
    type SensitiveWordPolicy,
    type SensitiveWordType,
} from "@/controllers/API/sensitiveWordPolicy";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

interface DirectionForm {
    enabled: boolean;
    wordsTypes: SensitiveWordType[];
    customWords: string;
    autoReply: string;
    action: "block" | "mask";
}

const DEFAULT_FORM: DirectionForm = {
    enabled: false,
    wordsTypes: [],
    customWords: "",
    autoReply: "",
    action: "mask",
};

function fromPolicy(policy: SensitiveWordPolicy | null): DirectionForm {
    if (!policy) return { ...DEFAULT_FORM };
    const extra = policy.extra_config || {};
    return {
        enabled: Boolean(policy.enabled),
        wordsTypes: policy.words_types || [],
        customWords: policy.custom_words || "",
        autoReply: policy.auto_reply || "",
        action: extra.action === "block" ? "block" : "mask",
    };
}

function DirectionPolicySection({
    title,
    desc,
    businessType,
    showAction,
}: {
    title: string;
    desc: string;
    businessType: string;
    showAction: boolean;
}) {
    const { t } = useTranslation();
    const { toast } = useToast();
    const [form, setForm] = useState<DirectionForm>(DEFAULT_FORM);
    const [loaded, setLoaded] = useState(false);
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        getSensitiveWordPolicyApi(businessType)
            .then((policy) => {
                setForm(fromPolicy(policy));
                setLoaded(true);
            })
            .catch(() => setLoaded(true));
    }, [businessType]);

    const toggleWordType = (checked: boolean, type: SensitiveWordType) => {
        setForm((prev) => {
            const wordsTypes = prev.wordsTypes || [];
            if (checked && !wordsTypes.includes(type)) return { ...prev, wordsTypes: [...wordsTypes, type] };
            return { ...prev, wordsTypes: wordsTypes.filter((item) => item !== type) };
        });
    };

    const validate = (next: DirectionForm) => {
        if (!next.enabled) return true;
        const errors: string[] = [];
        if (!next.wordsTypes.length) errors.push(t("build.errors.selectAtLeastOneWordType"));
        if (next.wordsTypes.includes("custom") && !next.customWords.trim()) {
            errors.push(t("guardrail.customWordsEmpty"));
        }
        if (errors.length) {
            toast({ title: t("prompt"), variant: "error", description: errors.join(", ") });
            return false;
        }
        return true;
    };

    const save = async () => {
        if (!loaded) return;
        const next = { ...form, autoReply: form.autoReply.trim() || t("guardrail.defaultAutoReply") };
        if (!validate(next)) return;
        setSaving(true);
        const res = await captureAndAlertRequestErrorHoc(
            updateSensitiveWordPolicyApi(
                {
                    enabled: next.enabled,
                    words_types: next.wordsTypes,
                    custom_words: next.customWords,
                    auto_reply: next.autoReply,
                    extra_config: showAction ? { action: next.action } : {},
                },
                businessType,
            ),
        );
        setSaving(false);
        if (res) {
            setForm(next);
            toast({ title: t("prompt"), variant: "success", description: t("updateSuccess") });
        }
    };

    return (
        <div className="mb-6 rounded-lg border border-[#ECECEC] bg-white p-5">
            <div className="flex items-center justify-between gap-4">
                <div>
                    <p className="text-lg font-bold">{title}</p>
                    <p className="mt-1 text-sm text-[#86909C]">{desc}</p>
                </div>
                <Switch
                    checked={form.enabled}
                    onCheckedChange={(checked) => setForm((prev) => ({ ...prev, enabled: checked }))}
                />
            </div>

            {form.enabled && (
                <div className="mt-4 w-full max-w-[560px] space-y-4 rounded-lg border border-[#ECECEC] bg-[#FAFBFC] p-4">
                    <div>
                        <span className="bisheng-label">{t("build.wordListType")}</span>
                        <div className="mt-3 space-y-2">
                            <div className="flex items-center space-x-2">
                                <Checkbox
                                    id={`${businessType}-builtin`}
                                    checked={form.wordsTypes.includes("builtin")}
                                    onCheckedChange={(checked) => toggleWordType(Boolean(checked), "builtin")}
                                />
                                <Label htmlFor={`${businessType}-builtin`} className="cursor-pointer">
                                    {t("build.builtinWordList")}
                                </Label>
                            </div>
                            <div className="flex items-center space-x-2">
                                <Checkbox
                                    id={`${businessType}-custom`}
                                    checked={form.wordsTypes.includes("custom")}
                                    onCheckedChange={(checked) => toggleWordType(Boolean(checked), "custom")}
                                />
                                <Label htmlFor={`${businessType}-custom`} className="cursor-pointer">
                                    {t("build.customWordList")}
                                </Label>
                            </div>
                        </div>
                    </div>

                    {form.wordsTypes.includes("custom") && (
                        <div>
                            <span className="bisheng-label">{t("guardrail.customWords")}</span>
                            <Textarea
                                className="mt-2 h-[100px] resize-none"
                                value={form.customWords}
                                onChange={(event) => setForm((prev) => ({ ...prev, customWords: event.target.value }))}
                                placeholder={t("bench.customWordsNewlinePlaceholder")}
                            />
                        </div>
                    )}

                    {showAction && (
                        <div>
                            <span className="bisheng-label">{t("guardrail.outputAction")}</span>
                            <RadioGroup
                                className="mt-3 flex gap-6"
                                value={form.action}
                                onValueChange={(value: "block" | "mask") =>
                                    setForm((prev) => ({ ...prev, action: value }))
                                }
                            >
                                <div className="flex items-center space-x-2">
                                    <RadioGroupItem value="block" id={`${businessType}-block`} />
                                    <Label htmlFor={`${businessType}-block`} className="cursor-pointer">
                                        {t("guardrail.actionBlock")}
                                    </Label>
                                </div>
                                <div className="flex items-center space-x-2">
                                    <RadioGroupItem value="mask" id={`${businessType}-mask`} />
                                    <Label htmlFor={`${businessType}-mask`} className="cursor-pointer">
                                        {t("guardrail.actionMask")}
                                    </Label>
                                </div>
                            </RadioGroup>
                        </div>
                    )}

                    <div>
                        <span className="bisheng-label">{t("guardrail.autoReply")}</span>
                        <Input
                            className="mt-2"
                            value={form.autoReply}
                            onChange={(event) => setForm((prev) => ({ ...prev, autoReply: event.target.value }))}
                            placeholder={t("guardrail.defaultAutoReply")}
                        />
                    </div>

                    <div className="flex justify-end">
                        <Button size="sm" onClick={save} disabled={saving}>
                            {t("save")}
                        </Button>
                    </div>
                </div>
            )}
        </div>
    );
}

export function LLMGuardrailPolicy() {
    const { t } = useTranslation();
    return (
        <div>
            <DirectionPolicySection
                title={t("guardrail.chatInput")}
                desc={t("guardrail.chatInputDesc")}
                businessType={CHAT_INPUT_POLICY}
                showAction={false}
            />
            <DirectionPolicySection
                title={t("guardrail.chatOutput")}
                desc={t("guardrail.chatOutputDesc")}
                businessType={CHAT_OUTPUT_POLICY}
                showAction
            />
        </div>
    );
}
