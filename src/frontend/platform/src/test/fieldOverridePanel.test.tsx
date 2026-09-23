/**
 * F064 — the per-field corrections as the ADMIN sees them.
 *
 * Two screens carry them, and each has a failure mode that looks correct on
 * screen until you know what to look for:
 *
 *  1. **The dialog** (`TableDescriptionDialog`) has to show the collected value
 *     next to the input (AC-08) — "what should I write here?" is unanswerable
 *     against a blank — and it has to keep showing corrections whose column the
 *     database no longer has (AC-05). Dropping those would lose the admin's work
 *     on the next collect without saying so.
 *  2. **The detail pane** has to show the EFFECTIVE comment, i.e. the
 *     correction, plus a marker saying it is one (AC-10). Showing the collected
 *     value there is the worse kind of wrong: the admin checks their corrections
 *     against a screen that does not display them, and "no comment collected"
 *     appears next to a field the model describes perfectly well.
 *
 * Assertions use `screen`, never `container`. The dialog renders through a Radix
 * portal, so `container` is empty for everything inside it and any
 * "X does not appear" assertion would pass no matter what.
 */
import { TableDescriptionDialog } from "@/pages/DatasourcePage/TableDescriptionDialog";
import { DatasourceTablesView } from "@/pages/DatasourcePage/DatasourceTablesView";
import {
  listAvailableTablesApi,
  listDatasourceTablesApi,
  updateTableDescriptionApi,
} from "@/controllers/API/datasource";
import { fireEvent, render, screen, waitFor } from "@/test/test-utils";
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
    updateTableDescriptionApi: vi.fn(),
    selectDatasourceTablesApi: vi.fn(),
    collectDatasourceTablesApi: vi.fn(),
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

type Row = Parameters<typeof TableDescriptionDialog>[0]["table"];

const table = (over: Partial<Row> = {}): Row =>
  ({
    id: 5,
    table_name: "orders",
    table_comment: null,
    structure_snapshot: [
      { name: "id", type: "INT", comment: "primary key" },
      { name: "amount", type: "DECIMAL", comment: "old comment" },
    ],
    business_desc: "orders master table",
    column_overrides: {},
    enabled: 1,
    ...over,
  }) as Row;

describe("TableDescriptionDialog — the field list (F064 T-13 / T-15)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows each field with the value the database holds, beside an input", () => {
    render(<TableDescriptionDialog table={table()} onOpenChange={() => {}} onSaved={() => {}} />);

    // Constructive half: the fields are listed at all.
    expect(screen.getByText("id")).toBeTruthy();
    expect(screen.getByText("amount")).toBeTruthy();
    // The collected value has to be readable on the SAME screen (AC-08) --
    // this is what tells the admin what to correct.
    expect(screen.getByText(/primary key/)).toBeTruthy();
    expect(screen.getByText(/old comment/)).toBeTruthy();
    // One input per field, which is the thing being edited.
    expect(screen.getAllByRole("textbox").length).toBeGreaterThanOrEqual(3); // description + 2 fields
  });

  it("carries an existing correction back into its input", () => {
    render(
      <TableDescriptionDialog
        table={table({ column_overrides: { amount: "amount incl. tax" } })}
        onOpenChange={() => {}}
        onSaved={() => {}}
      />,
    );

    expect(screen.getByDisplayValue("amount incl. tax")).toBeTruthy();
    // The marker says this field has been corrected -- without it the admin
    // cannot tell an override from a collected value at a glance.
    expect(screen.getAllByText("datasource.description.fieldOverridden").length).toBe(1);
  });

  it("submits both the description and the corrections in one request (AC-01)", async () => {
    vi.mocked(updateTableDescriptionApi).mockResolvedValue({ id: 5 } as never);
    render(<TableDescriptionDialog table={table()} onOpenChange={() => {}} onSaved={() => {}} />);

    // The field inputs in schema order: `id` first, `amount` second. Located by
    // placeholder rather than by value, because the input holds the CORRECTION —
    // the collected comment is rendered as text beside it, not inside it.
    const inputs = screen.getAllByPlaceholderText(
      "datasource.description.fieldOverridePlaceholder",
    );
    fireEvent.change(inputs[1], { target: { value: "amount incl. tax" } });
    fireEvent.click(screen.getByText("datasource.description.save"));

    await waitFor(() => expect(updateTableDescriptionApi).toHaveBeenCalledTimes(1));
    // Only the field that was actually typed into is in the map. The untouched
    // one is absent rather than sent as an empty string, so a save cannot blank
    // a correction the admin never looked at.
    expect(vi.mocked(updateTableDescriptionApi).mock.calls[0]).toEqual([
      5,
      "orders master table",
      { amount: "amount incl. tax" },
    ]);
  });

  it("keeps a correction whose field the database no longer has, and labels it (AC-05)", () => {
    render(
      <TableDescriptionDialog
        table={table({ column_overrides: { dropped_column: "note for a dropped field" } })}
        onOpenChange={() => {}}
        onSaved={() => {}}
      />,
    );

    expect(screen.getByText("dropped_column")).toBeTruthy();
    expect(screen.getByDisplayValue("note for a dropped field")).toBeTruthy();
    expect(screen.getByText("datasource.description.fieldNotInDatabase")).toBeTruthy();
  });

  it("does not label a live field as gone", () => {
    // The opposite direction, so the marker above cannot be a constant.
    render(
      <TableDescriptionDialog
        table={table({ column_overrides: { amount: "amount incl. tax" } })}
        onOpenChange={() => {}}
        onSaved={() => {}}
      />,
    );

    expect(screen.queryByText("datasource.description.fieldNotInDatabase")).toBeNull();
  });
});

