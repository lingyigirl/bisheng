/**
 * F076: the 接入指南 tab (the API-access handover material).
 *
 * The load-bearing promise is "what the admin reads is what the integrator
 * receives": the rendered body and the copied/exported text come from one
 * substitution, so the tests assert that equality directly. The content
 * anchors (21 error codes, 8 permission scopes) tie the shipped document to
 * the backend contract — `scopes.py` and `errcode/open_api.py` must move
 * together with `accessGuide.md`, and this test goes red if they drift.
 */
import { AccessGuideTab } from "@/pages/SystemPage/components/ServiceAccount/AccessGuideTab"
import { getOpenApiBaseUrlApi } from "@/controllers/API/serviceAccount"
import { copyText } from "@/utils"
import { fireEvent, render, screen, waitFor } from "@/test/test-utils"
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils", () => ({ copyText: vi.fn() }))

// Address provenance is fetched once on mount; the default mock keeps the
// console-origin behaviour the rest of these tests were written against.
vi.mock("@/controllers/API/serviceAccount", () => ({
    getOpenApiBaseUrlApi: vi.fn(async () => ({ configured: "", detected: null })),
}))
const getOpenApiBaseUrlMock = vi.mocked(getOpenApiBaseUrlApi)

const toastMock = vi.fn()
vi.mock("@/components/bs-ui/toast/use-toast", () => ({
    toast: (...args: unknown[]) => toastMock(...args),
    message: vi.fn(),
}))

const copyTextMock = vi.mocked(copyText)

const STORAGE_KEY = "serviceAccount.guideBaseUrl"

const createObjectURLMock = vi.fn<(blob: Blob) => string>(() => "blob:guide")
const revokeObjectURLMock = vi.fn()
let clickedAnchor: { download: string; href: string } | null = null

function readBlob(blob: Blob): Promise<string> {
    return new Promise((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve(String(reader.result))
        reader.onerror = () => reject(reader.error)
        reader.readAsText(blob)
    })
}

const baseUrlInput = () => screen.getByLabelText("guide.baseUrlLabel") as HTMLInputElement
const bodyText = () => document.body.textContent ?? ""
// The i18n mock returns keys verbatim, so the rendered label cannot distinguish
// provenance — assert the machine-readable attribute the badge carries instead.
const sourceBadge = () => document.querySelector("[data-source]") as HTMLElement | null

function setAddress(value: string) {
    const input = baseUrlInput()
    fireEvent.change(input, { target: { value } })
    fireEvent.blur(input)
}

