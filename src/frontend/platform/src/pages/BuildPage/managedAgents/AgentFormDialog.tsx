import { Button } from "@/components/bs-ui/button"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/bs-ui/dialog"
import { Input, Textarea } from "@/components/bs-ui/input"
import { Label } from "@/components/bs-ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/bs-ui/select"
import { createManagedAgentApi, listTaxonomiesApi, updateManagedAgentApi } from "@/controllers/API/managedAgent"
import { captureAndAlertRequestErrorHoc } from "@/controllers/request"
import type {
    ManagedAgent,
    ManagedAgentCreate,
    ManagedAgentTaxonomy,
} from "@/types/api/managedAgent"
import { useEffect, useState } from "react"
import { useTranslation } from "react-i18next"

interface Props {
    open: boolean
    onClose: () => void
    agent: ManagedAgent | null
    onSaved: () => void
}

type FormState = {
    name: string
    description: string
    source_kind: ManagedAgentCreate["source_kind"]
    protocol: ManagedAgentCreate["protocol"]
    native_app_id: string
    native_app_type: string
    endpoint: string
    model_name: string
    health_method: "health_url" | "test_invoke"
    health_url: string
    scenario_code: string
    industry_code: string
    version: string
    agent_card: string
    credential_type: "none" | "bearer" | "api_key"
    credential_secret: string
    credential_headers: string
}

const EMPTY: FormState = {
    name: "",
    description: "",
    source_kind: "high_code",
    protocol: "openai_compatible",
    native_app_id: "",
    native_app_type: "10",
    endpoint: "",
    model_name: "",
    health_method: "test_invoke",
    health_url: "",
    scenario_code: "",
    industry_code: "",
    version: "",
    agent_card: "",
    credential_type: "none",
    credential_secret: "",
    credential_headers: "",
}

function fromAgent(agent: ManagedAgent): FormState {
    return {
        ...EMPTY,
        name: agent.name,
        description: agent.description || "",
        source_kind: agent.source_kind,
        protocol: agent.protocol,
        native_app_id: agent.native_app_id || "",
        native_app_type: String(agent.native_app_type || 10),
        endpoint: agent.endpoint || "",
        model_name: agent.model_name || "",
        health_method: agent.health_method,
        health_url: "",
        scenario_code: agent.scenario_code || "",
        industry_code: agent.industry_code || "",
        version: agent.version || "",
        credential_type: agent.credential_type,
        // Secret is never returned; blank means "unchanged" on edit.
        credential_secret: "",
    }
}

function parseJson(value: string, label: string): Record<string, unknown> | undefined {
    if (!value.trim()) return undefined
    try {
        return JSON.parse(value)
    } catch {
        throw new Error(`${label}: invalid JSON`)
    }
}

