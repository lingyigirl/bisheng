import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { FavoritesSection } from "../FavoritesSection";
import { RecentSection } from "../RecentSection";

const mockGetFavoritesApi = jest.fn();
const mockGetFrequently = jest.fn();

jest.mock("~/api/apps", () => ({
  getFavoritesApi: (...args: unknown[]) => mockGetFavoritesApi(...args),
  getFrequently: (...args: unknown[]) => mockGetFrequently(...args),
}));

jest.mock("~/hooks", () => ({
  useLocalize: () => (key: string) => key,
}));

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

describe("FavoritesSection", () => {
  it("renders favorite apps and opens preview on click", async () => {
    mockGetFavoritesApi.mockResolvedValue({
      data: { list: [{ id: 10, name: "AppA", logo: "" }], total: 1 },
    });
    const onOpenPreview = jest.fn();
    renderWithClient(<FavoritesSection onOpenPreview={onOpenPreview} />);
    await waitFor(() => expect(screen.getByText("AppA")).toBeTruthy());
    fireEvent.click(screen.getByText("AppA"));
    expect(onOpenPreview).toHaveBeenCalledWith({ id: 10, name: "AppA", logo: "" });
  });

  it("hides itself when the user has no favorites", async () => {
    mockGetFavoritesApi.mockResolvedValue({ data: { list: [], total: 0 } });
    const { container } = renderWithClient(<FavoritesSection onOpenPreview={jest.fn()} />);
    await waitFor(() => expect(container.innerHTML).toBe(""));
  });
});

describe("RecentSection", () => {
  it("renders recent apps sorted by last_used_time (pin order ignored)", async () => {
    mockGetFrequently.mockResolvedValue({
      data: {
        list: [
          { id: 1, name: "PinnedOld", last_used_time: "2026-09-01T08:00:00" },
          { id: 2, name: "RecentNew", last_used_time: "2026-09-01T10:00:00" },
        ],
        total: 2,
      },
    });
    const onOpenPreview = jest.fn();
    renderWithClient(<RecentSection onOpenPreview={onOpenPreview} />);
    await waitFor(() => {
      const nodes = screen.getAllByText(/PinnedOld|RecentNew/);
      expect(nodes[0].textContent).toContain("RecentNew");
    });
  });

  it("hides itself when there is no history", async () => {
    mockGetFrequently.mockResolvedValue({ data: { list: [], total: 0 } });
    const { container } = renderWithClient(<RecentSection onOpenPreview={jest.fn()} />);
    await waitFor(() => expect(container.innerHTML).toBe(""));
  });
});
