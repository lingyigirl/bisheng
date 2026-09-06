// @ts-strict-ignore
"use client"

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowLeft } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { useToastContext } from "~/Providers"
import {
    addFavoriteApi,
    getFavoritesApi,
    getHomeLabelApi,
    getPlazaAppsApi,
    removeFavoriteApi,
} from "~/api/apps"
import { NotificationSeverity } from "~/common"
import { Button } from "~/components/ui/Button"
import { LoadingIcon } from "~/components/ui/icon/Loading"
import { EmptyStateIllustration } from "~/components/illustrations"
import { useLocalize, useMediaQuery } from "~/hooks"
import { useGetBsConfig } from "~/hooks/queries/data-provider"
import { cn, copyText } from "~/utils"
import { getAppShareUrl } from './appUtils'
import { AgentCard } from './components/AgentCard'
import { AppSearchBar } from './components/AppSearchBar'
import { FavoritesSection } from './components/plaza/FavoritesSection'
import { PlazaFilters, type PlazaFilterState } from './components/plaza/PlazaFilters'
import { PreviewDrawer, type PlazaApp } from './components/plaza/PreviewDrawer'
import { RecentSection } from './components/plaza/RecentSection'

const appFlowOriginKey = (flowId: string) => `app-flow-origin:${flowId}`;
const appLastOriginKey = 'app-last-origin';

const PAGE_SIZE = 20

interface PlazaPage {
    data: { data: PlazaApp[]; total: number }
}

interface FavoritesPayload {
    data: { list: PlazaApp[]; total: number }
}

