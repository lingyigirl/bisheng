/**
 * F076 regression: resolving grant names for the resource-grants table must stay
 * within each list endpoint's page-size ceiling. `/api/v1/managed-agents` declares
 * `page_size le=100`, so the eager lookup used to send 200 and toast
 * "Input should be less than or equal to 100" for any account holding a
 * managed-agent grant. The ceiling is shared by all five loaders, so these tests
 * lock it for both the agent and the app loaders.
 */
import { ResourceGrantsTab } from "@/pages/SystemPage/components/ServiceAccount/ResourceGrantsTab"
import { render, screen, waitFor } from "@/test/test-utils"
import { ServiceAccount } from "@/types/api/serviceAccount"
import { beforeEach, describe, expect, it, vi } from "vitest"

const listGrantsMock = vi.hoisted(() => vi.fn())
const listManagedAgentsMock = vi.hoisted(() => vi.fn())
const getAppsMock = vi.hoisted(() => vi.fn())
const readFileLibMock = vi.hoisted(() => vi.fn())

vi.mock("react-i18next", () => ({
    useTranslation: () => ({ t: (key: string) => key }),
}))

// Pass values through untouched; a rejected lookup resolves to undefined just like
// the real helper (it toasts and returns false), so the table falls back to raw ids.
vi.mock("@/controllers/request", () => ({
    captureAndAlertRequestErrorHoc: (promise: Promise<unknown>) => promise.catch(() => undefined),
}))

vi.mock("@/controllers/API/serviceAccount", () => ({
    listResourceGrantsApi: listGrantsMock,
    mutateResourceGrantsApi: vi.fn(),
}))

vi.mock("@/controllers/API/managedAgent", () => ({
    listManagedAgentsApi: listManagedAgentsMock,
}))

vi.mock("@/controllers/API/flow", () => ({
    getAppsApi: getAppsMock,
}))

vi.mock("@/controllers/API/index", () => ({
    readFileLibDatabase: readFileLibMock,
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
    key_count: 1,
    last_used_at: null,
} as ServiceAccount

function mockEmptyLoaders() {
    listManagedAgentsMock.mockResolvedValue({ data: [], total: 0 })
    getAppsMock.mockResolvedValue({ data: [], page_size: 100, has_more: false, next_cursor: null })
    readFileLibMock.mockResolvedValue({ data: [], page_size: 100, has_more: false, next_cursor: null })
}

describe("ResourceGrantsTab name lookup page size (F076)", () => {
    beforeEach(() => {
        vi.clearAllMocks()
        mockEmptyLoaders()
    })

    it("asks for managed agents with the endpoint's maximum page size", async () => {
        listGrantsMock.mockResolvedValue({
            data: [{ resource_type: "managed_agent", resource_id: "7", level: "editor", source: "direct" }],
            total: 1,
        })
        listManagedAgentsMock.mockResolvedValue({ data: [{ id: 7, name: "Agent Seven" }], total: 1 })

        render(<ResourceGrantsTab account={account} onGrantsChanged={vi.fn()} />)

        expect(await screen.findByText("Agent Seven")).toBeTruthy()
        expect(listManagedAgentsMock).toHaveBeenCalledWith({ keyword: "", page_size: 100 })
        // The backend rejects anything above 100; no call may exceed it.
        for (const [params] of listManagedAgentsMock.mock.calls) {
            expect(params.page_size).toBeLessThanOrEqual(100)
        }
    })

    it("keeps the same ceiling for the app list loader", async () => {
        listGrantsMock.mockResolvedValue({
            data: [{ resource_type: "assistant", resource_id: "11", level: "viewer", source: "direct" }],
            total: 1,
        })
        getAppsMock.mockResolvedValue({
            data: [{ id: 11, name: "Assistant Eleven" }],
            page_size: 100,
            has_more: false,
            next_cursor: null,
        })

        render(<ResourceGrantsTab account={account} onGrantsChanged={vi.fn()} />)

        expect(await screen.findByText("Assistant Eleven")).toBeTruthy()
        expect(getAppsMock).toHaveBeenCalledWith(expect.objectContaining({ pageSize: 100 }))
    })

    it("does not look names up when the account has no grants", async () => {
        listGrantsMock.mockResolvedValue({ data: [], total: 0 })

        render(<ResourceGrantsTab account={account} onGrantsChanged={vi.fn()} />)

        expect(await screen.findByText("grants.emptyHint")).toBeTruthy()
        expect(listManagedAgentsMock).not.toHaveBeenCalled()
        expect(getAppsMock).not.toHaveBeenCalled()
        expect(readFileLibMock).not.toHaveBeenCalled()
    })

    it("falls back to the raw id when the lookup fails", async () => {
        listGrantsMock.mockResolvedValue({
            data: [{ resource_type: "managed_agent", resource_id: "7", level: "editor", source: "direct" }],
            total: 1,
        })
        listManagedAgentsMock.mockRejectedValue(new Error("Request failed with status code 422"))

        render(<ResourceGrantsTab account={account} onGrantsChanged={vi.fn()} />)

        expect(await screen.findByText("7")).toBeTruthy()
        await waitFor(() => expect(listManagedAgentsMock).toHaveBeenCalledTimes(1))
    })
})
