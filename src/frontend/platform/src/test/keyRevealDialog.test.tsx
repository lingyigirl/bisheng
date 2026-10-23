/**
 * F076 increment: the reveal dialog has two modes.
 *
 * - `issued` (right after key creation) keeps the AC-39 contract: the operator
 *   must confirm the key was saved before the dialog can close.
 * - `viewed` (an explicit admin reveal, already audited server-side) can be
 *   dismissed freely and shows the audit warning instead.
 */
import { KeyRevealDialog } from "@/pages/SystemPage/components/ServiceAccount/KeyRevealDialog"
import { fireEvent, render, screen } from "@/test/test-utils"
import { describe, expect, it, vi } from "vitest"

describe("KeyRevealDialog", () => {
    it("issued mode requires the saved confirmation", () => {
        const onClose = vi.fn()
        render(
            <KeyRevealDialog open keyName="key" plaintext="bs-sak-test" mode="issued" onClose={onClose} />,
        )

        const done = screen.getByText("keys.reveal.done").closest("button") as HTMLButtonElement
        expect(done.disabled).toBe(true)
        expect(screen.getByText("keys.reveal.warningTitle")).toBeTruthy()

        fireEvent.click(screen.getByRole("checkbox"))
        expect(done.disabled).toBe(false)
        fireEvent.click(done)
        expect(onClose).toHaveBeenCalledTimes(1)
    })

    it("viewed mode can close without confirmation and shows the audit warning", () => {
        const onClose = vi.fn()
        render(
            <KeyRevealDialog open keyName="key" plaintext="bs-sak-test" mode="viewed" onClose={onClose} />,
        )

        expect(screen.getByText("keys.reveal.viewWarningTitle")).toBeTruthy()
        expect(screen.queryByText("keys.reveal.confirmSaved")).toBeNull()

        fireEvent.click(screen.getByText("keys.reveal.close"))
        expect(onClose).toHaveBeenCalledTimes(1)
    })
})
