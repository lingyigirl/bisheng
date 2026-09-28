/**
 * F061 AC-24 (2026-09-24 缺口补齐): the assistant form's datasource picker.
 *
 * This test exists because of how the feature failed the first time. The whole
 * binding chain — `AssistantLink.datasource_id`, the read/write schemas, the
 * permission filtering, the tool injection — shipped complete, with 30+ backend
 * assertions, and AC-24 was still unreachable: **no UI could set the field.**
 * Nothing failed, because nothing asserted that an admin could actually do it.
 * So these tests render the real form and drive it, rather than scanning the
 * source for a picker that may or may not be mounted.
 *
 * The Radix Accordion unmounts collapsed content, so every assertion below is
 * behind a real expand click — asserting without expanding would assert nothing.
 */
import { listAvailableDatasourcesApi } from "@/controllers/API/datasource";
import Setting from "@/pages/BuildPage/assistant/editAssistant/Setting";
import { useAssistantStore } from "@/store/assistantStore";
import { render, screen, waitFor } from "@/test/test-utils";
import { fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("@/controllers/API/datasource", async () => {
  const actual = await vi.importActual<typeof import("@/controllers/API/datasource")>(
    "@/controllers/API/datasource",
  );
  return { ...actual, listAvailableDatasourcesApi: vi.fn() };
});

// The picker reads the request through the shared HoC; identity is enough here,
// and it keeps the test off the interceptor's toast/redirect machinery.
vi.mock("@/controllers/request", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/controllers/request")>()),
  captureAndAlertRequestErrorHoc: vi.fn((promise: Promise<unknown>) => promise),
}));

// The form's OTHER pickers fetch on mount and would take this test to the
// network. They are not what is under test, so they are stubbed out — the
// datasource picker is the only live one below.
vi.mock("@/components/bs-comp/sheets/ToolsSheet", () => ({ default: () => null }));
vi.mock("@/components/bs-comp/sheets/SkillSheet", () => ({ default: () => null }));
vi.mock("@/components/bs-comp/selectComponent/knowledge", () => ({ default: () => null }));
vi.mock("@/pages/BuildPage/assistant/editAssistant/ModelSelect", () => ({ default: () => null }));
vi.mock("@/pages/BuildPage/assistant/editAssistant/Temperature", () => ({ default: () => null }));

const AVAILABLE = [
  { id: 7, name: "orders-db", engine: "mysql" },
  { id: 9, name: "risk-db", engine: "mysql" },
];

const mockedList = vi.mocked(listAvailableDatasourcesApi);

function seed(datasourceList?: { id: number; name: string }[]) {
  useAssistantStore.setState({
    changed: false,
    assistantState: {
      id: 1,
      name: "assistant",
      model_name: 0,
      temperature: 1,
      max_token: 32000,
      prompt: "",
      guide_word: "",
      guide_question: [""],
      tool_list: [],
      flow_list: [],
      knowledge_list: [],
      datasource_list: datasourceList,
    } as never,
  });
}

async function renderAndExpand() {
  render(<Setting />);
  // Assert the panel really is collapsed first. Without this the expand click
  // below could be decorative — the assertions would pass either way, and the
  // test would not notice if the picker moved somewhere always-visible.
  expect(screen.queryByText("build.selectDatasource")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "build.datasource" }));
  await waitFor(() => expect(screen.getByText("build.selectDatasource")).toBeInTheDocument());
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedList.mockResolvedValue({ data: AVAILABLE, total: AVAILABLE.length } as never);
});

describe("assistant form — datasource binding (F061 AC-24)", () => {
  it("renders every already-bound datasource by name", async () => {
    seed([{ id: 7, name: "orders-db" }]);
    await renderAndExpand();

    expect(screen.getByText("orders-db")).toBeInTheDocument();
  });

  it("loads the pickable datasources once, through the AC-49-gated endpoint", async () => {
    seed([]);
    await renderAndExpand();

    await waitFor(() => expect(mockedList).toHaveBeenCalledTimes(1));
  });

  it("removing one drops it from the binding and keeps the rest", async () => {
    seed([
      { id: 7, name: "orders-db" },
      { id: 9, name: "risk-db" },
    ]);
    await renderAndExpand();

    // The remove affordance is the only button inside the bound row.
    fireEvent.click(screen.getByText("orders-db").parentElement!.querySelector("svg")!);

    await waitFor(() => {
      const bound = useAssistantStore.getState().assistantState.datasource_list;
      expect(bound).toEqual([{ id: 9, name: "risk-db" }]);
    });
  });

  it("survives a missing datasource_list (the field is optional on the read model)", async () => {
    seed(undefined);
    await renderAndExpand();

    // `datasource_list` is declared optional, and an assistant that never bound
    // one omits it entirely — the form must render, not throw.
    expect(screen.getByText("build.selectDatasource")).toBeInTheDocument();
  });
});
