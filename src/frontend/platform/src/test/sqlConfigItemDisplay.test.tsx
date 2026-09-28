/**
 * F061 T-42 / AC-54 / AC-56: what the Agent node's SQL block shows.
 *
 * Two display defects lived here, and neither had a test — which is why both
 * survived a reviewed, fully-asserted backend.
 *
 *   1. The 数据库类型 field rendered a BLANK box on every node that had an
 *      engine. `SqlAgentParams` defaults to `"mysql"` and its validator
 *      lowercases, but the option list was capitalised (`'MySQL'`), so Radix
 *      Select could not resolve the value to an item and rendered neither the
 *      value nor the placeholder. Real data: 16 of 16 nodes carrying an engine
 *      stored the lowercase form. The defect was latent while the field was
 *      editable (re-picking wrote the capitalised form and "fixed" it); AC-56
 *      made the field read-only and thereby made the blank permanent.
 *   2. A node bound to a datasource still rendered the five legacy inline
 *      inputs — all of them empty, because a bound node never carries them.
 *      The credentials live on the datasource row and are deliberately never
 *      echoed (the picker API returns id/name/engine only, INV-19 ①). Five
 *      empty boxes read as a failed load.
 *
 * Assertions are on `screen`, not on markup shape: a "the field is not empty"
 * test written as a regex over the source would pass whatever actually renders.
 */
import SqlConfigItem from "@/pages/BuildPage/flow/FlowNode/component/SqlConfigItem";
import { listAvailableDatasourcesApi } from "@/controllers/API/datasource";
import { render, screen, waitFor } from "@/test/test-utils";
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

vi.mock("@/controllers/request", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/controllers/request")>()),
  captureAndAlertRequestErrorHoc: vi.fn((p: Promise<unknown>) => p),
}));

const BOUND_ID = 7;

function renderNode(value: Record<string, unknown>) {
  return render(
    <SqlConfigItem
      data={{ key: "sql_agent", required: false, value: { open: true, ...value } }}
      onChange={() => undefined}
      onValidate={() => undefined}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listAvailableDatasourcesApi).mockResolvedValue({
    data: [{ id: BOUND_ID, name: "orders-db", description: "", engine: "mysql" }],
    total: 1,
  } as never);
});

describe("Agent node SQL block — display (F061 T-42)", () => {
  it("shows the bound datasource's engine instead of five empty legacy fields", async () => {
    // The node still carries a STALE inline engine from before it was bound —
    // the datasource wins at runtime, so it must win on screen too. Asserting
    // "MySQL" alone would pass even if the field read the dead inline copy.
    renderNode({ datasource_id: BOUND_ID, database_engine: "oracle" });

    await waitFor(() => expect(screen.getByText("orders-db")).toBeInTheDocument());

    expect(screen.getByDisplayValue("MySQL")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("Oracle")).toBeNull();
    // ...and the legacy inputs are gone. They could only ever be empty here.
    expect(screen.queryByText("dbAddress")).toBeNull();
    expect(screen.queryByText("dbUsername")).toBeNull();
  });

  it("displays a stored lowercase engine (the blank-box defect)", async () => {
    renderNode({ database_engine: "mysql" });

    await waitFor(() => expect(screen.getByDisplayValue("MySQL")).toBeInTheDocument());
  });

  it("displays the pre-F061 'postgres' spelling that old flows still carry", async () => {
    renderNode({ database_engine: "postgres" });

    await waitFor(() => expect(screen.getByDisplayValue("PostgreSQL")).toBeInTheDocument());
  });

  it("still shows a legacy flow's inline connection (AC-54 compatibility read)", async () => {
    renderNode({
      database_engine: "mysql",
      db_address: "10.0.0.9",
      db_name: "erp",
      db_username: "reader",
    });

    await waitFor(() => expect(screen.getByText("dbAddress")).toBeInTheDocument());
    expect(screen.getByDisplayValue("10.0.0.9")).toBeInTheDocument();
    expect(screen.getByDisplayValue("erp")).toBeInTheDocument();
    expect(screen.getByDisplayValue("reader")).toBeInTheDocument();
    expect(screen.getByDisplayValue("MySQL")).toBeInTheDocument();
  });
});
