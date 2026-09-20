/**
 * F061 T-36 / T-37 / AC-48 on the tables view.
 *
 * Three things here are easy to get wrong and invisible when wrong:
 *
 *  1. **AC-48**: authorization is at the DATASOURCE level and the UI must offer
 *     no table-level grant. A "grant" affordance per table would be a plausible
 *     thing for a future edit to add, and nothing else in the suite would notice
 *     — so it is asserted as an absence here.
 *  2. **AC-14**: when the engine cannot report comments, the column must say so
 *     rather than render blank. Blank reads as "this column has no comment",
 *     which is a different and false claim.
 *  3. **AC-12**: the re-collect control must state that descriptions survive,
 *     since that is the promise an admin is trusting when they press it.
 */
import { DatasourceTablesView } from "@/pages/DatasourcePage/DatasourceTablesView";
import {
  listAvailableTablesApi,
  listDatasourceTablesApi,
} from "@/controllers/API/datasource";
import { render, screen, waitFor } from "@/test/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
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

// The expose toggle is wrapped in a Tooltip; the app supplies a
// TooltipProvider at its root, so stub the primitives rather than rebuild that
// provider tree here.
vi.mock("@/components/bs-ui/tooltip", () => ({
  Tooltip: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  TooltipContent: () => null,
  Portal: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
}));

const mockedAvailable = vi.mocked(listAvailableTablesApi);
const mockedTables = vi.mocked(listDatasourceTablesApi);

function renderView() {
  return render(
    <DatasourceTablesView datasourceId={1} datasourceName="orders-db" onBack={() => {}} />,
  );
}

describe("DatasourceTablesView (F061 T-36 / T-37 / AC-48)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedAvailable.mockResolvedValue({
      data: [{ table_name: "orders", collected: true, enabled: 1 }],
      total: 1,
    } as never);
  });

  it("offers no table-level authorization entry (AC-48)", async () => {
    mockedTables.mockResolvedValue({
      data: [
        {
          id: 11,
          table_name: "orders",
          table_comment: "orders",
          structure_snapshot: [{ name: "id", type: "int", comment: "pk" }],
          business_desc: null,
          enabled: 1,
        },
      ],
      total: 1,
    } as never);

    renderView();
    await waitFor(() => {
      // Present in both panels (available + collected), which is expected.
      expect(screen.getAllByText("orders").length).toBeGreaterThan(0);
    });

    // Asserted over the whole rendered view, not one sub-panel: the constraint
    // is that nothing in this view offers a per-table grant, wherever it sits.
    const rendered = (document.body.textContent || "").toLowerCase();
    for (const token of [
      "authorize",
      "permission",
      "grant",
      String.fromCharCode(0x6388, 0x6743), // zh: authorize
      String.fromCharCode(0x6743, 0x9650), // zh: permission
    ]) {
      expect(rendered).not.toContain(token);
    }
  });

  it("states a missing column comment instead of rendering it blank (AC-14)", async () => {
    mockedTables.mockResolvedValue({
      data: [
        {
          id: 11,
          table_name: "orders",
          table_comment: "",
          structure_snapshot: [
            // `comment` absent entirely: the dialect cannot report comments.
            { name: "id", type: "int" },
            // `comment` present but empty: it CAN report, this column has none.
            { name: "note", type: "varchar", comment: "" },
          ],
          business_desc: null,
          enabled: 1,
        },
      ],
      total: 1,
    } as never);

    renderView();

    await waitFor(() => {
      // The first column must carry the explicit "cannot report" copy.
      expect(screen.getByText("datasource.tables.commentUnavailable")).toBeTruthy();
    });
    // The second must NOT: it is a real empty comment, not a missing capability.
    expect(screen.getAllByText("datasource.tables.commentUnavailable").length).toBe(1);
  });

  it("tells the admin a re-collect preserves descriptions (AC-12)", async () => {
    mockedTables.mockResolvedValue({ data: [], total: 0 } as never);

    renderView();

    await waitFor(() => {
      expect(screen.getByText("datasource.tables.collectHint")).toBeTruthy();
    });
    expect(screen.getByText("datasource.tables.collect")).toBeTruthy();
  });
});
