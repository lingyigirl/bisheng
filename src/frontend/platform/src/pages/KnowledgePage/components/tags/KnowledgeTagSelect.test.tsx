import { render, screen, waitFor } from "@/test/test-utils";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/components/bs-ui/toast/use-toast", () => ({
    useToast: () => ({ toast: vi.fn() }),
}));

// Pass API promises straight through — this mirrors the real request
// interceptor, which unwraps the UnifiedResponseModel envelope before the
// promise resolves (so API functions resolve to the BARE payload).
vi.mock("@/controllers/request", () => ({
    captureAndAlertRequestErrorHoc: (promise: Promise<unknown>) => promise,
}));

const apiMocks = vi.hoisted(() => ({
    getKnowledgeTagsApi: vi.fn(),
    createKnowledgeTagApi: vi.fn(),
    setFileTagsApi: vi.fn(),
    batchAddFileTagsApi: vi.fn(),
    deleteKnowledgeTagApi: vi.fn(),
}));

vi.mock("@/controllers/API/knowledgeTags", () => ({
    getKnowledgeTagsApi: apiMocks.getKnowledgeTagsApi,
    createKnowledgeTagApi: apiMocks.createKnowledgeTagApi,
    setFileTagsApi: apiMocks.setFileTagsApi,
    batchAddFileTagsApi: apiMocks.batchAddFileTagsApi,
    deleteKnowledgeTagApi: apiMocks.deleteKnowledgeTagApi,
}));

import KnowledgeTagSelect from "./KnowledgeTagSelect";

describe("KnowledgeTagSelect create-tag regression (AC-02)", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        apiMocks.getKnowledgeTagsApi.mockResolvedValue({ data: [] });
        apiMocks.setFileTagsApi.mockResolvedValue({});
    });

    it("saves the NEW tag id after creating a tag in the dialog", async () => {
        // 2026-09-06 regression: the API resolves to the bare tag object
        // (interceptor unwraps the envelope). Reading `.data` from it would
        // yield undefined and save an invalid tag_id.
        apiMocks.createKnowledgeTagApi.mockResolvedValue({
            id: 55,
            name: "new-tag",
        });

        const user = userEvent.setup();
        const onUpdate = vi.fn();
        render(
            <KnowledgeTagSelect knowledgeId={5} fileIds={[1]} onUpdate={onUpdate}>
                <button>open</button>
            </KnowledgeTagSelect>
        );

        await user.click(screen.getByRole("button", { name: "open" }));
        await waitFor(() => expect(apiMocks.getKnowledgeTagsApi).toHaveBeenCalledTimes(1));

        // global setup mocks react-i18next with t = identity, so the
        // placeholder text renders as the key itself
        const input = screen.getByPlaceholderText("createTagPlaceholder");
        await user.type(input, "new-tag{Enter}");

        await waitFor(() =>
            expect(apiMocks.createKnowledgeTagApi).toHaveBeenCalledWith(5, "new-tag")
        );

        await user.click(screen.getByRole("button", { name: "confirm" }));

        await waitFor(() => expect(apiMocks.setFileTagsApi).toHaveBeenCalledTimes(1));
        const payload = apiMocks.setFileTagsApi.mock.calls[0][0] as {
            knowledge_id: number;
            file_id: number;
            tag_ids: number[];
        };
        expect(payload.knowledge_id).toBe(5);
        expect(payload.file_id).toBe(1);
        expect(payload.tag_ids).toEqual([55]);
        expect(onUpdate).toHaveBeenCalledTimes(1);
    });

    it("reuses an existing tag instead of duplicating it", async () => {
        apiMocks.getKnowledgeTagsApi.mockResolvedValue({
            data: [{ id: 7, name: "existing-tag" }],
        });

        const user = userEvent.setup();
        render(
            <KnowledgeTagSelect knowledgeId={5} fileIds={[1]} onUpdate={vi.fn()}>
                <button>open</button>
            </KnowledgeTagSelect>
        );

        await user.click(screen.getByRole("button", { name: "open" }));
        await waitFor(() => expect(apiMocks.getKnowledgeTagsApi).toHaveBeenCalledTimes(1));

        const input = screen.getByPlaceholderText("createTagPlaceholder");
        await user.type(input, "existing-tag{Enter}");

        // a duplicate name must not call the create API
        expect(apiMocks.createKnowledgeTagApi).not.toHaveBeenCalled();
    });
});
