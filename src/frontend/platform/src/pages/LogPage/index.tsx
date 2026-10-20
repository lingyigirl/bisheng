import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/bs-ui/tabs";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import SystemLog from "./systemLog";
import AppUseLog from "./useAppLog";
import { AgentMonitor } from "./agentMonitor";

export default function LogPage() {
    const { t } = useTranslation()
    const [searchParams, setSearchParams] = useSearchParams()
    // AC-52: task list links here with `?tab=monitor&trace_id=...`; keep the
    // tab in the URL so a refresh / deep link lands on the same view.
    const tabParam = searchParams.get("tab") || "app"
    const [tab, setTab] = useState(tabParam)

    useEffect(() => setTab(tabParam), [tabParam])

    const handleTabChange = (next: string) => {
        setTab(next)
        const params = new URLSearchParams(searchParams)
        params.set("tab", next)
        setSearchParams(params, { replace: true })
    }

    return <div id="model-scroll" className="w-full h-full px-2 pt-4">
        <Tabs value={tab} className="w-full mb-[40px]" onValueChange={handleTabChange}>
            <TabsList className="">
                <TabsTrigger value="app">{t('log.appUsage')}</TabsTrigger>
                <TabsTrigger value="system">{t('log.systemOperations')}</TabsTrigger>
                <TabsTrigger value="monitor">{t('log.agentMonitor')}</TabsTrigger>
            </TabsList>
            <TabsContent value="app">
                <AppUseLog />
            </TabsContent>
            <TabsContent value="system">
                <SystemLog />
            </TabsContent>
            <TabsContent value="monitor">
                <AgentMonitor />
            </TabsContent>
        </Tabs>
    </div>
};
