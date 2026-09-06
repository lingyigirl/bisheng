// @ts-strict-ignore
import { Button } from "@/components/bs-ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/bs-ui/sheet";
import { getInstanceLogsApi } from "@/controllers/API/gpu";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import type { DeployInstance } from "@/types/api/gpu";
import { RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

interface LogsDrawerProps {
    instance: DeployInstance;
    onOpenChange: (open: boolean) => void;
}

export function LogsDrawer({ instance, onOpenChange }: LogsDrawerProps) {
    const { t } = useTranslation();
    const [logs, setLogs] = useState("");
    const [loading, setLoading] = useState(false);

    const load = () => {
        setLoading(true);
        captureAndAlertRequestErrorHoc(getInstanceLogsApi(instance.id, 500))
            .then((res) => setLogs(res || ""))
            .finally(() => setLoading(false));
    };

    useEffect(() => {
        load();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [instance.id]);

    return (
        <Sheet open onOpenChange={onOpenChange}>
            <SheetContent side="right" className="w-[640px] sm:max-w-[640px] flex flex-col">
                <SheetHeader>
                    <div className="flex items-center justify-between">
                        <SheetTitle>{t("model.deployLogs")} — {instance.name}</SheetTitle>
                        <Button variant="ghost" size="icon" onClick={load} disabled={loading}>
                            <RefreshCw className={loading ? "animate-spin h-4 w-4" : "h-4 w-4"} />
                        </Button>
                    </div>
                </SheetHeader>
                <pre className="flex-1 overflow-auto bg-black/90 text-green-400 text-xs p-3 rounded-md whitespace-pre-wrap">
                    {logs || t("model.deployLogsEmpty")}
                </pre>
            </SheetContent>
        </Sheet>
    );
}
