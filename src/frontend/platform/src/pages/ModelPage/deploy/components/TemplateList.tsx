// @ts-strict-ignore
import { bsConfirm } from "@/components/bs-ui/alertDialog/useConfirm";
import { Badge } from "@/components/bs-ui/badge";
import { Button } from "@/components/bs-ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/bs-ui/dialog";
import { Input } from "@/components/bs-ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/bs-ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/bs-ui/table";
import { useToast } from "@/components/bs-ui/toast/use-toast";
import { createTemplateApi, deleteTemplateApi, updateTemplateApi } from "@/controllers/API/gpu";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import type { DeployTemplate, GpuNodeItem } from "@/types/api/gpu";
import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

interface TemplateListProps {
    templates: DeployTemplate[];
    nodes: GpuNodeItem[];
    onChanged: () => void;
}

interface TemplateForm {
    name: string;
    image: string;
    model_path: string;
    gpu_index: string;
    host_port: number;
    container_port: number;
    served_model_name: string;
    start_args: string;
    remark: string;
}

const emptyForm = (): TemplateForm => ({
    name: "",
    image: "vllm/vllm-openai:latest",
    model_path: "",
    gpu_index: "0",
    host_port: 8001,
    container_port: 8000,
    served_model_name: "",
    start_args: '{"--gpu-memory-utilization": 0.9}',
    remark: "",
});

