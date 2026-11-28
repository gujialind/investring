import { toDateOnly } from "@/lib/utils";

export type FilterPatch<T> = Partial<T> | ((current: T) => T);

export type FilterDraftState<T> = {
  applied: T;
  draft: T;
  open: boolean;
};

export type FilterDraftAction<T> =
  | { type: "setApplied"; patch: FilterPatch<T> }
  | { type: "setDraft"; patch: FilterPatch<T> }
  | { type: "open" }
  | { type: "resetDraft"; value: T }
  | { type: "resetApplied"; value: T }
  | { type: "apply" }
  | { type: "discard" };

function resolvePatch<T>(current: T, patch: FilterPatch<T>): T {
  return typeof patch === "function" ? patch(current) : { ...current, ...patch };
}

export function filterDraftReducer<T extends object>(
  state: FilterDraftState<T>,
  action: FilterDraftAction<T>,
): FilterDraftState<T> {
  switch (action.type) {
    case "setApplied":
      return { ...state, applied: resolvePatch(state.applied, action.patch) };
    case "setDraft":
      return { ...state, draft: resolvePatch(state.draft, action.patch) };
    case "open":
      return { ...state, draft: state.applied, open: true };
    case "resetDraft":
      return { ...state, draft: action.value };
    case "resetApplied":
      return { applied: action.value, draft: action.value, open: false };
    case "apply":
      return { applied: state.draft, draft: state.draft, open: false };
    case "discard":
      return { ...state, draft: state.applied, open: false };
  }
}

export type FilterDateRange = {
  from?: Date;
  to?: Date;
};

export function filterDateRangeError(
  ...ranges: Array<FilterDateRange | undefined>
): string | null {
  for (const range of ranges) {
    if (!range) continue;
    if (!range.from) {
      if (range.to) return "日期区间无效，请重新选择";
      continue;
    }
    const from = toDateOnly(range.from);
    const to = range.to ? toDateOnly(range.to) : "";
    if (!from || (range.to && (!to || to < from))) {
      return "日期区间无效，请重新选择";
    }
  }
  return null;
}
