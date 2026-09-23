/**
 * The tables view's selection model.
 *
 * The detail pane used to render EVERY collected table stacked, which made the
 * page grow past the viewport and left the admin scrolling to find the table
 * they were working on. It now follows a single selected row, and that change
 * splits one interaction into two — so the things worth pinning are the ones
 * that quietly break:
 *
 *  1. Clicking a row SELECTS it; only the tick box ticks. If the row click also
 *     ticked, browsing tables would silently stage them for exposure, and
 *     "save selection" would push a set the admin never chose.
 *  2. Exactly one table is rendered. Asserted in both directions (the second
 *     table's columns appear once it is selected), so a pane that rendered
 *     nothing at all would not satisfy it.
 *  3. A table the database no longer has stays reachable. With a single-table
 *     pane, such a table has no entry in the live list, so without the merge it
 *     would be unreachable — snapshot and description intact but unopenable.
 *  4. Saving the selection echoes that table back. `set_exposed` disables every
 *     stored row whose name is not in the submitted list, so a table with no
 *     live entry would be silently un-exposed by a save the admin made for
 *     unrelated reasons.
 */
import { DatasourceTablesView } from "@/pages/DatasourcePage/DatasourceTablesView";
import {
  listAvailableTablesApi,
  listDatasourceTablesApi,
  selectDatasourceTablesApi,
} from "@/controllers/API/datasource";
import { fireEvent, render, screen, waitFor } from "@/test/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  // Echo the key AND its interpolation values: `/notCollectedHint/` alone would
  // pass even if the table name never reached the copy.
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) =>
      opts ? `${key}${JSON.stringify(opts)}` : key,
  }),
}));

vi.mock("@/controllers/API/datasource", async () => {
  const actual = await vi.importActual<typeof import("@/controllers/API/datasource")>(
    "@/controllers/API/datasource",
  );
  return {
    ...actual,
    listAvailableTablesApi: vi.fn(),
    listDatasourceTablesApi: vi.fn(),
    selectDatasourceTablesApi: vi.fn(),
    collectDatasourceTablesApi: vi.fn(),
    updateTableDescriptionApi: vi.fn(),
  };
});

vi.mock("@/controllers/request", () => ({
  captureAndAlertRequestErrorHoc: vi.fn((promise: Promise<unknown>) => promise),
}));

vi.mock("@/components/bs-ui/toast/use-toast", () => ({
  useToast: () => ({ toast: vi.fn() }),
}));

vi.mock("@/components/bs-ui/tooltip", () => ({
  Tooltip: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  TooltipContent: () => null,
  Portal: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
}));

const mockedAvailable = vi.mocked(listAvailableTablesApi);
const mockedTables = vi.mocked(listDatasourceTablesApi);
const mockedSelect = vi.mocked(selectDatasourceTablesApi);

function table(id: number, name: string, column: string, enabled: number) {
  return {
    id,
    table_name: name,
    table_comment: "",
    structure_snapshot: [{ name: column, type: "int", comment: "" }],
    business_desc: null,
    enabled,
  };
}

/**
 * The tick box inside a picker row, found by the row's own name.
 *
 * Searched across every element carrying that name rather than the first one:
 * once the table is selected the name is on screen twice — the picker row and
 * the detail pane's title — and the pane has no tick box to find.
 */
function checkboxOf(name: string): HTMLElement {
  for (const node of screen.getAllByText(name)) {
    const checkbox = node.closest("div")?.querySelector('[role="checkbox"]');
    if (checkbox) return checkbox as HTMLElement;
  }
  throw new Error(`no tick box on the "${name}" row`);
}

describe("DatasourceTablesView selection model", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // `users` is collected but not exposed, `draft` is not collected at all,
    // and `legacy` exists on the platform while the database no longer has it.
    mockedAvailable.mockResolvedValue({
      data: [
        { table_name: "orders", collected: true, enabled: 1 },
        { table_name: "users", collected: true, enabled: 0 },
        { table_name: "draft", collected: false, enabled: 0 },
      ],
      total: 3,
    } as never);
    mockedTables.mockResolvedValue({
      data: [
        table(11, "orders", "order_col", 1),
        table(12, "users", "user_col", 0),
        table(13, "legacy", "legacy_col", 1),
      ],
      total: 3,
    } as never);
    mockedSelect.mockResolvedValue({ changed: 0 } as never);
  });

  function renderView() {
    return render(
      <DatasourceTablesView datasourceId={1} datasourceName="orders-db" onBack={() => {}} />,
    );
  }

  it("selects on a row click without ticking the row's box", async () => {
    renderView();
    const row = await screen.findByText("users");

    expect(checkboxOf("users").getAttribute("aria-checked")).toBe("false");

    fireEvent.click(row);

    // Selected: the pane switched to it.
    await waitFor(() => expect(screen.getByText("user_col")).toBeTruthy());
    // Still unticked: browsing is not staging. A row click that also ticked
    // would leave this "true" and would ship `users` on the next save.
    expect(checkboxOf("users").getAttribute("aria-checked")).toBe("false");
  });

  it("shows only the selected table, not every collected one", async () => {
    renderView();
    await screen.findByText("users");

    fireEvent.click(screen.getByText("users"));
    await waitFor(() => expect(screen.getByText("user_col")).toBeTruthy());
    expect(screen.queryByText("order_col")).toBeNull();

    // Reversed: without this, a pane that rendered no columns at all would pass
    // the assertion above.
    fireEvent.click(screen.getByText("orders"));
    await waitFor(() => expect(screen.getByText("order_col")).toBeTruthy());
    expect(screen.queryByText("user_col")).toBeNull();
  });

  it("keeps a table the database no longer has reachable, and says so", async () => {
    renderView();

    await waitFor(() => {
      expect(screen.getByText("datasource.tables.notInDatabase")).toBeTruthy();
    });

    fireEvent.click(screen.getByText("legacy"));

    // Reachable, not merely listed: its snapshot opens in the pane.
    await waitFor(() => expect(screen.getByText("legacy_col")).toBeTruthy());
  });

  it("saves the selection with such a table included, not silently dropped", async () => {
    renderView();
    await screen.findByText("datasource.tables.saveSelection");

    fireEvent.click(screen.getByText("datasource.tables.saveSelection"));

    await waitFor(() => expect(mockedSelect).toHaveBeenCalled());
    const [, submitted] = mockedSelect.mock.calls[0];
    expect(submitted).toContain("orders");
    // Exposed but absent from the live list: dropping it here would disable it
    // in `set_exposed` without the admin ever choosing to.
    expect(submitted).toContain("legacy");
    expect(submitted).not.toContain("users");
  });

  it("says a selected table has not been collected yet, naming it", async () => {
    renderView();
    const row = await screen.findByText("draft");

    fireEvent.click(row);

    await waitFor(() => {
      expect(screen.getByText(/notCollectedHint/)).toBeTruthy();
    });
    expect(screen.getByText(/notCollectedHint.*draft/)).toBeTruthy();
  });
});
