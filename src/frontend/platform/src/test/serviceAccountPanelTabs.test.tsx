/**
 * F076: the service-account panel grew a panel-level sub-tab — "接入指南" next
 * to the account list. The wiring contract worth locking: both sub-tabs exist,
 * and switching to the guide and back does NOT refetch the account list. The
 * list state lives on the panel component, which Radix does not unmount when a
 * sibling TabsContent hides its subtree — so the search box keeps its value
 * and listServiceAccountsApi stays at one call.
 */
import { listServiceAccountsApi } from "@/controllers/API/serviceAccount"
import { ServiceAccountPanel } from "@/pages/SystemPage/components/ServiceAccount"
import { fireEvent, render, screen, waitFor } from "@/test/test-utils"
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/controllers/API/serviceAccount", () => ({
    listServiceAccountsApi: vi.fn(),
    createServiceAccountApi: vi.fn(),
    deleteServiceAccountApi: vi.fn(),
    enableServiceAccountApi: vi.fn(),
    disableServiceAccountApi: vi.fn(),
}))

vi.mock("@/controllers/request", () => ({
    captureAndAlertRequestErrorHoc: (promise: Promise<unknown>) => promise,
}))

vi.mock("@/components/bs-ui/toast/use-toast", () => ({
    useToast: () => ({ message: vi.fn(), toast: vi.fn() }),
    toast: vi.fn(),
    message: vi.fn(),
}))

// `SearchInput` / the create button pull in SVG assets (`?react` imports that
// vite-plugin-svgr resolves at build time); under vitest they resolve to data
// URLs, which are not valid tag names in jsdom. Same stub as the datasource
// layout tests.
vi.mock("@/components/bs-icons/search", () => ({
    SearchIcon: () => <span data-testid="search-icon" />,
}))
vi.mock("@/components/bs-icons", () => ({
    PlusIcon: () => <span data-testid="plus-icon" />,
}))

const listMock = vi.mocked(listServiceAccountsApi)

describe("ServiceAccountPanel sub-tabs (F076)", () => {
    beforeEach(() => {
        vi.clearAllMocks()
        window.localStorage.clear()
        listMock.mockResolvedValue({ data: [], total: 0 })
    })

    it("shows both sub-tabs and starts on the account list", async () => {
        render(<ServiceAccountPanel />)
        expect(await screen.findByText("tabs.accounts")).toBeTruthy()
        expect(screen.getByText("tabs.guide")).toBeTruthy()
        await waitFor(() => expect(listMock).toHaveBeenCalledTimes(1))
    })

    it("keeps list state and does not refetch when switching to the guide and back", async () => {
        render(<ServiceAccountPanel />)
        await waitFor(() => expect(listMock).toHaveBeenCalledTimes(1))

        const search = screen.getByPlaceholderText("searchPlaceholder") as HTMLInputElement
        fireEvent.change(search, { target: { value: "acme" } })

        // Radix Tabs activates on mouse down, not click.
        fireEvent.mouseDown(screen.getByText("tabs.guide"))
        expect(await screen.findByText("guide.intro")).toBeTruthy()

        fireEvent.mouseDown(screen.getByText("tabs.accounts"))
        const restored = (await screen.findByPlaceholderText("searchPlaceholder")) as HTMLInputElement
        expect(restored.value).toBe("acme")
        expect(listMock).toHaveBeenCalledTimes(1)
    })
})
