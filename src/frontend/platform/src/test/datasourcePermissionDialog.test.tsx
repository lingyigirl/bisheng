/**
 * F061 AC-46 / AC-47: what the datasource authorization UI must NOT offer.
 *
 * These are "absence" constraints, and an absence constraint nobody asserts is
 * exactly the kind that goes unimplemented without anyone noticing — the dialog
 * still works, it just grants something the spec forbids. Two separate things
 * are asserted:
 *
 *   1. No department subject for `db_datasource` (AC-47: "no department-ownership
 *      semantics"). This one is load-bearing at the API level too: the backend's
 *      `validate_resource_grants` accepts a department subject for EVERY resource
 *      type, so the refusal exists only if the UI does not offer it.
 *   2. No "everyone / public" option (AC-46). Asserted as an absence over the
 *      rendered dialog rather than by naming a component, because there is no
 *      such component to point at today — the test's job is to fail if one is
 *      ever added, not to describe the current implementation.
 *
 * The second half of the file checks the filter is per-resource: a knowledge
 * library must still offer department grants, or this change would have silently
 * removed a capability from every other resource type.
 */
import { PermissionDialog } from "@/components/bs-comp/permission/PermissionDialog";
import { getGrantableRelationModelsApi, getResourcePermissions } from "@/controllers/API/permission";
import { render, screen, waitFor } from "@/test/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("@/controllers/API/permission", () => ({
  authorizeResource: vi.fn(),
  getGrantableRelationModelsApi: vi.fn(),
  getResourcePermissions: vi.fn(),
  getResourceGrantUsersApi: vi.fn(),
  getResourceGrantDepartmentsApi: vi.fn(),
  getResourceGrantUserGroupsApi: vi.fn(),
  searchResourceGrantUsersApi: vi.fn(),
  searchResourceGrantDepartmentsApi: vi.fn(),
  searchResourceGrantUserGroupsApi: vi.fn(),
  getResourceGrantDepartmentChildrenApi: vi.fn(),
}));

vi.mock("@/controllers/request", () => ({
  captureAndAlertRequestErrorHoc: vi.fn((promise: Promise<unknown>) => promise),
}));

vi.mock("@/components/bs-ui/toast/use-toast", () => ({
  useToast: () => ({ message: vi.fn() }),
}));

// The row captions in the list are wrapped in a truncation Tooltip; stub the
// primitives so they render without needing a TooltipProvider in the test tree.
vi.mock("@/components/bs-ui/tooltip", () => ({
  Tooltip: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  TooltipContent: () => null,
  Portal: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
}));

const mockedGetResourcePermissions = vi.mocked(getResourcePermissions);
const mockedGetGrantableRelationModelsApi = vi.mocked(getGrantableRelationModelsApi);

function renderDialog(resourceType: "db_datasource" | "knowledge_library") {
  return render(
    <PermissionDialog
      open
      onOpenChange={() => {}}
      resourceType={resourceType}
      resourceId="7"
      resourceName="orders-db"
    />,
  );
}

describe("PermissionDialog subject tabs (F061 AC-46 / AC-47)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedGetResourcePermissions.mockResolvedValue([]);
    mockedGetGrantableRelationModelsApi.mockResolvedValue([]);
  });

  it("offers no department subject for a datasource (AC-47)", async () => {
    renderDialog("db_datasource");

    await waitFor(() => {
      expect(screen.getByText("subject.user")).toBeTruthy();
    });
    expect(screen.getByText("subject.userGroup")).toBeTruthy();
    expect(screen.queryByText("subject.department")).toBeNull();
  });

  it("offers no 'everyone / public' option anywhere in the dialog (AC-46)", async () => {
    renderDialog("db_datasource");
    await waitFor(() => {
      expect(screen.getByText("subject.user")).toBeTruthy();
    });

    // Read from the DIALOG, not the render container. Radix portals
    // `DialogContent` out of the container, so `container.textContent` is the
    // empty string here and any assertion made against it passes no matter what
    // the dialog renders — verified by injecting the token and watching the
    // container-based version stay green.
    const dialog = screen.getByRole("dialog");
    const rendered = (dialog.textContent || "").toLowerCase();

    // Matched on the copy an "everyone" toggle would carry in the three shipped
    // locales, plus the role keys that would label one. Spelled as code points
    // because the i18n lint rule bans hardcoded CJK in source literals.
    const forbidden = [
      "public",
      "everyone",
      "all_users",
      String.fromCharCode(0x5168, 0x5458), // zh: everyone
      String.fromCharCode(0x6240, 0x6709, 0x4eba), // zh: all people
      String.fromCharCode(0x516c, 0x958b), // ja: public
      String.fromCharCode(0x5168, 0x4f53), // ja: everyone
    ];
    for (const token of forbidden) {
      expect(rendered).not.toContain(token.toLowerCase());
    }
  });

  it("still offers the department subject for other resource types", async () => {
    renderDialog("knowledge_library");

    await waitFor(() => {
      expect(screen.getByText("subject.department")).toBeTruthy();
    });
  });
});
