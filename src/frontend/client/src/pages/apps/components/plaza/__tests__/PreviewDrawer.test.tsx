import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { PreviewDrawer, type PlazaApp } from "../PreviewDrawer";

const mockGetAppStatsApi = jest.fn();

jest.mock("~/api/apps", () => ({
  getAppStatsApi: (...args: unknown[]) => mockGetAppStatsApi(...args),
}));

jest.mock("~/hooks", () => ({
  useLocalize: () => (key: string) => key,
}));

const agent: PlazaApp = {
  id: 10,
  name: "Support Agent",
  description: "A test agent",
  tags: [{ id: 1, name: "Finance" }],
  flow_type: 5,
};

function setup(overrides: Record<string, unknown> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const onStartChat = jest.fn();
  const onToggleFavorite = jest.fn();
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <PreviewDrawer
        agent={agent}
        open
        onOpenChange={jest.fn()}
        onStartChat={onStartChat}
        onToggleFavorite={onToggleFavorite}
        {...overrides}
      />
    </QueryClientProvider>,
  );
  return { utils, onStartChat, onToggleFavorite };
}

describe("PreviewDrawer", () => {
  it("renders detail fields and stats from the API", async () => {
    mockGetAppStatsApi.mockResolvedValue({
      data: { recent_7d_visits: 7, total_visits: 100, success_rate: 0.9, avg_process_time: 500 },
    });
    setup();
    expect(screen.getByText("Support Agent")).toBeTruthy();
    expect(screen.getByText("A test agent")).toBeTruthy();
    expect(screen.getByText("Finance")).toBeTruthy();
    await waitFor(() => {
      expect(screen.getByText("7")).toBeTruthy();
      expect(screen.getByText("100")).toBeTruthy();
      expect(screen.getByText("90%")).toBeTruthy();
      expect(screen.getByText("500ms")).toBeTruthy();
    });
  });

  it("shows error state with retry on stats failure", async () => {
    mockGetAppStatsApi.mockRejectedValue(new Error("boom"));
    setup();
    await waitFor(() => {
      expect(screen.getByText("com_app_plaza_stats_failed")).toBeTruthy();
    });
    fireEvent.click(screen.getByText("com_app_plaza_retry"));
    expect(mockGetAppStatsApi).toHaveBeenCalledTimes(2);
  });

  it("start chat delegates to the callback", () => {
    mockGetAppStatsApi.mockResolvedValue({ data: {} });
    const { onStartChat } = setup();
    fireEvent.click(screen.getByText("com_app_plaza_preview_start"));
    expect(onStartChat).toHaveBeenCalledWith(agent);
  });

  it("favorite button reflects the isFavorited prop and toggles", () => {
    mockGetAppStatsApi.mockResolvedValue({ data: {} });
    const { onToggleFavorite } = setup({ isFavorited: true });
    expect(screen.getByText("com_app_plaza_unfavorite")).toBeTruthy();
    fireEvent.click(screen.getByText("com_app_plaza_unfavorite"));
    expect(onToggleFavorite).toHaveBeenCalledWith(agent);
  });
});
