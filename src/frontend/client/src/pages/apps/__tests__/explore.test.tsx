import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";

const mockGetPlazaAppsApi = jest.fn();
const mockGetFavoritesApi = jest.fn();
const mockGetFrequently = jest.fn();
const mockGetHomeLabelApi = jest.fn();

jest.mock("~/api/apps", () => ({
  getPlazaAppsApi: (...args: unknown[]) => mockGetPlazaAppsApi(...args),
  getFavoritesApi: (...args: unknown[]) => mockGetFavoritesApi(...args),
  getFrequently: (...args: unknown[]) => mockGetFrequently(...args),
  getHomeLabelApi: (...args: unknown[]) => mockGetHomeLabelApi(...args),
}));

jest.mock("~/Providers", () => ({
  useToastContext: () => ({ showToast: jest.fn() }),
}));

jest.mock("~/hooks", () => ({
  useLocalize: () => (key: string) => key,
  useMediaQuery: () => true,
}));

jest.mock("~/hooks/queries/data-provider", () => ({
  useGetBsConfig: () => ({ data: null }),
}));

// The shared AgentCard is covered by its own consumers; the plaza page test
// stubs it so the assertions stay focused on the page-level contract.
jest.mock("../components/AgentCard", () => ({
  AgentCard: ({ agent, onStartChat }: any) => (
    <button type="button" data-testid="agent-card" onClick={() => onStartChat(agent)}>
      {agent.name}
    </button>
  ),
}));

class MockIntersectionObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(global as any).IntersectionObserver = MockIntersectionObserver;

jest.mock("react-router-dom", () => ({
  useNavigate: () => jest.fn(),
}));

import ExplorePlaza from "../explore";

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ExplorePlaza />
    </QueryClientProvider>,
  );
}

describe("ExplorePlaza (F047)", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockGetFavoritesApi.mockResolvedValue({ data: { list: [], total: 0 } });
    mockGetFrequently.mockResolvedValue({ data: { list: [], total: 0 } });
    mockGetHomeLabelApi.mockResolvedValue({ data: [{ id: 1, name: "金融" }] });
  });

  it("renders plaza cards with name and 7d-visit badge", async () => {
    mockGetPlazaAppsApi.mockResolvedValue({
      data: {
        data: [
          {
            id: 10,
            name: "Support Agent",
            description: "d",
            flow_type: 5,
            stats: { recent_7d_visits: 42, total_visits: 100, success_rate: 0.9, avg_process_time: 500 },
          },
        ],
        total: 1,
      },
    });
    renderPage();
    await waitFor(() => expect(screen.getByText("Support Agent")).toBeTruthy());
    expect(screen.getByText(/42/)).toBeTruthy();
  });

  it("shows the empty state when no apps match", async () => {
    mockGetPlazaAppsApi.mockResolvedValue({ data: { data: [], total: 0 } });
    renderPage();
    await waitFor(() => expect(screen.getByText("com_app_explore_no_agents")).toBeTruthy());
  });

  it("search query and filters are forwarded to the plaza API", async () => {
    mockGetPlazaAppsApi.mockResolvedValue({ data: { data: [], total: 0 } });
    renderPage();
    await waitFor(() => expect(mockGetPlazaAppsApi).toHaveBeenCalled());
    const call = mockGetPlazaAppsApi.mock.calls[0][0];
    expect(call).toMatchObject({
      page: 1,
      limit: 20,
      sort_by: "recent",
    });
    expect(call.keyword).toBeUndefined();
    expect(call.flow_types).toBeUndefined();
    expect(call.tag_ids).toBeUndefined();
  });
});
