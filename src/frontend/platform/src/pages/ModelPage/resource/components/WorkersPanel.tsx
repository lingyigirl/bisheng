// @ts-strict-ignore
import { Badge } from "@/components/bs-ui/badge";
import { getWorkersApi } from "@/controllers/API/gpu";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import type { WorkerStatus } from "@/types/api/gpu";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

const fmtAge = (seconds: number) => {
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    return `${Math.round(seconds / 3600)}h`;
};

export function WorkersPanel() {
    const { t } = useTranslation();
    const [workers, setWorkers] = useState<WorkerStatus[]>([]);

    useEffect(() => {
        let mounted = true;
        const load = () => {
            captureAndAlertRequestErrorHoc(getWorkersApi(), { silent: true })
                .then((res) => mounted && setWorkers(res || []))
                .catch(() => undefined);
        };
        load();
        const timer = setInterval(load, 10000);
        return () => {
            mounted = false;
            clearInterval(timer);
        };
    }, []);

    return (
        <div className="rounded-lg border bg-background">
            <div className="px-3 py-2 border-b text-sm font-semibold">{t("model.workers")}</div>
            <div className="p-3 grid gap-2">
                {!workers.length && <div className="text-sm text-gray-400">{t("model.workerEmpty")}</div>}
                {workers.map((worker) => (
                    <div key={worker.queue} className="flex items-center justify-between text-sm">
                        <span className="font-medium">{worker.queue}</span>
                        <div className="flex items-center gap-2">
                            <span className="text-gray-400 text-xs">{t("model.workerLastBeat")}: {fmtAge(worker.age_seconds)}</span>
                            <Badge variant={worker.alive ? "secondary" : "destructive"}>
                                {worker.alive ? t("model.workerAlive") : t("model.workerDead")}
                            </Badge>
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}
