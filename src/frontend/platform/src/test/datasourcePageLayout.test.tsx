/**
 * The datasource page's tab panels must be flex containers.
 *
 * jsdom cannot measure layout, so this does not assert a height — it asserts the
 * property the height depends on. Every tab body in this page sizes itself with
 * `flex-1 min-h-0` so that its own list scrolls; `flex-1` means nothing outside a
 * flex parent, and `TabsContent` is a plain Radix div. With that link missing the
 * children fall back to `height: auto`, their `overflow-auto` never triggers, and
 * the page grows past the viewport instead of scrolling — which is exactly how
 * the table picker and the datasource list ended up running off the bottom of the
 * screen.
 *
 * Worth a test because nothing else can catch it: the rendered markup is
 * identical whether or not the panel is a flex container, and the visible symptom
 * only appears in a browser.
 */
import DatasourcePage from "@/pages/DatasourcePage";
import { listDatasourcesApi } from "@/controllers/API/datasource";
import { render, screen, waitFor } from "@/test/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
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

// `SearchInput` pulls in an SVG asset resolved by vite-plugin-svgr, which is not
// a valid tag name in jsdom.
vi.mock("@/components/bs-icons/search", () => ({
  SearchIcon: () => <span data-testid="search-icon" />,
}));

const mockedList = vi.mocked(listDatasourcesApi);

describe("datasource page layout", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedList.mockResolvedValue({ data: [], total: 0 });
  });

  it("makes the tab panel a flex column, so the tab bodies can scroll internally", async () => {
    render(<DatasourcePage />);

    const panel = await screen.findByRole("tabpanel");
    await waitFor(() => expect(mockedList).toHaveBeenCalled());

    // Matched as whole class tokens, not substrings: `flex-1` — which the
    // children already carry — contains "flex", so a substring check for "flex"
    // passes whether or not the panel is a flex container.
    const classes = new Set(panel.className.split(/\s+/));
    expect(classes.has("flex")).toBe(true);
    expect(classes.has("flex-col")).toBe(true);
    // The bodies below rely on being flex items that may shrink below their
    // content; without `min-h-0` a flex child refuses to and grows anyway.
    expect(classes.has("min-h-0")).toBe(true);
  });
});