describe("AccessGuideTab (F076)", () => {
    beforeEach(() => {
        vi.clearAllMocks()
        window.localStorage.clear()
        copyTextMock.mockResolvedValue(undefined as never)
        getOpenApiBaseUrlMock.mockResolvedValue({ configured: "", detected: null })
        clickedAnchor = null
        Object.defineProperty(URL, "createObjectURL", { value: createObjectURLMock, writable: true, configurable: true })
        Object.defineProperty(URL, "revokeObjectURL", { value: revokeObjectURLMock, writable: true, configurable: true })
        // jsdom would try to navigate on anchor.click(); record the click instead.
        HTMLAnchorElement.prototype.click = function (this: HTMLAnchorElement) {
            clickedAnchor = { download: this.download, href: this.getAttribute("href") ?? "" }
        }
    })

    it("renders the guide with this deployment's own address substituted", () => {
        render(<AccessGuideTab />)
        expect(bodyText()).toContain(`${window.location.origin}/api/v2/auth/whoami`)
        expect(bodyText()).not.toContain("{{")
    })

    it("applies an edited address to the body, normalizes it and persists it", () => {
        render(<AccessGuideTab />)
        setAddress("https://gw.example.com/")
        expect(baseUrlInput().value).toBe("https://gw.example.com")
        expect(bodyText()).toContain("https://gw.example.com/api/v2/auth/whoami")
        expect(bodyText()).toContain("wss://gw.example.com/api/v2/assistant/chat/")
        expect(window.localStorage.getItem(STORAGE_KEY)).toBe("https://gw.example.com")
    })

    it("copies exactly what it exports — one substitution, one text", async () => {
        render(<AccessGuideTab />)
        setAddress("https://gw.example.com")

        fireEvent.click(screen.getByText("guide.copy"))
        await waitFor(() => expect(copyTextMock).toHaveBeenCalledTimes(1))
        const copiedText = copyTextMock.mock.calls[0][0] as string
        // jsdom has no clipboard API, so the honest feedback is the manual hint.
        await waitFor(() => expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ description: "guide.copyManualHint" })))

        fireEvent.click(screen.getByText("guide.export"))
        await waitFor(() => expect(createObjectURLMock).toHaveBeenCalledTimes(1))
        const exportedText = await readBlob(createObjectURLMock.mock.calls[0][0] as Blob)

        expect(exportedText).toBe(copiedText)
        expect(exportedText).toContain("https://gw.example.com/api/v2/auth/whoami")
        expect(exportedText).toContain("guide.exportHeaderSource")
        expect(exportedText).not.toContain("{{")
        expect(clickedAnchor?.download).toBe("guide.exportFileName")
        await waitFor(() => expect(revokeObjectURLMock).toHaveBeenCalledWith("blob:guide"))
    })

    it("shows the copied state when the clipboard API is available", async () => {
        Object.defineProperty(navigator, "clipboard", {
            value: { writeText: vi.fn() },
            configurable: true,
        })
        render(<AccessGuideTab />)
        fireEvent.click(screen.getByText("guide.copy"))
        expect(await screen.findByText("guide.copied")).toBeTruthy()
    })

    it("rejects an invalid address without touching the committed one", () => {
        render(<AccessGuideTab />)
        setAddress("ftp://gw.example.com")
        expect(screen.getByText("guide.baseUrlInvalid")).toBeTruthy()
        // The document still shows the last valid address, not the rejected draft.
        expect(bodyText()).toContain(`${window.location.origin}/api/v2/auth/whoami`)
        expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull()
    })

    it("restores the deployment address with the reset action", () => {
        render(<AccessGuideTab />)
        setAddress("https://gw.example.com")
        fireEvent.click(screen.getByText("guide.baseUrlReset"))
        expect(baseUrlInput().value).toBe(window.location.origin)
    })

    it("restores a stored address on mount (survives a reload)", () => {
        window.localStorage.setItem(STORAGE_KEY, "https://stored.example.com/bisheng")
        render(<AccessGuideTab />)
        expect(baseUrlInput().value).toBe("https://stored.example.com/bisheng")
        expect(bodyText()).toContain("https://stored.example.com/bisheng/api/v2/auth/whoami")
    })

    it("reports a clipboard rejection instead of claiming success", async () => {
        Object.defineProperty(navigator, "clipboard", { value: { writeText: vi.fn() }, configurable: true })
        copyTextMock.mockRejectedValue(new Error("denied"))
        render(<AccessGuideTab />)
        fireEvent.click(screen.getByText("guide.copy"))
        await waitFor(() => expect(toastMock).toHaveBeenCalledWith(expect.objectContaining({ description: "guide.copyFailed" })))
    })

    it("keeps the document in step with the backend contract (35 codes, 12 scopes)", () => {
        render(<AccessGuideTab />)
        const text = bodyText()
        expect(new Set(text.match(/118\d\d/g) ?? []).size).toBe(23)
        expect([...new Set(text.match(/119\d\d/g) ?? [])].sort()).toEqual([
            "11907",
            "11910",
            "11911",
            "11912",
            "11916",
        ])
        expect([...new Set(text.match(/121\d\d/g) ?? [])].sort()).toEqual([
            "12105",
            "12106",
            "12107",
            "12111",
            "12112",
            "12113",
            "12114",
        ])
        for (const scope of [
            "workflow:invoke",
            "workflow:read",
            "assistant:invoke",
            "assistant:read",
            "llm:workbench",
            "chat:invoke",
            "schedule:trigger",
            "managed_agent:invoke",
            "knowledge:read",
            "knowledge:write",
            "diagnostics:read",
            "delegate",
        ]) {
            expect(text).toContain(scope)
        }
    })

    it("shows the ops-configured address instead of the console default", async () => {
        getOpenApiBaseUrlMock.mockResolvedValue({ configured: "https://gw.example.com", detected: null })
        window.localStorage.setItem(STORAGE_KEY, "https://stored.example.com")

        render(<AccessGuideTab />)

        // Ops config outranks a stale per-browser override.
        await waitFor(() => expect(baseUrlInput().value).toBe("https://gw.example.com"))
        expect(sourceBadge()?.dataset.source).toBe("configured")
    })

    it("falls back to the backend-detected address on the dev server", async () => {
        getOpenApiBaseUrlMock.mockResolvedValue({ configured: "", detected: "http://10.0.0.5:7860" })

        render(<AccessGuideTab />)

        await waitFor(() => expect(baseUrlInput().value).toBe("http://10.0.0.5:7860"))
        expect(sourceBadge()?.dataset.source).toBe("detected")
    })

    it("keeps the console default when the address lookup fails", async () => {
        getOpenApiBaseUrlMock.mockRejectedValue(new Error("older backend"))

        render(<AccessGuideTab />)

        await waitFor(() => expect(getOpenApiBaseUrlMock).toHaveBeenCalled())
        expect(baseUrlInput().value).toBe(window.location.origin)
        expect(sourceBadge()?.dataset.source).toBe("console")
    })
})
