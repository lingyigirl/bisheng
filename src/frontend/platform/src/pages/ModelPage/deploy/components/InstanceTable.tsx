// @ts-strict-ignore
import { bsConfirm } from "@/components/bs-ui/alertDialog/useConfirm";
import { Badge } from "@/components/bs-ui/badge";
import { Button } from "@/components/bs-ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/bs-ui/table";
import { useToast } from "@/components/bs-ui/toast/use-toast";
import {
    deleteInstanceApi,
    restartInstanceApi,
    startInstanceApi,
    stopInstanceApi,
} from "@/controllers/API/gpu";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import type { DeployInstance, DeployTemplate, GpuNodeItem } from "@/types/api/gpu";
import { Play, Square, Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { LogsDrawer } from "./LogsDrawer";

interface InstanceTableProps {
    instances: DeployInstance[];
    templates: DeployTemplate[];
    nodes: GpuNodeItem[];
    onChanged: () => void;
}

export function InstanceTable({ instances, templates, nodes, onChanged }: InstanceTableProps) {
    const { t } = useTranslation();
    const { message } = useToast();
    const [logsFor, setLogsFor] = useState<DeployInstance | null>(null);
    const [busyId, setBusyId] = useState<number | null>(null);

    const nodeName = (id: number) => nodes.find((n) => n.id === id)?.name || String(id);
    const templateName = (id: number) => templates.find((tp) => tp.id === id)?.name || String(id);

    const statusBadge = (status: string) => {
        const map: Record<string, "secondary" | "destructive" | "outline"> = {
            running: "secondary",
            stopped: "outline",
            error: "destructive",
            missing: "destructive",
            unknown: "outline",
        };
        return <Badge variant={map[status] || "outline"}>{t(`model.status${status[0]?.toUpperCase() || ""}${status.slice(1)}`)}</Badge>;
    };

    const run = (id: number, api: (id: number) => Promise<string>, okText: string) => {
        setBusyId(id);
        captureAndAlertRequestErrorHoc(api(id))
            .then(() => {
                message({ title: t("prompt"), variant: "success", description: [okText] });
                onChanged();
            })
            .finally(() => setBusyId(null));
    };

    const handleDelete = (instance: DeployInstance) => {
        bsConfirm({
            title: t("prompt"),
            desc: t("model.deployInstanceDeleteConfirm", { name: instance.name }),
            okTxt: t("delete"),
            onOk(next) {
                captureAndAlertRequestErrorHoc(deleteInstanceApi(instance.id)).then(() => {
                    onChanged();
                    next();
                }).catch(() => next());
            },
        });
    };

    return (
        <div className="rounded-lg border bg-background">
            <Table>
                <TableHeader>
                    <TableRow>
                        <TableHead>{t("model.deployInstanceName")}</TableHead>
                        <TableHead>{t("model.deployInstanceNode")}</TableHead>
                        <TableHead>{t("model.deployTemplate")}</TableHead>
                        <TableHead>{t("model.deployInstanceStatus")}</TableHead>
                        <TableHead className="w-[260px]"></TableHead>
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {!instances.length && (
                        <TableRow>
                            <TableCell colSpan={5} className="text-center text-gray-400">{t("model.deployInstancesEmpty")}</TableCell>
                        </TableRow>
                    )}
                    {instances.map((instance) => (
                        <TableRow key={instance.id}>
                            <TableCell className="font-medium">{instance.name}</TableCell>
                            <TableCell>{nodeName(instance.node_id)}</TableCell>
                            <TableCell className="text-gray-500">{templateName(instance.template_id)}</TableCell>
                            <TableCell>{statusBadge(instance.status)}</TableCell>
                            <TableCell className="text-right">
                                <Button
                                    variant="ghost"
                                    size="sm"
                                    disabled={busyId === instance.id || instance.status === "running"}
                                    onClick={() => run(instance.id, startInstanceApi, t("model.deployStarted"))}
                                >
                                    <Play className="h-4 w-4" />
                                </Button>
                                <Button
                                    variant="ghost"
                                    size="sm"
                                    disabled={busyId === instance.id || instance.status !== "running"}
                                    onClick={() => run(instance.id, stopInstanceApi, t("model.deployStopped"))}
                                >
                                    <Square className="h-4 w-4" />
                                </Button>
                                <Button
                                    variant="ghost"
                                    size="sm"
                                    disabled={busyId === instance.id || instance.status !== "running"}
                                    onClick={() => run(instance.id, restartInstanceApi, t("model.deployRestarted"))}
                                >
                                    {t("model.deployRestart")}
                                </Button>
                                <Button variant="ghost" size="sm" onClick={() => setLogsFor(instance)}>{t("model.deployLogs")}</Button>
                                <Button variant="ghost" size="sm" onClick={() => handleDelete(instance)}>
                                    <Trash2 className="h-4 w-4 text-red-500" />
                                </Button>
                            </TableCell>
                        </TableRow>
                    ))}
                </TableBody>
            </Table>
            {logsFor && <LogsDrawer instance={logsFor} onOpenChange={(v) => !v && setLogsFor(null)} />}
        </div>
    );
}