export default function ExplorePlaza() {
    const localize = useLocalize()
    const navigate = useNavigate()
    const { showToast } = useToastContext()
    const queryClient = useQueryClient()
    const { data: bsConfig } = useGetBsConfig()
    const isAtLeast768 = useMediaQuery('(min-width: 768px)')
    const isAtLeast1024 = useMediaQuery('(min-width: 1024px)')

    const bannerTitle = bsConfig?.applicationCenterWelcomeMessage?.trim() || localize('com_app_plaza_title')
    const bannerDescription = bsConfig?.applicationCenterDescription?.trim() || localize('com_app_center_description')

    const exploreCols = useMemo(() => {
        if (isAtLeast1024) return 3;
        if (isAtLeast768) return 2;
        return 1;
    }, [isAtLeast768, isAtLeast1024]);

    // --- filter state -------------------------------------------------------
    const [searchQuery, setSearchQuery] = useState("")
    const [filters, setFilters] = useState<PlazaFilterState>({ flowType: null, tagIds: [], sortBy: "recent" })
    const isDefaultView = !searchQuery && filters.flowType === null && filters.tagIds.length === 0

    const applyFilterPatch = useCallback((patch: Partial<PlazaFilterState>) => {
        setFilters(prev => ({ ...prev, ...patch }))
    }, [])

    // --- plaza list (react-query infinite) ----------------------------------
    const plazaQuery = useInfiniteQuery({
        queryKey: ["plaza-apps", searchQuery, filters.flowType, filters.tagIds, filters.sortBy],
        queryFn: ({ pageParam = 1 }) =>
            getPlazaAppsApi({
                keyword: searchQuery || undefined,
                flow_types: filters.flowType !== null ? [filters.flowType] : undefined,
                tag_ids: filters.tagIds.length > 0 ? filters.tagIds : undefined,
                sort_by: filters.sortBy,
                page: pageParam,
                limit: PAGE_SIZE,
            }) as Promise<PlazaPage>,
        getNextPageParam: (lastPage: PlazaPage, allPages: PlazaPage[]) => {
            const total = lastPage?.data?.total ?? 0
            const loaded = allPages.reduce((n, p) => n + (p?.data?.data?.length ?? 0), 0)
            return loaded < total ? allPages.length + 1 : undefined
        },
        staleTime: 30_000,
    })

    const agents = useMemo(
        () => plazaQuery.data?.pages.flatMap((p) => p?.data?.data ?? []) ?? [],
        [plazaQuery.data],
    )
    const hasMore = plazaQuery.hasNextPage ?? false
    const loading = plazaQuery.isLoading
    const loadingMore = plazaQuery.isFetchingNextPage

    // --- infinite scroll trigger --------------------------------------------
    const loaderRef = useRef<HTMLDivElement>(null)
    const loadMoreLockRef = useRef(false)

    useEffect(() => {
        if (!loadingMore) {
            loadMoreLockRef.current = false
        }
    }, [loadingMore])

    useEffect(() => {
        const observer = new IntersectionObserver((entries) => {
            const target = entries[0];
            if (
                target.isIntersecting &&
                !loading &&
                !loadingMore &&
                hasMore &&
                !loadMoreLockRef.current
            ) {
                loadMoreLockRef.current = true;
                plazaQuery.fetchNextPage();
            }
        }, { threshold: 0, rootMargin: '400px 0px' });

        if (loaderRef.current) {
            observer.observe(loaderRef.current);
        }

        return () => observer.disconnect();
    }, [loading, loadingMore, hasMore, plazaQuery.fetchNextPage]);

    // --- tags for the filter chips ------------------------------------------
    const { data: tagsResponse } = useQuery({
        queryKey: ["plaza-tags"],
        queryFn: () => getHomeLabelApi(),
        staleTime: 5 * 60_000,
    })
    const tags: { id: number; name: string }[] =
        (tagsResponse as unknown as { data: { id: number; name: string }[] } | undefined)?.data ?? []

    // --- favorites ----------------------------------------------------------
    const { data: favoritesResponse } = useQuery({
        queryKey: ["plaza-favorites", 1],
        queryFn: () => getFavoritesApi(1, 50),
        staleTime: 30_000,
    })
    const favoriteIds = useMemo(
        () => new Set(((favoritesResponse as unknown as FavoritesPayload | undefined)?.data?.list ?? []).map((a) => Number(a.id))),
        [favoritesResponse],
    )

    const favoriteMutation = useMutation({
        mutationFn: ({ agent, favorited }: { agent: PlazaApp; favorited: boolean }) =>
            favorited
                ? removeFavoriteApi(agent.id, agent.flow_type ?? 5)
                : addFavoriteApi(agent.id, agent.flow_type ?? 5),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ["plaza-favorites"] })
        },
        onError: () => {
            showToast?.({
                message: localize('com_ui_error'),
                severity: NotificationSeverity.ERROR,
            })
        },
    })

    const handleToggleFavorite = useCallback(
        (agent: PlazaApp) => {
            favoriteMutation.mutate({
                agent,
                favorited: favoriteIds.has(Number(agent.id)),
            })
        },
        [favoriteMutation, favoriteIds],
    )

    // --- preview drawer -----------------------------------------------------
    const [previewAgent, setPreviewAgent] = useState<PlazaApp | null>(null)

    const handleOpenPreview = useCallback((agent: PlazaApp) => {
        setPreviewAgent(agent)
    }, [])

    const handleCardClick = (agent: PlazaApp) => {
        handleOpenPreview(agent)
    }

    const handleStartChat = (agent: PlazaApp) => {
        const flowId = agent.id
        const flowType = agent.flow_type
        try {
            sessionStorage.setItem(appFlowOriginKey(String(flowId)), 'explore');
            sessionStorage.setItem(appLastOriginKey, 'explore');
        } catch {
            // ignore storage failures
        }
        // Enter without chatId — AppChatEntry resolves the most recent conversation,
        // or creates a new one. The use_app pre-check happens in the appChat init flow.
        navigate(`/app/${flowId}/${flowType}?from=explore&returnTo=%2Fapps%2Fexplore`, {
            state: { appSurfaceReturn: '/apps/explore' as const },
        });
    }

    const handleShare = async (agent: PlazaApp) => {
        if (agent.can_share !== true) return;
        const shareUrl = getAppShareUrl(agent.id, agent.flow_type ?? 10);
        try {
            await copyText(shareUrl);
            showToast?.({
                message: localize('com_app_share_link_copied'),
                severity: NotificationSeverity.SUCCESS,
            });
        } catch {
            showToast?.({
                message: localize('com_app_share_link_copy_failed'),
                severity: NotificationSeverity.ERROR,
            });
        }
    }

    return (
        <div
            className={cn(
                'flex h-full min-h-0 w-full flex-1 flex-col items-center overflow-hidden bg-white',
                'max-[767px]:h-[100dvh]',
            )}
        >
            {/* Banner */}
            <div className="relative w-full shrink-0 overflow-hidden border-b border-[#F0F1F5] bg-blue-500/[0.05]">
                <div
                    aria-hidden
                    className="pointer-events-none absolute inset-0 bg-blue-200"
                    style={{
                        WebkitMaskImage: `url(${__APP_ENV__.BASE_URL || ''}/assets/channel/apptab-icons.svg)`,
                        maskImage: `url(${__APP_ENV__.BASE_URL || ''}/assets/channel/apptab-icons.svg)`,
                        WebkitMaskSize: "cover",
                        maskSize: "cover",
                        WebkitMaskPosition: "center",
                        maskPosition: "center",
                        WebkitMaskRepeat: "no-repeat",
                        maskRepeat: "no-repeat",
                    }}
                />
                <div className="absolute left-4 top-4 z-10">
                    <Button
                        variant="ghost"
                        onClick={() => navigate('/apps')}
                        className="h-8 w-8 rounded-md border border-border-base bg-white p-0 text-text-2 fine-pointer:hover:bg-fill-1 fine-pointer:hover:text-blue-500"
                    >
                        <ArrowLeft className="size-3.5" />
                    </Button>
                </div>
                <div className="relative mx-auto flex w-full max-w-[1000px] flex-col items-center justify-center px-5 pb-5 pt-7 text-center">
                    <h1 className="mb-1 font-['PingFang_SC'] text-[26px] font-semibold text-blue-500">
                        {bannerTitle}
                    </h1>
                    <p className="mb-3 max-w-[640px] font-['PingFang_SC'] text-[13px] leading-[22px] text-text-3">
                        {bannerDescription}
                    </p>
                </div>
            </div>

            {/* Search + filters */}
            <div className="z-10 w-full max-w-[1000px] shrink-0 px-5 py-4">
                <div className="mb-3">
                    <AppSearchBar query={searchQuery} onSearch={setSearchQuery} />
                </div>
                <PlazaFilters value={filters} tags={tags} onChange={applyFilterPatch} />
            </div>

            {/* Grid + sections */}
            <main className="flex min-h-0 w-full flex-1 flex-col items-center overflow-x-hidden overflow-y-auto scrollbar-os">
                <div className="flex w-full max-w-[1000px] flex-1 flex-col gap-6 px-5 pb-5">
                    {isDefaultView && (
                        <>
                            <FavoritesSection onOpenPreview={handleOpenPreview} />
                            <RecentSection onOpenPreview={handleOpenPreview} />
                        </>
                    )}

                    <div
                        className="grid w-full items-start gap-4"
                        style={{ gridTemplateColumns: `repeat(${exploreCols}, minmax(0, 1fr))` }}
                    >
                        {agents.map((agent, idx) => (
                            <div key={`${agent.id}-${idx}`} className="relative">
                                {typeof agent?.stats?.recent_7d_visits === 'number' && (
                                    <span className="pointer-events-none absolute right-2 top-2 z-10 rounded-md bg-blue-500/[0.07] px-1.5 py-0.5 text-caption text-blue-500">
                                        {localize('com_app_plaza_visits_7d')} {agent.stats.recent_7d_visits}
                                    </span>
                                )}
                                <AgentCard
                                    agent={agent}
                                    onStartChat={handleCardClick}
                                    onShare={handleShare}
                                />
                            </div>
                        ))}
                    </div>

                    {/* Load trigger & states */}
                    <div
                        ref={loaderRef}
                        className={cn(
                            'flex w-full flex-col items-center',
                            (loading || agents.length === 0) ? 'flex-1' : 'py-10',
                        )}
                    >
                        {(loading || agents.length === 0) && <div className="flex-[8] md:flex-[9]" aria-hidden />}
                        {loading ? (
                            <div className="flex flex-col items-center gap-3 text-blue-500">
                                <LoadingIcon className="size-20 text-primary" />
                                <span className="text-sm font-['PingFang_SC'] text-text-3">{localize('com_app_explore_loading_more')}</span>
                            </div>
                        ) : loadingMore ? (
                            <div className="flex items-center gap-2 text-blue-500">
                                <LoadingIcon className="size-6 text-primary" />
                                <span className="text-sm font-['PingFang_SC'] text-text-3">{localize('com_app_explore_loading_more')}</span>
                            </div>
                        ) : null}
                        {!hasMore && agents.length > 0 && (
                            <p className="mt-4 text-[12px] font-['PingFang_SC'] text-[#a9aeb8]">{localize('com_app_explore_end_of_list')}</p>
                        )}
                        {!loading && agents.length === 0 && (
                            <div className="flex flex-col items-center">
                                <EmptyStateIllustration className="mb-4 size-[120px]" />
                                <p className="text-[14px] font-['PingFang_SC'] text-[#a9aeb8]">{localize('com_app_explore_no_agents')}</p>
                            </div>
                        )}
                        {(loading || agents.length === 0) && <div className="flex-[12] md:flex-[11]" aria-hidden />}
                    </div>
                </div>
            </main>

            {/* Preview drawer */}
            <PreviewDrawer
                agent={previewAgent}
                open={!!previewAgent}
                onOpenChange={(open) => { if (!open) setPreviewAgent(null) }}
                isFavorited={previewAgent ? favoriteIds.has(Number(previewAgent.id)) : false}
                onToggleFavorite={handleToggleFavorite}
                onStartChat={handleStartChat}
            />
        </div>
    )
}
