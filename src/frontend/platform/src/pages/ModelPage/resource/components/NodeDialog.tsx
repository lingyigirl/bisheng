// @ts-strict-ignore
import { Button } from "@/components/bs-ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/bs-ui/dialog";
import { Input } from "@/components/bs-ui/input";
import { useToast } from "@/components/bs-ui/toast/use-toast";
import { createGpuNodeApi, testGpuNodeApi, updateGpuNodeApi } from "@/controllers/API/gpu";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import type { GpuNodeItem } from "@/types/api/gpu";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

interface NodeDialogProps {
    open: boolean;
    node?: GpuNodeItem | null;
    onOpenChange: (open: boolean) => void;
    onSaved: () => void;
}

export function NodeDialog({ open, node, onOpenChange, onSaved }: NodeDialogProps) {
    const { t } = useTranslation();
    const { message } = useToast();
    const isEdit = Boolean(node);
    const [saving, setSaving] = useState(false);
    const [testing, setTesting] = useState(false);
    const [form, setForm] = useState({
        name: "",
        host: "",
        port: 22,
        username: "root",
        password: "",
        model_dir: "/data1",
        remark: "",
    });

    useEffect(() => {
        if (open) {
            setForm({
                name: node?.name || "",
                host: node?.host || "",
                port: node?.port ?? 22,
                username: node?.username || "root",
                password: "",
                model_dir: node?.model_dir || "/data1",
                remark: node?.remark || "",
            });
        }
    }, [open, node]);

    const set = (key: string, value: string | number) => setForm((prev) => ({ ...prev, [key]: value }));

    const handleSave = () => {
        if (!form.name || !form.host) {
            message({ title: t("prompt"), variant: "warning", description: [t("model.gpuNodeNameHostRequired")] });
            return;
        }
        if (!isEdit && !form.password) {
            message({ title: t("prompt"), variant: "warning", description: [t("model.gpuNodePasswordRequired")] });
            return;
        }
        setSaving(true);
        const api = isEdit
            ? updateGpuNodeApi(node!.id, { ...form, password: form.password || undefined })
            : createGpuNodeApi(form);
        captureAndAlertRequestErrorHoc(api).then(() => {
            setSaving(false);
            onOpenChange(false);
            onSaved();
        }).catch(() => setSaving(false));
    };

    const handleTest = () => {
        if (!node) return;
        setTesting(true);
        captureAndAlertRequestErrorHoc(testGpuNodeApi(node.id)).then((res) => {
            setTesting(false);
            message({
                title: t("prompt"),
                variant: res.ok ? "success" : "error",
                description: [res.ok ? `${t("model.gpuNodeTestSuccess")} (${res.gpu_count} GPU)` : `${t("model.gpuNodeTestFailed")}: ${res.error || ""}`],
            });
        }).catch(() => setTesting(false));
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="max-w-[480px]">
                <DialogHeader>
                    <DialogTitle>{isEdit ? t("model.gpuNodeEdit") : t("model.gpuNodeAdd")}</DialogTitle>
                </DialogHeader>
                <div className="grid gap-4 py-2">
                    <div className="grid grid-cols-2 gap-3">
                        <div className="grid gap-1">
                            <label className="text-sm">{t("model.gpuNodeName")}</label>
                            <Input value={form.name} onChange={(e) => set("name", e.target.value)} placeholder={t("model.gpuNodeName")} />
                        </div>
                        <div className="grid gap-1">
                            <label className="text-sm">{t("model.gpuNodeHost")}</label>
                            <Input value={form.host} onChange={(e) => set("host", e.target.value)} placeholder="192.168.1.10" />
                        </div>
                        <div className="grid gap-1">
                            <label className="text-sm">{t("model.gpuNodePort")}</label>
                            <Input type="number" value={form.port} onChange={(e) => set("port", Number(e.target.value))} />
                        </div>
                        <div className="grid gap-1">
                            <label className="text-sm">{t("model.gpuNodeUsername")}</label>
                            <Input value={form.username} onChange={(e) => set("username", e.target.value)} />
                        </div>
                        <div className="grid gap-1">
                            <label className="text-sm">{t("model.gpuNodePassword")}</label>
                            <Input
                                type="password"
                                value={form.password}
                                onChange={(e) => set("password", e.target.value)}
                                placeholder={isEdit ? t("model.gpuNodePasswordKeep") : ""}
                            />
                        </div>
                        <div className="grid gap-1">
                            <label className="text-sm">{t("model.gpuNodeModelDir")}</label>
                            <Input value={form.model_dir} onChange={(e) => set("model_dir", e.target.value)} />
                        </div>
                    </div>
                    <div className="grid gap-1">
                        <label className="text-sm">{t("model.gpuNodeRemark")}</label>
                        <Input value={form.remark} onChange={(e) => set("remark", e.target.value)} />
                    </div>
                </div>
                <div className="flex justify-end gap-3">
                    {isEdit && (
                        <Button variant="secondary" disabled={testing} onClick={handleTest}>
                            {testing ? "..." : t("model.gpuNodeTest")}
                        </Button>
                    )}
                    <Button variant="secondary" onClick={() => onOpenChange(false)}>{t("cancel")}</Button>
                    <Button disabled={saving} onClick={handleSave}>{t("save")}</Button>
                </div>
            </DialogContent>
        </Dialog>
    );
}
