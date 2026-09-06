import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PlazaFilters, type PlazaFilterState } from "../PlazaFilters";

jest.mock("~/hooks", () => ({
  useLocalize: () => (key: string) => key,
}));

const TAGS = [
  { id: 1, name: "Finance" },
  { id: 2, name: "Report" },
];

function setup(overrides: Partial<PlazaFilterState> = {}) {
  const value: PlazaFilterState = { flowType: null, tagIds: [], sortBy: "recent", ...overrides };
  const onChange = jest.fn();
  const utils = render(
    <PlazaFilters
      value={value}
      tags={TAGS}
      onChange={onChange}
    />,
  );
  return { onChange, utils, value };
}

describe("PlazaFilters", () => {
  it("renders type chips and sort trigger", () => {
    setup();
    expect(screen.getByText("com_app_plaza_type_all")).toBeTruthy();
    expect(screen.getByText("com_app_plaza_type_assistant")).toBeTruthy();
    expect(screen.getByText("com_app_plaza_type_workflow")).toBeTruthy();
    expect(screen.getByText("com_app_plaza_sort_recent")).toBeTruthy();
  });

  it("switching app type reports the flowType patch", () => {
    const { onChange } = setup();
    fireEvent.click(screen.getByText("com_app_plaza_type_assistant"));
    expect(onChange).toHaveBeenCalledWith({ flowType: 5 });
  });

  it("toggling a tag adds and removes it (multi-select, OR semantics)", () => {
    const { onChange, utils, value } = setup();
    fireEvent.click(screen.getByText("Finance"));
    expect(onChange).toHaveBeenCalledWith({ tagIds: [1] });

    // Re-render with the tag pre-selected — removing it must emit [2] only.
    utils.rerender(
      <PlazaFilters
        value={{ ...value, tagIds: [1, 2] }}
        tags={TAGS}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByText("Finance"));
    expect(onChange).toHaveBeenLastCalledWith({ tagIds: [2] });
  });

  it("selecting a sort option reports the sortBy patch", async () => {
    const { onChange } = setup();
    // Radix menus need the full pointer sequence that user-event provides.
    await userEvent.click(screen.getByText("com_app_plaza_sort_recent"));
    await userEvent.click(screen.getByText("com_app_plaza_sort_visits"));
    expect(onChange).toHaveBeenCalledWith({ sortBy: "visit_count" });
  });
});
