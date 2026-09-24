/**
 * Deleting a datasource asks who still binds it, and refuses while anyone does.
 *
 * The backend guard (11711) is the authority, but its refusal is a code: it
 * cannot say WHICH workflow is in the way, and nothing else in the product
 * shows that a flow is using a datasource. So the page asks first and names
 * what has to be unbound — that dialog is the only place an admin can find out.
 *
 * Both directions are asserted. Only testing the blocked case would pass for an
 * implementation that never deletes anything.
 */
import { DatasourceListTab } from "@/pages/DatasourcePage/DatasourceListTab";
import type { DatasourceQueries } from "@/pages/DatasourcePage/useDatasourceQueries";
import {
  deleteDatasourceApi,
  getDatasourceReferencesApi,
} from "@/controllers/API/datasource";
import { bsConfirm } from "@/components/bs-ui/alertDialog/useConfirm";
import { render, screen, waitFor } from "@/test/test-utils";
import { fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("@/components/bs-icons/search", () => ({
  SearchIcon: () => <span data-testid="search-icon" />,
}));

vi.mock("@/components/bs-ui/alertDialog/useConfirm", () => ({ bsConfirm: vi.fn() }));

vi.mock("@/controllers/request", () => ({
  captureAndAlertRequestErrorHoc: vi.fn((promise: Promise<unknown>) => promise),
}));

vi.mock("@/controllers/API/datasource", async () => {
  const actual = await vi.importActual<typeof import("@/controllers/API/datasource")>(
    "@/controllers/API/datasource",
  );
  return {
    ...actual,
    deleteDatasourceApi: vi.fn(),
    getDatasourceReferencesApi: vi.fn(),
  };
});

const ROW = {
  id: 7,
  name: "orders-db",
  engine: "mysql",
  host: "10.0.0.9",
  port: 3306,
  database_name: "erp",
  username: "reader",
  writable_account: 0,
  enabled: 1,
};

function queriesWith(row: typeof ROW): DatasourceQueries {
  return {
    rows: [row],
    total: 1,
    page: 1,
    pageSize: 20,
    loading: false,
    name: "",
    setName: vi.fn(),
    setPage: vi.fn(),
    refetch: vi.fn(),
    findRow: () => null,
    tablesDatasourceId: null,
    setTablesDatasourceId: vi.fn(),
  } as unknown as DatasourceQueries;
}

const mockedConfirm = vi.mocked(bsConfirm);
const mockedReferences = vi.mocked(getDatasourceReferencesApi);
const mockedDelete = vi.mocked(deleteDatasourceApi);

function clickDelete() {
  render(<DatasourceListTab queries={queriesWith(ROW)} />);
  fireEvent.click(screen.getByText("datasource.list.delete"));
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("datasource delete guard", () => {
  it("names the referencing assistants and workflows instead of deleting", async () => {
    mockedReferences.mockResolvedValue({
      assistants: [{ id: "a-1", name: "assistant-one" }],
      flows: [{ id: "f-1", name: "risk-report" }],
    } as never);

    clickDelete();

    await waitFor(() => expect(mockedConfirm).toHaveBeenCalledTimes(1));
    expect(mockedConfirm.mock.calls[0][0].title).toBe("datasource.list.inUseTitle");
    // The delete never reaches the API — the backend would refuse anyway, and
    // firing it would surface a second, less informative error on top.
    expect(mockedDelete).not.toHaveBeenCalled();
  });

  it("lists only the half that actually references it", async () => {
    mockedReferences.mockResolvedValue({
      assistants: [],
      flows: [{ id: "f-1", name: "risk-report" }],
    } as never);

    clickDelete();

    await waitFor(() => expect(mockedConfirm).toHaveBeenCalledTimes(1));
    const desc = mockedConfirm.mock.calls[0][0].desc as React.ReactElement;
    const text = JSON.stringify(desc);
    expect(text).toContain("datasource.list.inUseFlows");
    expect(text).not.toContain("datasource.list.inUseAssistants");
  });

  it("falls through to the normal confirm when nothing references it", async () => {
    mockedReferences.mockResolvedValue({ assistants: [], flows: [] } as never);
    mockedDelete.mockResolvedValue({ deleted: ROW.id } as never);

    clickDelete();

    await waitFor(() => expect(mockedConfirm).toHaveBeenCalledTimes(1));
    expect(mockedConfirm.mock.calls[0][0].title).toBe("datasource.list.deleteTitle");

    // Driving the confirm is what proves the delete is still reachable.
    mockedConfirm.mock.calls[0][0].onOk?.(() => undefined);
    await waitFor(() => expect(mockedDelete).toHaveBeenCalledWith(ROW.id));
  });
});
