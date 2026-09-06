// @ts-strict-ignore
"use client"

import { useQuery } from "@tanstack/react-query"
import { getFavoritesApi } from "~/api/apps"
import AppAvator from "~/components/Avator"
import { LoadingIcon } from "~/components/ui/icon/Loading"
import { useLocalize } from "~/hooks"
import type { PlazaApp } from "./PreviewDrawer"

interface FavoritesSectionProps {
    onOpenPreview: (agent: PlazaApp) => void
}

export function FavoritesSection({ onOpenPreview }: FavoritesSectionProps) {
    const localize = useLocalize()
    const { data, isLoading } = useQuery({
        queryKey: ["plaza-favorites", 1],
        queryFn: () => getFavoritesApi(1, 50),
        staleTime: 30_000,
    })

    const payload = data as unknown as { data: { list: PlazaApp[]; total: number } } | undefined
    const list: PlazaApp[] = payload?.data?.list ?? []
    if (!isLoading && list.length === 0) return null

    return (
        <section className="w-full">
            <h2 className="mb-2 text-body font-medium text-text-1">
                {localize("com_app_plaza_favorites_title")}
            </h2>
            {isLoading ? (
                <div className="flex h-14 items-center justify-center">
                    <LoadingIcon className="size-5 text-primary" />
                </div>
            ) : list.length === 0 ? (
                <p className="py-3 text-center text-caption text-text-4">
                    {localize("com_app_plaza_favorites_empty")}
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
