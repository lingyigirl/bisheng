import { Badge } from "@/components/bs-ui/badge"
import { Button } from "@/components/bs-ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/bs-ui/tabs"
import { getServiceAccountApi } from "@/controllers/API/serviceAccount"
import { captureAndAlertRequestErrorHoc } from "@/controllers/request"
import { ServiceAccount } from "@/types/api/serviceAccount"
import { ArrowLeft } from "lucide-react"
import { useCallback, useEffect, useState } from "react"
import { useTranslation } from "react-i18next"
import { ApiKeysTab } from "./ApiKeysTab"
import { OverviewTab } from "./OverviewTab"
import { ResourceGrantsTab } from "./ResourceGrantsTab"

interface DetailViewProps {
    accountId: number
    /** Landing tab; the post-create flow jumps straight to the keys tab. */
    initialTab?: string
    onBack: () => void
}

/** One service account: overview + keys + the resources its identity may reach. */
export function DetailView({ accountId, initialTab = "overview", onBack }: DetailViewProps) {
    const { t } = useTranslation("serviceAccount")
    const [account, setAccount] = useState<ServiceAccount | null>(null)
    const [loading, setLoading] = useState(true)
    const [tab, setTab] = useState(initialTab)

    /**
     * Single source of truth for this view. PATCH / enable / disable all answer
     * with stale stats (key_count 0, last_used_at null), so every write routes
     * back through this re-read rather than through the write response.
     */
    const refresh = useCallback(async () => {
        const res = await captureAndAlertRequestErrorHoc(getServiceAccountApi(accountId))
        if (res) setAccount(res)
        setLoading(false)
    }, [accountId])

    useEffect(() => {
        void refresh()
    }, [refresh])

    return (
        <div className="flex h-full w-full flex-col gap-4 overflow-hidden p-4">
            <div className="flex shrink-0 items-center gap-3">
                <Button variant="ghost" size="sm" className="px-2" onClick={onBack}>
                    <ArrowLeft className="h-4 w-4" />
                </Button>
                <span className="text-base font-medium">{account?.name ?? ""}</span>
                {account && (
                    <Badge variant={account.enabled ? "success" : "gray"}>
                        {account.enabled ? t("status.enabled") : t("status.disabled")}
                    </Badge>
                )}
            </div>

            {!account ? (
                <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
                    {loading ? t("loading") : t("detail.gone")}
                </div>
            ) : (
                <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 w-full flex-1 flex-col">
                    <TabsList className="shrink-0 self-start">
                        <TabsTrigger value="overview">{t("detail.tab.overview")}</TabsTrigger>
                        <TabsTrigger value="keys">{t("detail.tab.keys")}</TabsTrigger>
                        <TabsTrigger value="grants">{t("detail.tab.grants")}</TabsTrigger>
                    </TabsList>
                    {/* Inactive Radix tabs unmount, so the keys tab refetches on
                        every entry -- which is what we want after a write. */}
                    <TabsContent value="overview" className="mt-3 flex min-h-0 flex-1 flex-col overflow-y-auto">
                        <OverviewTab account={account} onChanged={refresh} onDeleted={onBack} />
                    </TabsContent>
                    <TabsContent value="keys" className="mt-3 flex min-h-0 flex-1 flex-col overflow-hidden">
                        <ApiKeysTab account={account} onKeysChanged={() => void refresh()} />
                    </TabsContent>
                    <TabsContent value="grants" className="mt-3 flex min-h-0 flex-1 flex-col overflow-hidden">
                        <ResourceGrantsTab account={account} onGrantsChanged={() => void refresh()} />
                    </TabsContent>
                </Tabs>
            )}
        </div>
    )
}
