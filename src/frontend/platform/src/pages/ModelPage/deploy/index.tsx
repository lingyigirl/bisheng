// @ts-strict-ignore
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import { getGpuNodesApi, getInstancesApi, getTemplatesApi } from "@/controllers/API/gpu";
import type { DeployInstance, DeployTemplate, GpuNodeItem } from "@/types/api/gpu";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { InstanceTable } from "./components/InstanceTable";
import { TemplateList } from "./components/TemplateList";

export default function DeployPage() {
    const { t } = useTranslation();

    const [nodes, setNodes] = useState<GpuNodeItem[]>([]);
    const [templates, setTemplates] = useState<DeployTemplate[]>([]);
    const [instances, setInstances] = useState<DeployInstance[]>([]);

    const loadAll = () => {
        captureAndAlertRequestErrorHoc(getGpuNodesApi()).then((res) => setNodes(res || []));
        captureAndAlertRequestErrorHoc(getTemplatesApi()).then((res) => setTemplates(res || []));
        captureAndAlertRequestErrorHoc(getInstancesApi()).then((res) => setInstances(res || []));
    };

    useEffect(() => {
        loadAll();
    }, []);

    return (
        <div className="relative bg-background-login h-full px-2 py-4 overflow-y-auto">
            <h3 className="text-sm font-semibold mb-2">{t("model.deployTemplate")}</h3>
            <TemplateList templates={templates} nodes={nodes} onChanged={loadAll} />

            <h3 className="text-sm font-semibold mb-2 mt-6">{t("model.deployInstances")}</h3>
            <InstanceTable instances={instances} templates={templates} nodes={nodes} onChanged={loadAll} />
        </div>
    );
}
