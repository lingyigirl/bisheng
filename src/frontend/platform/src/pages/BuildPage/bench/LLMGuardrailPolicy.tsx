import { Checkbox } from "@/components/bs-ui/checkBox";
import { Button } from "@/components/bs-ui/button";
import { Input, Textarea } from "@/components/bs-ui/input";
import { Label } from "@/components/bs-ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/bs-ui/radio-group";
import { Switch } from "@/components/bs-ui/switch";
import { useToast } from "@/components/bs-ui/toast/use-toast";
import {
    getPlatformPolicyApi,
    updatePlatformPolicyApi,
} from "@/controllers/API/platformSensitiveWord";
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
                            {/* The built-in list and the platform word library are two
                                independent sources (backend: `_resolve_words` reads the
                                static words.txt, the platform layer queries
                                `platform_sensitive_word`). Without this line the two sit
                                side by side in the same tab under similar names and read
                                as one library. */}
                            <p className="ml-6 text-xs text-[#86909C]">{t("build.builtinWordListHint")}</p>
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

/**
 * Platform-wide (cross-tenant) guardrail policy.
 *
 * Visually distinct from the tenant sections below it, and deliberately so: this
 * one is enforced on EVERY tenant and cannot be disabled by them (spec AC-08).
 * Rendering it with the same chrome as the tenant sections would invite a tenant
 * admin to assume they can turn it off — and they will not even see this page,
 * because the platform word endpoints are super-admin only.
 */
function PlatformPolicySection() {
    const { t } = useTranslation();
    const { toast } = useToast();
    const [enabled, setEnabled] = useState(false);
    const [action, setAction] = useState<"block" | "mask">("mask");
    const [autoReply, setAutoReply] = useState("");
    const [isDefaultReply, setIsDefaultReply] = useState(true);
    const [loaded, setLoaded] = useState(false);
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        getPlatformPolicyApi()
            .then((policy) => {
                setEnabled(Boolean(policy.enabled));
                setAction(policy.action === "block" ? "block" : "mask");
                setAutoReply(policy.auto_reply || "");
                setIsDefaultReply(Boolean(policy.auto_reply_is_default));
                setLoaded(true);
            })
            .catch(() => setLoaded(true));
    }, []);

    const save = async () => {
        if (!loaded) return;
        setSaving(true);
        const result = await captureAndAlertRequestErrorHoc(
            updatePlatformPolicyApi({ enabled, action, auto_reply: autoReply.trim() }),
        );
        setSaving(false);
        if (!result) return;
        // The server resolves an empty reply to the requirement's wording; mirror
        // that back so the box shows what users will actually receive.
        setAutoReply(result.auto_reply || "");
        setIsDefaultReply(Boolean(result.auto_reply_is_default));
        toast({ title: t("prompt"), variant: "success", description: t("updateSuccess") });
    };

    return (
        <div className="mb-6 rounded-lg border border-[#FFD8BF] bg-[#FFF9F5] p-5">
            <div className="flex items-center justify-between gap-4">
                <div>
                    <p className="text-lg font-bold">{t("platformPolicy.title")}</p>
                    <p className="mt-1 text-sm text-[#86909C]">{t("platformPolicy.desc")}</p>
                </div>
                <Switch checked={enabled} onCheckedChange={setEnabled} />
            </div>

            {enabled && (
                <div className="mt-4 w-full max-w-[560px] space-y-4 rounded-lg border border-[#ECECEC] bg-white p-4">
                    <div>
                        <span className="bisheng-label">{t("guardrail.outputAction")}</span>
                        <RadioGroup
                            className="mt-3 flex gap-6"
                            value={action}
                            onValueChange={(value: "block" | "mask") => setAction(value)}
                        >
                            <div className="flex items-center space-x-2">
                                <RadioGroupItem value="block" id="platform-block" />
                                <Label htmlFor="platform-block" className="cursor-pointer">
                                    {t("guardrail.actionBlock")}
                                </Label>
                            </div>
                            <div className="flex items-center space-x-2">
                                <RadioGroupItem value="mask" id="platform-mask" />
                                <Label htmlFor="platform-mask" className="cursor-pointer">
                                    {t("guardrail.actionMask")}
                                </Label>
                            </div>
                        </RadioGroup>
                    </div>

                    <div>
                        <span className="bisheng-label">{t("guardrail.autoReply")}</span>
                        <Input
                            className="mt-2"
                            value={autoReply}
                            onChange={(event) => {
                                setAutoReply(event.target.value);
                                setIsDefaultReply(false);
                            }}
                            placeholder={t("platformPolicy.defaultReplyPlaceholder")}
                        />
                        {isDefaultReply && <p className="mt-1 text-xs text-[#86909C]">{t("platformPolicy.defaultReplyHint")}</p>}
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

interface LLMGuardrailPolicyProps {
    /**
     * Render the cross-tenant platform sections. False for anyone but a global
     * super admin: those endpoints are guarded by `require_global_super_admin`,
     * and the 403 interceptor answers a failed GET by navigating the whole page
     * to /403. Rendering them anyway is not "an empty panel" — it is a redirect
     * out of the admin console.
     */
    showPlatformSections: boolean;
}

export function LLMGuardrailPolicy({ showPlatformSections }: LLMGuardrailPolicyProps) {
    const { t } = useTranslation();
    return (
        <div>
            {showPlatformSections && <PlatformPolicySection />}
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
