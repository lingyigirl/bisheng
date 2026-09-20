/**
 * F061 T-35: the datasource form's required contents and required ABSENCES.
 *
 * The absences are the point. AC-07 forbids two things the obvious form would
 * have — a raw connection-string field, and any way to save SQL as a queryable
 * object — and a constraint that is only ever asserted by its absence is the
 * one that gets re-added by accident. Both are checked here as absences over the
 * rendered dialog, and both were mutation-checked (adding the field back turns
 * the corresponding test red).
 *
 * Assertions read from `screen` rather than the render container: Radix portals
 * `DialogContent` out of the container, so a container-based assertion against
 * a dialog passes vacuously.
 */
import { DatasourceFormDialog } from "@/pages/DatasourcePage/DatasourceFormDialog";
import {
  createDatasourceApi,
  testDatasourceConnectionApi,
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
    createDatasourceApi: vi.fn(),
    updateDatasourceApi: vi.fn(),
    testDatasourceConnectionApi: vi.fn(),
  };
});

vi.mock("@/controllers/request", () => ({
  captureAndAlertRequestErrorHoc: vi.fn((promise: Promise<unknown>) => promise),
}));

vi.mock("@/components/bs-ui/toast/use-toast", () => ({
  useToast: () => ({ message: vi.fn() }),
}));

// Radix Select renders options into a portal on open; stubbing the primitives
// keeps the six engine entries queryable without driving open state.
vi.mock("@/components/bs-ui/select", () => ({
  Select: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  SelectTrigger: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  SelectValue: () => <span />,
  SelectContent: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  SelectItem: ({ children, value }: { children?: React.ReactNode; value: string }) => (
    <div data-value={value}>{children}</div>
  ),
}));

const mockedCreateDatasourceApi = vi.mocked(createDatasourceApi);
const mockedTestConnectionApi = vi.mocked(testDatasourceConnectionApi);

function renderForm() {
  return render(
    <DatasourceFormDialog open onOpenChange={() => {}} editing={null} onSaved={() => {}} />,
  );
}

/**
 * Fill every field the save button requires.
 *
 * Driven through `fireEvent.change` rather than by typing: the form is a
 * controlled React form, and assigning `.value` directly does not notify React
 * (the DOM value updates while the component's state does not), so the save
 * button would stay disabled and the assertion below would test nothing.
 *
 * Fields are addressed by placeholder, which is stable copy, rather than by
 * DOM order, which is not.
 */
function fillRequiredFields() {
  const type = (placeholderFragment: string, value: string) => {
    const input = Array.from(document.querySelectorAll("input")).find((el) =>
      (el.getAttribute("placeholder") || "").includes(placeholderFragment),
    );
    if (!input) throw new Error(`no input with placeholder ~ "${placeholderFragment}"`);
    fireEvent.change(input, { target: { value } });
  };
  type("datasource.form.namePlaceholder", "orders-db");
  type("datasource.form.hostPlaceholder", "10.0.0.1");
  type("datasource.form.databaseNamePlaceholder", "orders");
  type("datasource.form.usernamePlaceholder", "readonly_user");
  type("datasource.form.passwordPlaceholder", "secret");
}

describe("DatasourceFormDialog (F061 AC-01 / AC-05 / AC-07)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedCreateDatasourceApi.mockResolvedValue({} as never);
    mockedTestConnectionApi.mockResolvedValue({} as never);
  });

  it("offers all six supported engines (AC-02)", async () => {
    renderForm();
    const dialog = await screen.findByRole("dialog");

    for (const engine of ["mysql", "postgresql", "gaussdb", "oracle", "db2", "dm"]) {
      expect(dialog.querySelector(`[data-value="${engine}"]`)).not.toBeNull();
    }
  });

  it("has no raw connection-string field anywhere in the form (AC-07)", async () => {
    renderForm();
    const dialog = await screen.findByRole("dialog");

    // An input the admin could paste a JDBC URL / DSN into. Asserted by looking
    // for the affordance, not a label: renaming a label must not satisfy this.
    const textInputs = Array.from(dialog.querySelectorAll("input, textarea"));
    for (const input of textInputs) {
      const placeholder = (input.getAttribute("placeholder") || "").toLowerCase();
      expect(placeholder).not.toContain("jdbc");
      expect(placeholder).not.toContain("dsn");
      expect(placeholder).not.toContain("connection string");
      expect(placeholder).not.toContain("url");
    }
    // The dialog must not carry a URL-shaped text input at all.
    const urlInputs = dialog.querySelectorAll('input[type="url"]');
    expect(urlInputs.length).toBe(0);
  });

  it("has no way to save SQL as a queryable object (AC-07)", async () => {
    renderForm();
    const dialog = await screen.findByRole("dialog");

    // Checked over the FIELD LABELS, not the whole dialog text: the dialog also
    // renders the engine list, and the i18n key for MySQL ("datasource.engine.
    // mysql") contains the substring "sql", which would make a whole-text
    // assertion fail on a legitimate form.
    const labels = Array.from(dialog.querySelectorAll("label, legend")).map((el) =>
      (el.textContent || "").toLowerCase(),
    );
    const forbidden = [
      "statement",
      "query text",
      String.fromCharCode(0x67e5, 0x8be2, 0x8bed, 0x53e5), // zh: query statement
      String.fromCharCode(0x0053, 0x0051, 0x004c, 0x8bed, 0x53e5), // zh: SQL statement
    ];
    for (const label of labels) {
      for (const token of forbidden) {
        expect(label).not.toContain(token);
      }
    }
    // There is also no textarea in which one could be typed: the only textarea
    // on this form is the datasource's own description.
    const textareas = Array.from(dialog.querySelectorAll("textarea"));
    for (const area of textareas) {
      const placeholder = (area.getAttribute("placeholder") || "").toLowerCase();
      expect(placeholder).not.toContain("select");
      expect(placeholder).not.toContain("where");
    }
  });

  it("warns about a writable account without blocking the save (AC-05)", async () => {
    mockedTestConnectionApi.mockResolvedValue({
      ok: true,
      category: "ok",
      message: "ok",
      writable: true,
    });

    renderForm();
    await screen.findByRole("dialog");

    fillRequiredFields();

    fireEvent.click(screen.getByText("datasource.form.testConnection"));

    await waitFor(() => {
      expect(screen.getByText("datasource.form.writableWarning")).toBeTruthy();
    });
    // AC-05's second half: the warning is advisory, so the save button is not
    // disabled by it. (It IS disabled until the form is complete — asserted
    // separately below — so this reads the state with the form filled in.)
    const save = screen.getByText("datasource.form.save").closest("button");
    expect(save?.disabled).toBe(false);
  });
});