export function AgentFormDialog({ open, onClose, agent, onSaved }: Props) {
    const { t } = useTranslation("managedAgent")
    const [form, setForm] = useState<FormState>(EMPTY)
    const [taxonomies, setTaxonomies] = useState<ManagedAgentTaxonomy[]>([])
    const [saving, setSaving] = useState(false)
    const isEdit = Boolean(agent)

    useEffect(() => {
        if (!open) return
        setForm(agent ? fromAgent(agent) : EMPTY)
        captureAndAlertRequestErrorHoc(listTaxonomiesApi({ enabled_only: true })).then((res) => setTaxonomies(res || []))
    }, [open, agent])

    const set = <K extends keyof FormState>(key: K, value: FormState[K]) => {
        setForm((prev) => ({ ...prev, [key]: value }))
    }

    const submit = async () => {
        setSaving(true)
        try {
            const card = parseJson(form.agent_card, t("form.agentCard"))
            const headers = parseJson(form.credential_headers, t("form.credentialHeaders")) as
                | Record<string, string>
                | undefined
            if (headers && Object.values(headers).some((value) => typeof value !== "string")) {
                throw new Error(`${t("form.credentialHeaders")}: values must be strings`)
            }
            const payload: ManagedAgentCreate = {
                name: form.name.trim(),
                description: form.description.trim() || undefined,
                source_kind: form.source_kind,
                protocol: form.protocol,
                version: form.version.trim() || undefined,
                health_method: form.health_method,
                health_url: form.health_url.trim() || undefined,
                scenario_code: form.scenario_code || undefined,
                industry_code: form.industry_code || undefined,
                agent_card: card,
                credential:
                    form.credential_type === "none"
                        ? { auth_type: "none" }
                        : {
                              auth_type: form.credential_type,
                              secret: form.credential_secret || undefined,
                              headers,
                          },
            }
            if (form.source_kind === "native") {
                payload.protocol = "native"
                payload.native_app_id = form.native_app_id.trim()
                payload.native_app_type = Number(form.native_app_type)
            } else {
                payload.protocol = form.protocol === "native" ? "openai_compatible" : form.protocol
                payload.endpoint = form.endpoint.trim()
                payload.model_name = form.model_name.trim() || undefined
            }
            if (isEdit && agent) {
                await captureAndAlertRequestErrorHoc(updateManagedAgentApi(agent.id, payload))
            } else {
                await captureAndAlertRequestErrorHoc(createManagedAgentApi(payload))
            }
            onSaved()
            onClose()
        } catch (error) {
            // captureAndAlertRequestErrorHoc already surfaces request errors; local
            // JSON parse errors are shown through the same alert channel.
            if (error instanceof Error) {
                console.error(error)
            }
        } finally {
            setSaving(false)
        }
    }

    const scenarios = taxonomies.filter((one) => one.kind === "scenario")
    const industries = taxonomies.filter((one) => one.kind === "industry")

    return (
        <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
            <DialogContent className="max-w-2xl max-h-[85vh] overflow-y-auto">
                <DialogHeader>
                    <DialogTitle>{isEdit ? t("form.editTitle") : t("form.createTitle")}</DialogTitle>
                </DialogHeader>
                <div className="grid grid-cols-2 gap-4 py-2">
                    <div className="col-span-2">
                        <Label>{t("form.name")}</Label>
                        <Input value={form.name} onChange={(e) => set("name", e.target.value)} />
                    </div>
                    <div className="col-span-2">
                        <Label>{t("form.description")}</Label>
                        <Textarea value={form.description} onChange={(e) => set("description", e.target.value)} />
                    </div>
                    <div>
                        <Label>{t("form.sourceKind")}</Label>
                        <Select value={form.source_kind} onValueChange={(v) => set("source_kind", v as FormState["source_kind"])}>
                            <SelectTrigger><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="native">{t("source.native")}</SelectItem>
                                <SelectItem value="low_code">{t("source.low_code")}</SelectItem>
                                <SelectItem value="high_code">{t("source.high_code")}</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                    <div>
                        <Label>{t("form.protocol")}</Label>
                        <Select value={form.protocol} onValueChange={(v) => set("protocol", v as FormState["protocol"])}>
                            <SelectTrigger><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="native">{t("protocol.native")}</SelectItem>
                                <SelectItem value="openai_compatible">{t("protocol.openai_compatible")}</SelectItem>
                                <SelectItem value="custom_http">{t("protocol.custom_http")}</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                    {form.source_kind === "native" ? (
                        <>
                            <div>
                                <Label>{t("form.nativeAppId")}</Label>
                                <Input value={form.native_app_id} onChange={(e) => set("native_app_id", e.target.value)} />
                            </div>
                            <div>
                                <Label>{t("form.nativeAppType")}</Label>
                                <Select value={form.native_app_type} onValueChange={(v) => set("native_app_type", v)}>
                                    <SelectTrigger><SelectValue /></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="5">{t("form.assistant")}</SelectItem>
                                        <SelectItem value="10">{t("form.workflow")}</SelectItem>
                                    </SelectContent>
                                </Select>
                            </div>
                        </>
                    ) : (
                        <>
                            <div className="col-span-2">
                                <Label>{t("form.endpoint")}</Label>
                                <Input value={form.endpoint} onChange={(e) => set("endpoint", e.target.value)} />
                            </div>
                            <div>
                                <Label>{t("form.modelName")}</Label>
                                <Input value={form.model_name} onChange={(e) => set("model_name", e.target.value)} />
                            </div>
                        </>
                    )}
                    <div>
                        <Label>{t("form.version")}</Label>
                        <Input value={form.version} onChange={(e) => set("version", e.target.value)} />
                    </div>
                    <div>
                        <Label>{t("form.scenario")}</Label>
                        <Select
                            value={form.scenario_code || "__none"}
                            onValueChange={(v) => set("scenario_code", v === "__none" ? "" : v)}
                        >
                            <SelectTrigger><SelectValue placeholder={t("form.none")} /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="__none">{t("form.none")}</SelectItem>
                                {scenarios.map((one) => <SelectItem key={one.code} value={one.code}>{one.name_zh}</SelectItem>)}
                            </SelectContent>
                        </Select>
                    </div>
                    <div>
                        <Label>{t("form.industry")}</Label>
                        <Select
                            value={form.industry_code || "__none"}
                            onValueChange={(v) => set("industry_code", v === "__none" ? "" : v)}
                        >
                            <SelectTrigger><SelectValue placeholder={t("form.none")} /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="__none">{t("form.none")}</SelectItem>
                                {industries.map((one) => <SelectItem key={one.code} value={one.code}>{one.name_zh}</SelectItem>)}
                            </SelectContent>
                        </Select>
                    </div>
                    <div>
                        <Label>{t("form.healthMethod")}</Label>
                        <Select value={form.health_method} onValueChange={(v) => set("health_method", v as FormState["health_method"])}>
                            <SelectTrigger><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="test_invoke">{t("form.testInvoke")}</SelectItem>
                                <SelectItem value="health_url">{t("form.healthUrl")}</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                    <div>
                        <Label>{t("form.healthUrl")}</Label>
                        <Input value={form.health_url} onChange={(e) => set("health_url", e.target.value)} />
                    </div>
                    <div>
                        <Label>{t("form.credentialType")}</Label>
                        <Select value={form.credential_type} onValueChange={(v) => set("credential_type", v as FormState["credential_type"])}>
                            <SelectTrigger><SelectValue /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value="none">{t("form.none")}</SelectItem>
                                <SelectItem value="bearer">Bearer</SelectItem>
                                <SelectItem value="api_key">API Key</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                    <div>
                        <Label>{t("form.credentialSecret")}</Label>
                        <Input
                            type="password"
                            value={form.credential_secret}
                            placeholder={isEdit ? t("form.secretUnchanged") : ""}
                            onChange={(e) => set("credential_secret", e.target.value)}
                        />
                    </div>
                    <div className="col-span-2">
                        <Label>{t("form.credentialHeaders")}</Label>
                        <Textarea value={form.credential_headers} onChange={(e) => set("credential_headers", e.target.value)} />
                    </div>
                    <div className="col-span-2">
                        <Label>{t("form.agentCard")}</Label>
                        <Textarea
                            className="min-h-[120px] font-mono"
                            value={form.agent_card}
                            placeholder='{"version":"v1","inputs":[{"key":"query","type":"string","required":true}]}'
                            onChange={(e) => set("agent_card", e.target.value)}
                        />
                    </div>
                </div>
                <DialogFooter>
                    <Button variant="outline" onClick={onClose}>{t("form.cancel")}</Button>
                    <Button disabled={saving || !form.name.trim()} onClick={submit}>{t("form.save")}</Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    )
}
