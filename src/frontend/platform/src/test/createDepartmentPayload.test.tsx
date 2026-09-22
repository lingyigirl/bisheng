import { createDepartmentApi } from "@/controllers/API/department";
import { captureAndAlertRequestErrorHoc } from "@/controllers/request";
import { CreateDepartmentDialog } from "@/pages/DepartmentPage/components/CreateDepartmentDialog";
import { toast } from "@/components/bs-ui/toast/use-toast";
import { fireEvent, render, screen, waitFor } from "@/test/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockT = (key: string) => key;

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: mockT }),
}));

vi.mock("@/controllers/API/department", () => ({
  createDepartmentApi: vi.fn(),
}));

vi.mock("@/controllers/request", () => ({
  // Mirrors the real HoC's FAILURE shape (`controllers/request.ts:239-257`): it
  // swallows the rejection and RESOLVES to ``false`` once it has toasted. An
  // identity stub would leave the dialog's ``res === false`` guard unreachable,
  // so the failure path could never be exercised.
  captureAndAlertRequestErrorHoc: vi.fn((promise: Promise<unknown>) =>
    promise.catch(() => false),
  ),
}));

vi.mock("@/components/bs-ui/toast/use-toast", () => ({
  toast: vi.fn(),
}));

vi.mock("@/components/bs-comp/selectComponent/DepartmentUsersSelect", () => ({
  __esModule: true,
  default: () => <div data-testid="admin-select" />,
}));

/** Any parent other than the default one, so a test can move the picker. */
const REPICKED_PARENT_ID = 7;

vi.mock("@/components/bs-comp/department", () => ({
  TreeDepartmentSelect: ({ onChange }: { onChange: (id: number) => void }) => (
    // The picker is F038's lazy tree and its own concern — here it only needs to
    // emit a parent id different from ``defaultParentId``.
    <button
      type="button"
      data-testid="tree-select"
      onClick={() => onChange(REPICKED_PARENT_ID)}
    />
  ),
}));

const mockedCreate = vi.mocked(createDepartmentApi);
const mockedToast = vi.mocked(toast);

/** The two F062 radios, located the way a user locates them (label text). */
function radio(labelKey: string): HTMLElement {
  return screen.getByRole("radio", { name: labelKey });
}

function submit(name: string) {
  fireEvent.change(screen.getByPlaceholderText("bs:department.nameRequired"), {
    target: { value: name },
  });
  fireEvent.click(screen.getByRole("button", { name: "confirmButton" }));
}

describe("CreateDepartmentDialog node type", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedCreate.mockResolvedValue({});
    vi.mocked(captureAndAlertRequestErrorHoc).mockImplementation((promise) =>
      promise.catch(() => false),
    );
  });

  it("sends is_department: false by default, and offers the choice enabled", async () => {
    render(<CreateDepartmentDialog defaultParentId={1} onCreated={vi.fn()} onClose={vi.fn()} />);

    expect(radio("bs:department.nodeTypeOrg")).toHaveAttribute("aria-checked", "true");
    expect(radio("bs:department.nodeTypeDept")).toHaveAttribute("aria-checked", "false");
    // F062/AC-03: creation carries no privilege elevation, so the control is never
    // disabled here — unlike the same marker on the 部门设置 tab.
    expect(radio("bs:department.nodeTypeDept")).not.toBeDisabled();

    submit("NewDept");

    await waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(1));
    expect(mockedCreate).toHaveBeenCalledWith({
      name: "NewDept",
      parent_id: 1,
      is_department: false,
      admin_user_ids: undefined,
    });
  });

  it("sends is_department: true once the dept option is selected", async () => {
    render(<CreateDepartmentDialog defaultParentId={1} onCreated={vi.fn()} onClose={vi.fn()} />);

    fireEvent.click(radio("bs:department.nodeTypeDept"));
    expect(radio("bs:department.nodeTypeDept")).toHaveAttribute("aria-checked", "true");

    submit("NewDept");

    await waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(1));
    expect(mockedCreate).toHaveBeenCalledWith(
      expect.objectContaining({ is_department: true }),
    );
  });

  // What this pins is narrow, and the name says only that much: re-picking the
  // parent must not move the marker off its initial value (a handler treating
  // "under some parent" as a reason to change ``nodeType`` would fail here).
  //
  // It does NOT prove AC-03's "never inherited from the parent" — the dialog
  // never loads the parent, so inheritance cannot even be expressed today. That
  // guarantee is the literal initial state in the component, an invariant rather
  // than a behaviour, and no assertion written against this stub can reach it.
  it("does not move the marker when the parent is re-picked", async () => {
    render(<CreateDepartmentDialog defaultParentId={1} onCreated={vi.fn()} onClose={vi.fn()} />);

    fireEvent.click(screen.getByTestId("tree-select"));

    expect(radio("bs:department.nodeTypeOrg")).toHaveAttribute("aria-checked", "true");
    expect(radio("bs:department.nodeTypeDept")).toHaveAttribute("aria-checked", "false");

    submit("NewDept");

    await waitFor(() => expect(mockedCreate).toHaveBeenCalledTimes(1));
    expect(mockedCreate).toHaveBeenCalledWith(
      expect.objectContaining({ parent_id: 7, is_department: false }),
    );
  });

  // The dialog owns this branch itself: by the time it sees the resolved value
  // the HoC has already toasted, so the only thing left to get wrong is claiming
  // success. Worth pinning because the guard is what keeps a failed create from
  // refreshing the tree and telling the operator a node exists.
  it("does not report success when the create call fails", async () => {
    const onCreated = vi.fn();
    mockedCreate.mockRejectedValueOnce(new Error("create failed"));

    render(<CreateDepartmentDialog defaultParentId={1} onCreated={onCreated} onClose={vi.fn()} />);
    submit("NewDept");

    // Waiting on the re-enabled button is what makes this non-vacuous: the same
    // ``.then`` re-enables it and decides failure, so by now the guard has run.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "confirmButton" })).not.toBeDisabled(),
    );
    expect(onCreated).not.toHaveBeenCalled();
    expect(mockedToast).not.toHaveBeenCalledWith(
      expect.objectContaining({ variant: "success" }),
    );
  });
});