export function TemplateList({ templates, nodes, onChanged }: TemplateListProps) {
    const { t } = useTranslation();
    const { message } = useToast();
    const [open, setOpen] = useState(false);
    const [editing, setEditing] = useState<DeployTemplate | null>(null);
    const [form, setForm] = useState<TemplateForm>(emptyForm());
    const [instanceName, setInstanceName] = useState("");
    const [deployNodeId, setDeployNodeId] = useState<string>("");
    const [deployFor, setDeployFor] = useState<DeployTemplate | null>(null);

    const set = (key: keyof TemplateForm, value: string | number) => setForm((prev) => ({ ...prev, [key]: value }));

    const openCreate = () => {
        setEditing(null);
        setForm(emptyForm());
        setOpen(true);
    };

    const openEdit = (template: DeployTemplate) => {
        setEditing(template);
        setForm({
            name: template.name,
            image: template.image,
            model_path: template.model_path,
            gpu_index: template.gpu_index,
            host_port: template.host_port,
            container_port: template.container_port,
            served_model_name: template.served_model_name,
            start_args: template.start_args || "",
            remark: template.remark || "",
        });
        setOpen(true);
    };

    const handleSave = () => {
        const payload = { ...form, start_args: form.start_args || undefined };
        const api = editing ? updateTemplateApi(editing.id, payload) : createTemplateApi(payload);
        captureAndAlertRequestErrorHoc(api).then(() => {
            setOpen(false);
            onChanged();
        });
    };

    const handleDelete = (template: DeployTemplate) => {
        bsConfirm({
            title: t("prompt"),
            desc: t("model.deployTemplateDeleteConfirm", { name: template.name }),
            okTxt: t("delete"),
            onOk(next) {
                captureAndAlertRequestErrorHoc(deleteTemplateApi(template.id)).then(() => {
                    onChanged();
                    next();
                }).catch(() => next());
            },
        });
    };

    const handleDeploy = () => {
        if (!deployFor || !deployNodeId || !instanceName) {
            message({ title: t("prompt"), variant: "warning", description: [t("model.deployFillRequired")] });
            return;
        }
        captureAndAlertRequestErrorHoc(
            createInstanceApi({ node_id: Number(deployNodeId), template_id: deployFor.id, name: instanceName })
        ).then(() => {
            message({ title: t("prompt"), variant: "success", description: [t("model.deployCreated")] });
            setDeployFor(null);
            setInstanceName("");
            onChanged();
        });
    };

    return (
        <div className="rounded-lg border bg-background">
            <div className="flex items-center justify-between px-3 py-2 border-b">
                <span className="text-sm">{t("model.deployTemplate")}</span>
                <Button variant="secondary" size="sm" onClick={openCreate}>
                    <Plus className="h-4 w-4 mr-1" />{t("model.deployTemplateAdd")}
                </Button>
            </div>
            <Table>
                <TableHeader>
                    <TableRow>
                        <TableHead>{t("model.deployName")}</TableHead>
                        <TableHead>{t("model.deployImage")}</TableHead>
                        <TableHead>{t("model.deployModelPath")}</TableHead>
                        <TableHead>{t("model.deployGpuIndex")}</TableHead>
                        <TableHead>{t("model.deployPort")}</TableHead>
                        <TableHead>{t("model.deployServedModelName")}</TableHead>
                        <TableHead className="w-[200px]"></TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {templates.map((template) => (
                        <TableRow key={template.id}>
                            <TableCell className="font-medium">
                                {template.name}
                                {template.is_builtin && <Badge variant="outline" className="ml-2">{t("model.deployBuiltin")}</Badge>}
                            </TableCell>
                            <TableCell className="text-gray-500">{template.image}</TableCell>
                            <TableCell className="text-gray-500">{template.model_path}</TableCell>
                            <TableCell>{template.gpu_index}</TableCell>
                            <TableCell>{template.host_port}:{template.container_port}</TableCell>
                            <TableCell>{template.served_model_name}</TableCell>
                            <TableCell className="text-right">
                                <Button variant="secondary" size="sm" onClick={() => setDeployFor(template)}>{t("model.deployAction")}</Button>
                                <Button variant="ghost" size="sm" onClick={() => openEdit(template)}>{t("edit")}</Button>
                                <Button variant="ghost" size="sm" onClick={() => handleDelete(template)}>
                                    <Trash2 className="h-4 w-4 text-red-500" />
                                </Button>
                            </TableCell>
                        </TableRow>
                    ))}
                </TableBody>
            </Table>

            <Dialog open={open} onOpenChange={setOpen}>
                <DialogContent className="max-w-[520px]">
                    <DialogHeader>
                        <DialogTitle>{editing ? t("model.deployTemplateEdit") : t("model.deployTemplateAdd")}</DialogTitle>
                    </DialogHeader>
                    <div className="grid gap-3 py-2">
                        <div className="grid grid-cols-2 gap-3">
                            <div className="grid gap-1">
                                <label className="text-sm">{t("model.deployName")}</label>
                                <Input value={form.name} onChange={(e) => set("name", e.target.value)} />
                            </div>
                            <div className="grid gap-1">
                                <label className="text-sm">{t("model.deployImage")}</label>
                                <Input value={form.image} onChange={(e) => set("image", e.target.value)} />
                            </div>
                            <div className="grid gap-1 col-span-2">
                                <label className="text-sm">{t("model.deployModelPath")}</label>
                                <Input value={form.model_path} onChange={(e) => set("model_path", e.target.value)} placeholder="/data1/models/..." />
                            </div>
                            <div className="grid gap-1">
                                <label className="text-sm">{t("model.deployGpuIndex")}</label>
                                <Input value={form.gpu_index} onChange={(e) => set("gpu_index", e.target.value)} />
                            </div>
                            <div className="grid gap-1">
                                <label className="text-sm">{t("model.deployPort")}</label>
                                <Input type="number" value={form.host_port} onChange={(e) => set("host_port", Number(e.target.value))} />
                            </div>
                            <div className="grid gap-1 col-span-2">
                                <label className="text-sm">{t("model.deployServedModelName")}</label>
                                <Input value={form.served_model_name} onChange={(e) => set("served_model_name", e.target.value)} />
                            </div>
                            <div className="grid gap-1 col-span-2">
                                <label className="text-sm">{t("model.deployStartArgs")}</label>
                                <Input value={form.start_args} onChange={(e) => set("start_args", e.target.value)} />
                            </div>
                        </div>
                    </div>
                    <div className="flex justify-end gap-3">
                        <Button variant="secondary" onClick={() => setOpen(false)}>{t("cancel")}</Button>
                        <Button onClick={handleSave}>{t("save")}</Button>
                    </div>
                </DialogContent>
            </Dialog>

            <Dialog open={Boolean(deployFor)} onOpenChange={(v) => !v && setDeployFor(null)}>
                <DialogContent className="max-w-[440px]">
                    <DialogHeader>
                        <DialogTitle>{t("model.deployCreateInstance")} — {deployFor?.name}</DialogTitle>
                    </DialogHeader>
                    <div className="grid gap-3 py-2">
                        <div className="grid gap-1">
                            <label className="text-sm">{t("model.deployInstanceNode")}</label>
                            <Select value={deployNodeId} onValueChange={setDeployNodeId}>
                                <SelectTrigger><SelectValue placeholder={t("model.gpuNodeSelect")} /></SelectTrigger>
                                <SelectContent>
                                    {nodes.map((node) => (
                                        <SelectItem key={node.id} value={String(node.id)}>{node.name} ({node.host})</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="grid gap-1">
                            <label className="text-sm">{t("model.deployInstanceName")}</label>
                            <Input value={instanceName} onChange={(e) => setInstanceName(e.target.value)} placeholder="bisheng-xxx" />
                        </div>
                    </div>
                    <div className="flex justify-end gap-3">
                        <Button variant="secondary" onClick={() => setDeployFor(null)}>{t("cancel")}</Button>
                        <Button onClick={handleDeploy}>{t("model.deployAction")}</Button>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    );
}
