/**
 * F076: MarkdownDoc is the shared document renderer extracted from the F035
 * skill sheet. Three promises are worth locking here: wide tables get their
 * own horizontal scroll container, the code-block copy button exists only when
 * labels are supplied (the skill sheet renders without it), and the copied
 * text is the code alone — never the button's own label.
 */
import { MarkdownDoc } from "@/components/bs-comp/markdown/MarkdownDoc"
import { copyText } from "@/utils"
import { fireEvent, render, screen, waitFor } from "@/test/test-utils"
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils", () => ({ copyText: vi.fn() }))

const toastMock = vi.fn()
vi.mock("@/components/bs-ui/toast/use-toast", () => ({
    toast: (...args: unknown[]) => toastMock(...args),
    message: vi.fn(),
}))

const copyTextMock = vi.mocked(copyText)

const LABELS = { copy: "doc.codeCopy", copied: "doc.copied", failed: "doc.copyFailed" }

const CODE = "curl -s http://example.test/api/v2/auth/whoami"

const DOC = [
    "# Title",
    "",
    "Some **bold** text.",
    "",
    "| code | http |",
    "|---|---|",
    "| 11801 | 401 |",
    "",
    "```bash",
    CODE,
    "```",
].join("\n")

describe("MarkdownDoc (F076 shared document renderer)", () => {
    beforeEach(() => {
        vi.clearAllMocks()
        copyTextMock.mockResolvedValue(undefined as never)
    })

    it("renders the document and wraps tables in a horizontal scroll container", () => {
        render(<MarkdownDoc markdown={DOC} />)
        expect(screen.getByText("Title")).toBeTruthy()
        expect(screen.getByText(/bold/)).toBeTruthy()
        expect(document.querySelector(".overflow-x-auto > table")).not.toBeNull()
    })

    it("renders code blocks without a copy button unless labels are supplied", () => {
        render(<MarkdownDoc markdown={DOC} />)
        expect(screen.queryByRole("button")).toBeNull()
    })

    it("copies only the code text, never the button label", async () => {
        render(<MarkdownDoc markdown={DOC} codeCopy={LABELS} />)
        fireEvent.click(screen.getByRole("button"))
        await waitFor(() => expect(copyTextMock).toHaveBeenCalledTimes(1))
        const copied = copyTextMock.mock.calls[0][0] as string
        expect(copied).toContain(CODE)
        expect(copied).not.toContain(LABELS.copy)
        await screen.findByText(LABELS.copied)
    })

    it("reports a failed copy through the caller-supplied label", async () => {
        copyTextMock.mockRejectedValue(new Error("clipboard denied"))
        render(<MarkdownDoc markdown={DOC} codeCopy={LABELS} />)
        fireEvent.click(screen.getByRole("button"))
        await waitFor(() => expect(toastMock).toHaveBeenCalled())
        expect(toastMock.mock.calls[0][0]).toMatchObject({ variant: "warning", description: LABELS.failed })
    })
})
