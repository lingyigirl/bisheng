/**
 * F076 setup order: grant first, then issue (2026-10-22).
 *
 * Two contracts:
 * 1. A restricted key needs at least one agent. With no assignable agents the
 *    dialog must refuse to submit -- an empty `allowed_apps` is a key that can
 *    call nothing, and it used to be written silently.
 * 2. The keys tab's empty state links to the grants tab, because that is where
 *    the agents a restricted key can point at come from.
 */
import { KeyIssueDialog } from "@/pages/SystemPage/components/ServiceAccount/KeyIssueDialog"
import { ApiKeysTab } from "@/pages/SystemPage/components/ServiceAccount/ApiKeysTab"
import { fireEvent, render, screen, waitFor } from "@/test/test-utils"
import { ServiceAccount } from "@/types/api/serviceAccount"
import { beforeEach, describe, expect, it, vi } from "vitest"

const issueMock = vi.hoisted(() => vi.fn())
const getAccountMock = vi.hoisted(() => vi.fn())
const listKeysMock = vi.hoisted(() => vi.fn())
const listScopesMock = vi.hoisted(() => vi.fn())
const listAssignableMock = vi.hoisted(() => vi.fn())
const messageMock = vi.hoisted(() => vi.fn())

vi.mock("react-i18next", () => ({
    useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock("@/controllers/request", () => ({
    captureAndAlertRequestErrorHoc: (promise: Promise<unknown>) => promise,
}))

vi.mock("@/controllers/API/serviceAccount", () => ({
    issueServiceAccountKeyApi: issueMock,
    updateServiceAccountKeyApi: vi.fn(),
    getServiceAccountApi: getAccountMock,
    listServiceAccountKeysApi: listKeysMock,
    listOpenApiScopesApi: listScopesMock,
    listAssignableAgentsApi: listAssignableMock,
    revealServiceAccountKeyApi: vi.fn(),
    revokeServiceAccountKeyApi: vi.fn(),
    revokeAllServiceAccountKeysApi: vi.fn(),
}))

vi.mock("@/components/bs-ui/toast/use-toast", () => ({
    message: messageMock,
    toast: vi.fn(),
    useToast: () => ({ message: messageMock, toast: vi.fn() }),
}))

vi.mock("@/components/bs-ui/alertDialog/useConfirm", () => ({
    bsConfirm: vi.fn(),
}))

// SearchInput pulls in an SVG asset (`?react`) that is not a valid jsdom tag.
vi.mock("@/components/bs-icons/search", () => ({
    SearchIcon: () => <span data-testid="search-icon" />,
}))

const account = {
    id: 53,
    name: "verify076",
    description: null,
    resource_owner_user_id: 9001,
    enabled: true,
    key_count: 0,
    last_used_at: null,
} as ServiceAccount

function renderDialog(assignableAgents = []) {
    return render(
        <KeyIssueDialog
            open
            accountId={account.id}
            scopes={[]}
            assignableAgents={assignableAgents}
            onClose={() => {}}
            onIssued={() => {}}
            onUpdated={() => {}}
        />,
    )
}

async function fillName() {
    fireEvent.change(screen.getByPlaceholderText("keys.form.namePlaceholder"), { target: { value: "crm-prod" } })
}

describe("KeyIssueDialog guard rails (F076 setup order)", () => {
    beforeEach(() => {
        vi.clearAllMocks()
        getAccountMock.mockResolvedValue({ ...account, enabled: true })
    })

    it("refuses to issue a restricted key when the account has no granted agents", async () => {
        renderDialog([])
        await fillName()

        fireEvent.click(screen.getByRole("button", { name: "ok" }))

        expect(messageMock).toHaveBeenCalledWith(
            expect.objectContaining({ description: "keys.form.agentsNeedGrant", variant: "warning" }),
        )
        expect(issueMock).not.toHaveBeenCalled()
    })

    it("issues an unrestricted key once the restriction is turned off", async () => {
        issueMock.mockResolvedValue({ id: 1, plaintext: "bs-sak-raw" })
        renderDialog([])
        await fillName()

        fireEvent.click(screen.getByRole("checkbox"))
        fireEvent.click(screen.getByRole("button", { name: "ok" }))

        await waitFor(() =>
            expect(issueMock).toHaveBeenCalledWith(
                account.id,
                expect.objectContaining({ name: "crm-prod", allowed_apps: null }),
            ),
        )
    })
})

describe("ApiKeysTab empty state (F076 setup order)", () => {
    beforeEach(() => {
        vi.clearAllMocks()
        listKeysMock.mockResolvedValue({ data: [] })
        listScopesMock.mockResolvedValue({ data: [] })
        listAssignableMock.mockResolvedValue({ data: [] })
    })

    it("links an empty key list to the grants tab", async () => {
        const onGoToGrants = vi.fn()
        render(<ApiKeysTab account={account} onKeysChanged={() => {}} onGoToGrants={onGoToGrants} />)

        const link = await screen.findByText("keys.form.goToGrants")
        fireEvent.click(link)

        expect(onGoToGrants).toHaveBeenCalledTimes(1)
    })
})
