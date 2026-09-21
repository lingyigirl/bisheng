/**
 * The datasource list's two layout decisions.
 *
 * Both are asserted as they are OBSERVED, because both are the kind of change
 * that silently reverts: the count line is easy to re-add while adding another
 * toolbar control, and the pagination's centring comes from a shared component
 * rather than from this file — so reading this file alone would not tell you
 * which way it renders.
 *
 * The total-count assertion is deliberately paired with the pagination's own
 * count still being present: `showTotal` is a separate thing that stays, so
 * "the number 3 is nowhere on screen" would be the wrong assertion.
 */
import { DatasourceListTab } from "@/pages/DatasourcePage/DatasourceListTab";
import type { DatasourceQueries } from "@/pages/DatasourcePage/useDatasourceQueries";
import { listDatasourcesApi } from "@/controllers/API/datasource";
import { render, screen, waitFor } from "@/test/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  // Echo the key, and append interpolation values so a removed-and-re-added
  // "total" line is visible in the DOM as its own key rather than as a number.
  useTranslation: () => ({
    t: (key: string, opts?: Record<string, unknown>) =>
      opts && "total" in opts ? `${key}:${opts.total}` : key,
  }),
}));

vi.mock("@/controllers/API/datasource", async () => {
  const actual = await vi.importActual<typeof import("@/controllers/API/datasource")>(
    "@/controllers/API/datasource",
  );
  return { ...actual, listDatasourcesApi: vi.fn() };
});

vi.mock("@/controllers/request", () => ({
  captureAndAlertRequestErrorHoc: vi.fn((promise: Promise<unknown>) => promise),
}));

// `SearchInput` pulls in `@/components/bs-icons/search`, which is an SVG asset
// resolved by vite-plugin-svgr at build time and is not a valid tag name in
// jsdom. Stubbed here rather than avoided by not rendering the search box --
// the filter box is part of the toolbar under test.
vi.mock("@/components/bs-icons/search", () => ({
  SearchIcon: () => <span data-testid="search-icon" />,
}));

const mockedList = vi.mocked(listDatasourcesApi);

function queriesWith(total: number): DatasourceQueries {
  return {
    rows: [],
    total,
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

describe("datasource list layout", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedList.mockResolvedValue({ data: [], total: 0 });
  });

  it("does not render the datasource count next to the filter box", async () => {
    render(<DatasourceListTab queries={queriesWith(3)} />);

    // The pagination's own total is a different string and must remain.
    await waitFor(() => expect(screen.getByText(/pagination\./)).toBeInTheDocument());

    expect(screen.queryByText(/^datasource\.list\.total/)).toBeNull();
  });

  it("the count really would show up, so the absence above is not vacuous", async () => {
    // Guards the assertion itself: if the tab DID render the key, this is what
    // it would look like. Without this, a typo in the matcher would make the
    // test above pass forever.
    render(<DatasourceListTab queries={queriesWith(3)} />);
    await waitFor(() => expect(screen.getByText(/pagination\./)).toBeInTheDocument());
    expect(screen.queryByText("datasource.list.total:3")).toBeNull();
  });

  it("right-aligns the pagination rather than leaving it centred", async () => {
    const { container } = render(<DatasourceListTab queries={queriesWith(3)} />);
    await waitFor(() => expect(screen.getByText(/pagination\./)).toBeInTheDocument());

    const nav = container.querySelector('nav[role="navigation"]');
    expect(nav).not.toBeNull();
    // The shared `Pagination` ships `justify-center`; `cname` is tailwind-merge
    // based, so passing `justify-end` must REPLACE it rather than sit beside it.
    expect(nav!.className).toContain("justify-end");
    expect(nav!.className).not.toContain("justify-center");
  });
});
