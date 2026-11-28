import { describe, expect, it } from "vitest";
import {
  filterDateRangeError,
  filterDraftReducer,
  type FilterDraftState,
} from "./draftFilter";

type Filters = {
  status: string;
  page: number;
};

const initial: FilterDraftState<Filters> = {
  applied: { status: "all", page: 1 },
  draft: { status: "all", page: 1 },
  open: false,
};

describe("filterDraftReducer", () => {
  it("打开时复制已应用条件，修改草稿不影响 applied", () => {
    const opened = filterDraftReducer(initial, { type: "open" });
    expect(opened).toEqual({ ...initial, open: true });

    const changed = filterDraftReducer(opened, {
      type: "setDraft",
      patch: { status: "confirmed" },
    });
    expect(changed.draft).toEqual({ status: "confirmed", page: 1 });
    expect(changed.applied).toEqual(initial.applied);
  });

  it("支持函数式草稿更新", () => {
    const state = filterDraftReducer(initial, { type: "open" });
    const changed = filterDraftReducer(state, {
      type: "setDraft",
      patch: (current) => ({ ...current, page: current.page + 1 }),
    });
    expect(changed.draft.page).toBe(2);
  });

  it("桌面 applied 更新不覆盖关闭中的草稿", () => {
    const state = { ...initial, draft: { status: "pending", page: 1 } };
    const changed = filterDraftReducer(state, {
      type: "setApplied",
      patch: (current) => ({ ...current, status: "confirmed" }),
    });
    expect(changed.applied.status).toBe("confirmed");
    expect(changed.draft.status).toBe("pending");
  });

  it("重置草稿只改 draft，重置已应用条件同时关闭", () => {
    const state = filterDraftReducer(initial, { type: "open" });
    const defaults = { status: "all", page: 1 };
    expect(filterDraftReducer(state, { type: "resetDraft", value: defaults }).draft).toEqual(defaults);
    expect(filterDraftReducer(state, { type: "resetApplied", value: defaults })).toEqual({
      applied: defaults,
      draft: defaults,
      open: false,
    });
  });

  it("应用提交草稿，放弃恢复 applied", () => {
    const state = {
      applied: { status: "all", page: 1 },
      draft: { status: "confirmed", page: 1 },
      open: true,
    };
    expect(filterDraftReducer(state, { type: "apply" })).toEqual({
      applied: state.draft,
      draft: state.draft,
      open: false,
    });
    expect(filterDraftReducer(state, { type: "discard" })).toEqual({
      applied: state.applied,
      draft: state.applied,
      open: false,
    });
  });
});

describe("filterDateRangeError", () => {
  const day = (year: number, month: number, date: number) => new Date(year, month - 1, date);

  it("允许未选择和开放区间", () => {
    expect(filterDateRangeError()).toBeNull();
    expect(filterDateRangeError(undefined, {})).toBeNull();
    expect(filterDateRangeError({ from: day(2026, 11, 1) })).toBeNull();
  });

  it("拒绝缺少起点、非法日期和倒置区间", () => {
    expect(filterDateRangeError({ to: day(2026, 11, 1) })).not.toBeNull();
    expect(filterDateRangeError({ from: new Date(Number.NaN) })).not.toBeNull();
    expect(filterDateRangeError({ from: day(2026, 11, 2), to: new Date(Number.NaN) })).not.toBeNull();
    expect(filterDateRangeError({ from: day(2026, 11, 2), to: day(2026, 11, 1) })).not.toBeNull();
  });

  it("允许同日和正序区间，并逐项校验多个区间", () => {
    expect(filterDateRangeError({ from: day(2026, 11, 1), to: day(2026, 11, 1) })).toBeNull();
    expect(filterDateRangeError(
      { from: day(2026, 11, 1), to: day(2026, 11, 2) },
      { from: day(2026, 11, 3), to: day(2026, 11, 2) },
    )).not.toBeNull();
  });
});
