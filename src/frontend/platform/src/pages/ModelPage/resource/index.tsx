// @ts-strict-ignore
import { bsConfirm } from "@/components/bs-ui/alertDialog/useConfirm";
import { Button } from "@/components/bs-ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/bs-ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/bs-ui/tabs";
import {
    deleteGpuNodeApi,
    getGpuNodesApi,
    getGpuStatusApi,
    getNodeContainersApi,
    testGpuNodeApi,
} from "@/controllers/API/gpu";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import type { ContainerInfo, GpuInfo, GpuNodeItem } from "@/types/api/gpu";
import { Plus, RefreshCw, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ContainersTable } from "./components/ContainersTable";
import { GpuCards } from "./components/GpuCards";
import { ModelFiles } from "./components/ModelFiles";
import { NodeDialog } from "./components/NodeDialog";
import { WorkersPanel } from "./components/WorkersPanel";

export default function ResourcePage() {
    const { t } = useTranslation();

    const [nodes, setNodes] = useState<GpuNodeItem[]>([]);
    const [selectedId, setSelectedId] = useState<number | undefined>();
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editingNode, setEditingNode] = useState<GpuNodeItem | null>(null);
    const [gpus, setGpus] = useState<GpuInfo[]>([]);
    const [containers, setContainers] = useState<ContainerInfo[]>([]);
    const [refreshing, setRefreshing] = useState(false);

    const loadNodes = () => {
        captureAndAlertRequestErrorHoc(getGpuNodesApi()).then((res) => {
            setNodes(res || []);
            setSelectedId((prev) => prev ?? (res?.[0]?.id as number | undefined));
        });
    };

    useEffect(() => {
        loadNodes();
    }, []);

    const loadMonitor = useCallback(() => {
        if (!selectedId) return;
        captureAndAlertRequestErrorHoc(getGpuStatusApi(selectedId), { silent: true })
            .then((res) => setGpus(res || []))
            .catch(() => undefined);
        captureAndAlertRequestErrorHoc(getNodeContainersApi(selectedId), { silent: true })
            .then((res) => setContainers(res || []))
            .catch(() => undefined);
    }, [selectedId]);

    useEffect(() => {
        loadMonitor();
        const timer = setInterval(loadMonitor, 10000);
        return () => clearInterval(timer);
    }, [loadMonitor]);

    const handleRefresh = () => {
        setRefreshing(true);
        loadMonitor();
        setTimeout(() => setRefreshing(false), 600);
    };

    const handleDeleteNode = () => {
        const node = nodes.find((item) => item.id === selectedId);
        if (!node) return;
        bsConfirm({
            title: t("prompt"),
            desc: t("model.gpuNodeDeleteConfirm", { name: node.name }),
            okTxt: t("delete"),
            onOk(next) {
                captureAndAlertRequestErrorHoc(deleteGpuNodeApi(node.id)).then(() => {
                    setSelectedId(undefined);
                    loadNodes();
                    next();
                }).catch(() => next());
            },
        });
    };

    const handleTest = () => {
        if (!selectedId) return;
        captureAndAlertRequestErrorHoc(testGpuNodeApi(selectedId));
    };

    const selected = nodes.find((item) => item.id === selectedId);

    return (
        <div className="relative bg-background-login h-full px-2 py-4 overflow-y-auto">
            <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-3">
                    <Select value={selectedId ? String(selectedId) : undefined} onValueChange={(v) => setSelectedId(Number(v))}>
                        <SelectTrigger className="w-[220px]">
                            <SelectValue placeholder={t("model.gpuNodeSelect")} />
                        </SelectTrigger>
                        <SelectContent>
                            {nodes.map((node) => (
                                <SelectItem key={node.id} value={String(node.id)}>{node.name} ({node.host})</SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                    <Button variant="secondary" onClick={() => { setEditingNode(null); setDialogOpen(true); }}>
                        <Plus className="h-4 w-4 mr-1" />{t("model.gpuNodeAdd")}
                    </Button>
                    {selected && (
                        <>
                            <Button variant="secondary" onClick={() => { setEditingNode(selected); setDialogOpen(true); }}>
                                {t("edit")}
                            </Button>
                            <Button variant="secondary" onClick={handleTest}>{t("model.gpuNodeTest")}</Button>
                            <Button variant="ghost" size="icon" onClick={handleDeleteNode}>
                                <Trash2 className="h-4 w-4 text-red-500" />
                            </Button>
                        </>
                    )}
                </div>
                <Button variant="ghost" size="icon" onClick={handleRefresh}>
                    <RefreshCw className={refreshing ? "animate-spin h-4 w-4" : "h-4 w-4"} />
                </Button>
            </div>

            {selected ? (
                <Tabs defaultValue="gpu" className="w-full">
                    <TabsList>
                        <TabsTrigger value="gpu">{t("model.gpuSection")}</TabsTrigger>
                        <TabsTrigger value="workers">{t("model.workers")}</TabsTrigger>
                        <TabsTrigger value="files">{t("model.modelFiles")}</TabsTrigger>
                    </TabsList>
                    <TabsContent value="gpu" className="pt-3">
                        <GpuCards gpus={gpus} />
                    </TabsContent>
                    <TabsContent value="workers" className="pt-3 grid gap-4">
                        <WorkersPanel />
                        <ContainersTable containers={containers} />
                    </TabsContent>
                    <TabsContent value="files" className="pt-3">
                        <ModelFiles nodeId={selected.id} />
                    </TabsContent>
                </Tabs>
            ) : (
                <div className="h-40 flex items-center justify-center text-gray-400 text-sm">{t("model.gpuNodeEmpty")}</div>
            )}

            <NodeDialog open={dialogOpen} node={editingNode} onOpenChange={setDialogOpen} onSaved={loadNodes} />
        </div>
    );
}
