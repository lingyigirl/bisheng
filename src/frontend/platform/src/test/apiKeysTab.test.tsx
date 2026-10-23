/**
 * F076: key-list search + legacy-key reveal hint.
 *
 * The reveal button is only meaningful for keys with a ciphertext; legacy keys
 * must show a non-interactive "not viewable" hint instead of a disabled button
 * whose title is unreachable (bs-ui Button sets pointer-events: none).
 */
import { ApiKeysTab } from "@/pages/SystemPage/components/ServiceAccount/ApiKeysTab"
import { fireEvent, render, screen, waitFor } from "@/test/test-utils"
import { ServiceAccount, ServiceAccountKey } from "@/types/api/serviceAccount"
import { beforeEach, describe, expect, it, vi } from "vitest"

const listKeysMock = vi.hoisted(() => vi.fn())
const listScopesMock = vi.hoisted(() => vi.fn())
const listAgentsMock = vi.hoisted(() => vi.fn())

vi.mock("react-i18next", () => ({
    useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock("@/controllers/request", () => ({
    captureAndAlertRequestErrorHoc: (promise: Promise<unknown>) => promise.catch(() => undefined),
}))

vi.mock("@/controllers/API/serviceAccount", () => ({
    listServiceAccountKeysApi: listKeysMock,
    listOpenApiScopesApi: listScopesMock,
    listAssignableAgentsApi: listAgentsMock,
    revealServiceAccountKeyApi: vi.fn(),
    revokeAllServiceAccountKeysApi: vi.fn(),
    revokeServiceAccountKeyApi: vi.fn(),
    issueServiceAccountKeyApi: vi.fn(),
    updateServiceAccountKeyApi: vi.fn(),
    getServiceAccountApi: vi.fn(),
}))

vi.mock("@/components/bs-ui/toast/use-toast", () => ({
    message: vi.fn(),
    toast: vi.fn(),
    useToast: () => ({ message: vi.fn(), toast: vi.fn() }),
}))

vi.mock("@/components/bs-ui/alertDialog/useConfirm", () => ({
    bsConfirm: vi.fn(),
}))

// SearchInput pulls in an SVG asset (`?react`) that is not a valid jsdom tag name.
vi.mock("@/components/bs-icons/search", () => ({
    SearchIcon: () => <span data-testid="search-icon" />,
}))

const account = {
    id: 53,
    name: "verify076-api-1019",
    description: null,
    resource_owner_user_id: 9001,
    enabled: true,
    key_count: 2,
    last_used_at: null,
} as ServiceAccount

const keys: ServiceAccountKey[] = [
    {
        id: 1,
        name: "Prod key",
        key_mask: "bs-sak-********AbCd",
        scopes: ["assistant:invoke"],
        allowed_apps: [{ type: "assistant", id: "11" }],
        revealable: true,
        expires_at: null,
        revoked_at: null,
        last_used_at: null,
        status: "active",
    },
    {
        id: 2,
        name: "Legacy key",
        key_mask: "bs-sak-********WxYz",
        scopes: ["workflow:invoke"],
        allowed_apps: [],
        revealable: false,
        expires_at: null,
        revoked_at: null,
        last_used_at: null,
        status: "active",
    },
]

describe("ApiKeysTab", () => {
    beforeEach(() => {
        vi.clearAllMocks()
        listKeysMock.mockResolvedValue({ data: keys, total: keys.length })
        listScopesMock.mockResolvedValue({ data: [], total: 0 })
        listAgentsMock.mockResolvedValue({
            data: [{ type: "assistant", id: "11", name: "Agent Eleven", level: "editor", invocable: true }],
            total: 1,
        })
    })

    it("shows View only for revealable keys and a hint for legacy keys", async () => {
        render(<ApiKeysTab account={account} onKeysChanged={vi.fn()} />)

        expect(await screen.findByText("Prod key")).toBeTruthy()
        expect(screen.getByText("keys.reveal.action").closest("button")?.disabled).toBe(false)
        expect(screen.getByText("keys.reveal.notRevealable")).toBeTruthy()
        expect(screen.getByText("keys.reveal.notRevealable").closest("button")).toBeNull()
    })

    it("filters by key name, mask, scope, and agent name", async () => {
        render(<ApiKeysTab account={account} onKeysChanged={vi.fn()} />)
        await screen.findByText("Prod key")

        const search = screen.getByPlaceholderText("keys.searchPlaceholder")

        fireEvent.change(search, { target: { value: "Legacy" } })
        await waitFor(() => expect(screen.queryByText("Prod key")).toBeNull())
        expect(screen.getByText("Legacy key")).toBeTruthy()

        fireEvent.change(search, { target: { value: "AbCd" } })
        await waitFor(() => expect(screen.queryByText("Legacy key")).toBeNull())
        expect(screen.getByText("Prod key")).toBeTruthy()

        fireEvent.change(search, { target: { value: "workflow:invoke" } })
        await waitFor(() => expect(screen.queryByText("Prod key")).toBeNull())
        expect(screen.getByText("Legacy key")).toBeTruthy()

        fireEvent.change(search, { target: { value: "Agent Eleven" } })
        await waitFor(() => expect(screen.queryByText("Legacy key")).toBeNull())
        expect(screen.getByText("Prod key")).toBeTruthy()
    })

    it("shows a no-match state and restores rows when the search is cleared", async () => {
        render(<ApiKeysTab account={account} onKeysChanged={vi.fn()} />)
        await screen.findByText("Prod key")

        fireEvent.change(screen.getByPlaceholderText("keys.searchPlaceholder"), { target: { value: "zzz" } })
        expect(await screen.findByText("keys.noMatch")).toBeTruthy()

        fireEvent.click(screen.getByText("keys.clearSearch"))
        await waitFor(() => expect(screen.getByText("Prod key")).toBeTruthy())
        expect(screen.getByText("Legacy key")).toBeTruthy()
    })
})
