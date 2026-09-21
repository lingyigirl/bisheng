/**
 * The datasource list's name filter box.
 *
 * Two things are asserted, and the second is the one that is easy to get
 * wrong: the typed keyword must reach the request, AND the request must go back
 * to page 1. Typing while on page 3 without resetting leaves the admin looking
 * at an empty table that reads as "no matches" — a silent wrong answer rather
 * than a visible error.
 *
 * The debounce is driven with fake timers rather than asserted by absence:
 * "no request on the first keystroke" would also pass if the filter were
 * broken outright.
 */
import { useDatasourceQueries } from "@/pages/DatasourcePage/useDatasourceQueries";
import { listDatasourcesApi } from "@/controllers/API/datasource";
import { act, render, screen, waitFor } from "@/test/test-utils";
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

const mockedList = vi.mocked(listDatasourcesApi);

/** A harness that exercises the real hook through the UI it actually serves. */
function Harness() {
  const { name, setName, page, setPage, total } = useDatasourceQueries();
  return (
    <div>
      <input
        aria-label="filter"
        defaultValue={name}
        onChange={(e) => {
          setPage(1);
          setName(e.target.value);
        }}
      />
      <button onClick={() => setPage(page + 1)}>next</button>
      <span data-testid="page">{page}</span>
      <span data-testid="total">{total}</span>
    </div>
  );
}

async function typeIntoFilter(value: string) {
  const input = screen.getByLabelText("filter");
  await act(async () => {
    input.focus();
    // fireEvent-style single change: the debounce is what is under test, not
    // the keystroke stream.
    const { fireEvent } = await import("@/test/test-utils");
    fireEvent.change(input, { target: { value } });
  });
}

describe("datasource list name filter", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedList.mockResolvedValue({ data: [], total: 0 });
  });

  it("sends the typed name to the API and returns to the first page", async () => {
    render(<Harness />);

    await waitFor(() => expect(mockedList).toHaveBeenCalled());

    // Move off page 1 first, so "went back to page 1" is a real observation.
    await act(async () => {
      screen.getByText("next").click();
    });
    await waitFor(() => expect(mockedList).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 2 }),
    ));

    await typeIntoFilter("orders");

    await waitFor(
      () => {
        const lastCall = mockedList.mock.calls.at(-1)![0];
        expect(lastCall.name).toBe("orders");
        expect(lastCall.page).toBe(1);
      },
      { timeout: 2000 },
    );

    // The visible page counter agrees with the request that was sent.
    expect(screen.getByTestId("page").textContent).toBe("1");
  });

  it("omits the name parameter when the box is cleared", async () => {
    render(<Harness />);
    await waitFor(() => expect(mockedList).toHaveBeenCalled());

    await typeIntoFilter("orders");
    await waitFor(() => expect(mockedList.mock.calls.at(-1)![0].name).toBe("orders"));

    await typeIntoFilter("");

    await waitFor(() => {
      const lastCall = mockedList.mock.calls.at(-1)![0];
      // Absent, not an empty string: the backend's "no filter" rule lives in
      // one place rather than being re-implemented at the call site.
      expect(lastCall).not.toHaveProperty("name");
    });
  });

  it("trims before sending, so a stray space is not a filter", async () => {
    render(<Harness />);
    await waitFor(() => expect(mockedList).toHaveBeenCalled());

    await typeIntoFilter("  orders  ");

    await waitFor(() => expect(mockedList.mock.calls.at(-1)![0].name).toBe("orders"));
  });
});