describe("DatasourceTablesView — the effective comment (F064 T-14)", () => {
  const mockedAvailable = vi.mocked(listAvailableTablesApi);
  const mockedTables = vi.mocked(listDatasourceTablesApi);

  const renderView = () =>
    render(<DatasourceTablesView datasourceId={1} datasourceName="orders-db" onBack={() => {}} />);

  beforeEach(() => {
    vi.clearAllMocks();
    mockedAvailable.mockResolvedValue({
      data: [{ table_name: "orders", collected: true, enabled: 1 }],
      total: 1,
    } as never);
  });

  const openDetail = async (row: Row) => {
    mockedTables.mockResolvedValue({ data: [row], total: 1 } as never);
    renderView();
    fireEvent.click(await screen.findByText("orders"));
    // Wait on the column table's own header, not on a field name: two cases
    // below use a fixture whose only column is `amount`, and waiting for `id`
    // there would time out for a reason unrelated to what is being tested.
    await screen.findByText("datasource.tables.colFieldComment");
  };

  it("shows the correction, and says that it is one (AC-10)", async () => {
    await openDetail(table({ column_overrides: { amount: "amount incl. tax" } }));

    expect(screen.getByText("amount incl. tax")).toBeTruthy();
    expect(screen.getByText("datasource.description.fieldOverridden")).toBeTruthy();
    // The collected value must NOT be what the admin is shown -- they would be
    // checking corrections against a screen that hides them.
    expect(screen.queryByText("old comment")).toBeNull();
  });

  it("shows the collected value when there is no correction", async () => {
    // Same fixture, one field different: without this the case above passes for
    // a component that renders the constant "amount incl. tax".
    await openDetail(table());

    expect(screen.getByText("old comment")).toBeTruthy();
    expect(screen.queryByText("datasource.description.fieldOverridden")).toBeNull();
  });

  it("does not warn about a missing comment when the field HAS a correction (AC-10)", async () => {
    // The failure this exists to catch: a dialect that reports no comments plus
    // an admin who wrote one would otherwise show "未采集到注释" next to a field
    // the model describes perfectly well.
    await openDetail(
      table({
        structure_snapshot: [{ name: "amount", type: "DECIMAL", comment: undefined }],
        column_overrides: { amount: "amount incl. tax" },
      }),
    );

    expect(screen.getByText("amount incl. tax")).toBeTruthy();
    expect(screen.queryByText("datasource.tables.commentUnavailable")).toBeNull();
  });

  it("still warns when the field has neither", async () => {
    // The opposite direction, so the absence above is not vacuous.
    await openDetail(
      table({
        structure_snapshot: [{ name: "amount", type: "DECIMAL", comment: undefined }],
        column_overrides: {},
      }),
    );

    expect(screen.getByText("datasource.tables.commentUnavailable")).toBeTruthy();
  });
});
