// @ts-strict-ignore
import { Badge } from "@/components/bs-ui/badge";
import { Progress } from "@/components/bs-ui/progress";
import type { GpuInfo } from "@/types/api/gpu";
import { useTranslation } from "react-i18next";

interface GpuCardsProps {
    gpus: GpuInfo[];
}

const mb = (value: number) => (value / 1024).toFixed(1) + " GB";

export function GpuCards({ gpus }: GpuCardsProps) {
    const { t } = useTranslation();
    if (!gpus.length) return <div className="text-sm text-gray-400 p-4">{t("model.gpuEmpty")}</div>;
    return (
        <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
            {gpus.map((gpu) => {
                const percent = gpu.memory_total_mb ? Math.round((gpu.memory_used_mb / gpu.memory_total_mb) * 100) : 0;
                return (
                    <div key={gpu.index} className="rounded-lg border p-3 bg-background">
                        <div className="flex items-center justify-between">
                            <span className="text-sm font-semibold">GPU {gpu.index}</span>
                            <Badge variant={gpu.util_percent > 80 ? "destructive" : "secondary"}>{gpu.util_percent}%</Badge>
                        </div>
                        <div className="text-xs text-gray-500 truncate mt-1" title={gpu.name}>{gpu.name}</div>
                        <div className="mt-2">
                            <div className="flex justify-between text-xs text-gray-500">
                                <span>{t("model.gpuMemory")}</span>
                                <span>{mb(gpu.memory_used_mb)} / {mb(gpu.memory_total_mb)}</span>
                            </div>
                            <Progress value={percent} className="h-1.5 mt-1" />
                        </div>
                        <div className="flex justify-between text-xs text-gray-500 mt-2">
                            <span>{t("model.gpuUtilization")}: {gpu.util_percent}%</span>
                            <span>{t("model.gpuTemperature")}: {gpu.temperature_c}°C</span>
                        </div>
                    </div>
                );
            })}
        </div>
    );
}
