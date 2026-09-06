// @ts-strict-ignore
"use client"

import { useQuery } from "@tanstack/react-query"
import { getFrequently } from "~/api/apps"
import AppAvator from "~/components/Avator"
import { LoadingIcon } from "~/components/ui/icon/Loading"
import { useLocalize } from "~/hooks"
import type { PlazaApp } from "./PreviewDrawer"

const RECENT_LIMIT = 10

interface RecentSectionProps {
    onOpenPreview: (agent: PlazaApp) => void
}

export function RecentSection({ onOpenPreview }: RecentSectionProps) {
    const localize = useLocalize()
    const { data, isLoading } = useQuery({
        queryKey: ["plaza-recent"],
        queryFn: () => getFrequently(1, RECENT_LIMIT),
        staleTime: 30_000,
    })

    const payload = data as unknown as
        | { data: { list: { id: number; name: string; logo: string; last_used_time?: string }[]; total: number } }
        | undefined
    const rawList = payload?.data?.list ?? []
    // /app/used is pinned-first — the plaza section ignores pin ordering and
    // shows pure recency (design 坑 #3).
    const list = [...rawList]
        .sort((a, b) => {
            const at = a.last_used_time ? new Date(a.last_used_time).getTime() : 0
            const bt = b.last_used_time ? new Date(b.last_used_time).getTime() : 0
            return bt - at
        })
        .slice(0, RECENT_LIMIT)

    if (!isLoading && list.length === 0) return null

    return (
        <section className="w-full">
            <h2 className="mb-2 text-body font-medium text-text-1">
                {localize("com_app_plaza_recent_title")}
            </h2>
            {isLoading ? (
                <div className="flex h-14 items-center justify-center">
                    <LoadingIcon className="size-5 text-primary" />
                </div>
            ) : list.length === 0 ? (
                <p className="py-3 text-center text-caption text-text-4">
                    {localize("com_app_plaza_recent_empty")}
                </p>
            ) : (
                <div className="flex gap-3 overflow-x-auto pb-1 scrollbar-os">
                    {list.map((agent) => (
                        <button
                            key={agent.id}
                            type="button"
                            onClick={() => onOpenPreview(agent)}
                            className="flex w-44 shrink-0 flex-col items-center gap-2 rounded-lg border border-border-base bg-fill-2 p-3 hover:border-blue-500/40"
                        >
                            <AppAvator name={agent.name || "?"} size="md" url={agent.logo} />
                            <span className="line-clamp-2 w-full text-center text-caption text-text-2">
                                {agent.name}
                            </span>
                        </button>
                    ))}
                </div>
            )}
        </section>
    )
}
