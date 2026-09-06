// @ts-strict-ignore
"use client"

import { useQuery } from "@tanstack/react-query"
import { X } from "lucide-react"
import { getAppStatsApi } from "~/api/apps"
import { Button } from "~/components/ui/Button"
import { LoadingIcon } from "~/components/ui/icon/Loading"
import {
    Sheet,
    SheetContent,
    SheetHeader,
    SheetTitle,
} from "~/components/ui/Sheet"
import { useLocalize } from "~/hooks"
import { cn } from "~/utils"

export interface PlazaApp {
    id: number | string
    name?: string
    description?: string
    logo?: string
    tags?: { id: number; name: string }[] | string[]
    flow_type?: number
    user_name?: string
    stats?: AppStats
    can_share?: boolean
}

export interface AppStats {
    recent_7d_visits: number
    total_visits: number
    success_rate: number
    avg_process_time: number
}

interface PreviewDrawerProps {
    agent: PlazaApp | null
    open: boolean
    onOpenChange: (open: boolean) => void
    isFavorited?: boolean
    onToggleFavorite?: (agent: PlazaApp) => void
    onStartChat: (agent: PlazaApp) => void
}

function formatDuration(ms: number): string {
    if (!ms || ms <= 0) return "--"
    if (ms >= 60000) return `${(ms / 60000).toFixed(1)}min`
    if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`
    return `${Math.round(ms)}ms`
}

export function PreviewDrawer({
    agent,
    open,
    onOpenChange,
    isFavorited = false,
    onToggleFavorite,
    onStartChat,
}: PreviewDrawerProps) {
    const localize = useLocalize()

    const {
        data: statsResponse,
        isFetching,
        isError,
        refetch,
    } = useQuery({
        queryKey: ["plaza-app-stats", agent?.id, 7],
        queryFn: () => getAppStatsApi(agent!.id, 7),
        enabled: open && !!agent,
        staleTime: 60_000,
    })

    // The wrapped request resolves with the axios response — business data
    // lives under `.data`.
    const stats = (statsResponse as unknown as { data: AppStats | undefined } | undefined)?.data

    const tagNames = (agent?.tags ?? [])
        .map((t) => (typeof t === "string" ? t : t.name))
        .filter(Boolean)

    return (
        <Sheet open={open} onOpenChange={onOpenChange}>
            <SheetContent
                side="right"
                className="flex h-full min-h-0 w-full flex-col overflow-hidden p-4 sm:max-w-[480px]"
                hideClose
                onCloseAutoFocus={(e) => e.preventDefault()}
            >
                {!agent ? (
                    <div className="flex h-full items-center justify-center">
                        <LoadingIcon className="size-16 text-primary" />
                    </div>
                ) : (
                    <>
                        <SheetHeader className="gap-4 border-b border-border-base pb-5 text-left">
                            <div className="flex items-center justify-between">
                                <SheetTitle className="truncate text-[20px] font-medium leading-7 text-text-1">
                                    {agent.name || "--"}
                                </SheetTitle>
                                <button
                                    type="button"
                                    onClick={() => onOpenChange(false)}
                                    className="inline-flex size-9 shrink-0 items-center justify-center rounded-md text-text-2 hover:bg-fill-1"
                                    aria-label={localize("close")}
                                >
                                    <X className="size-5" />
                                </button>
                            </div>
                            {agent.description && (
                                <p className="whitespace-pre-wrap text-left text-[14px] leading-[22px] text-text-3">
                                    {agent.description}
                                </p>
                            )}
                            {tagNames.length > 0 && (
                                <div className="flex flex-wrap items-center gap-1.5">
                                    {tagNames.map((name) => (
                                        <span
                                            key={name}
                                            className="rounded-md bg-fill-2 px-2 py-0.5 text-caption text-text-3"
                                        >
                                            {name}
                                        </span>
                                    ))}
                                </div>
                            )}
                        </SheetHeader>

                        {/* Stats block */}
                        <div className="shrink-0 py-4">
                            <div className="grid grid-cols-4 gap-2 rounded-lg bg-fill-2 p-3">
                                <div className="flex flex-col items-center gap-0.5 text-center">
                                    <span className="text-[16px] font-medium text-text-1">
                                        {stats?.recent_7d_visits ?? 0}
                                    </span>
                                    <span className="text-caption text-text-4">{localize("com_app_plaza_visits_7d")}</span>
                                </div>
                                <div className="flex flex-col items-center gap-0.5 text-center">
                                    <span className="text-[16px] font-medium text-text-1">
                                        {stats?.total_visits ?? 0}
                                    </span>
                                    <span className="text-caption text-text-4">{localize("com_app_plaza_total_visits")}</span>
                                </div>
                                <div className="flex flex-col items-center gap-0.5 text-center">
                                    <span className="text-[16px] font-medium text-text-1">
                                        {stats ? `${Math.round((stats.success_rate || 0) * 100)}%` : "--"}
                                    </span>
                                    <span className="text-caption text-text-4">{localize("com_app_plaza_success_rate")}</span>
                                </div>
                                <div className="flex flex-col items-center gap-0.5 text-center">
                                    <span className="text-[16px] font-medium text-text-1">
                                        {stats ? formatDuration(stats.avg_process_time) : "--"}
                                    </span>
                                    <span className="text-caption text-text-4">{localize("com_app_plaza_avg_time")}</span>
                                </div>
                            </div>
                            {isError && (
                                <div className="mt-3 flex items-center justify-between rounded-md bg-fill-1 px-3 py-2 text-caption text-text-3">
                                    <span>{localize("com_app_plaza_stats_failed")}</span>
                                    <Button variant="ghost" size="sm" className="h-7 px-2" onClick={() => refetch()}>
                                        {localize("com_app_plaza_retry")}
                                    </Button>
                                </div>
                            )}
                            {isFetching && (
                                <div className="mt-3 flex items-center justify-center gap-2 text-caption text-text-4">
                                    <LoadingIcon className="size-4 text-primary" />
                                </div>
                            )}
                        </div>

                        {/* Actions */}
                        <div className="mt-auto flex shrink-0 items-center gap-2 border-t border-border-base pt-4">
                            {onToggleFavorite && (
                                <Button
                                    variant="outline"
                                    className={cn(
                                        "flex-1",
                                        isFavorited && "border-blue-500 bg-blue-500/[0.07] text-blue-500",
                                    )}
                                    onClick={() => onToggleFavorite(agent)}
                                >
                                    {localize(isFavorited ? "com_app_plaza_unfavorite" : "com_app_plaza_favorite")}
                                </Button>
                            )}
                            <Button className="flex-1" onClick={() => onStartChat(agent)}>
                                {localize("com_app_plaza_preview_start")}
                            </Button>
                        </div>
                    </>
                )}
            </SheetContent>
        </Sheet>
    )
}
