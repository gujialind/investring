"use client";

import { useReducer } from "react";
import {
  filterDraftReducer,
  type FilterDraftAction,
  type FilterDraftState,
  type FilterPatch,
} from "@/lib/draftFilter";

export function useDraftFilters<T extends object>(
  initial: T,
  defaults: T,
  validate?: (draft: T) => string | null | undefined,
) {
  const [state, dispatch] = useReducer(
    (current: FilterDraftState<T>, action: FilterDraftAction<T>) =>
      filterDraftReducer(current, action),
    { applied: initial, draft: initial, open: false },
  );
  const error = validate?.(state.draft) ?? null;
  const apply = () => {
    if (error) return false;
    dispatch({ type: "apply" });
    return true;
  };

  return {
    applied: state.applied,
    draft: state.draft,
    isOpen: state.open,
    error,
    isValid: error === null,
    setApplied: (patch: FilterPatch<T>) => dispatch({ type: "setApplied", patch }),
    setDraft: (patch: FilterPatch<T>) => dispatch({ type: "setDraft", patch }),
    openDraft: () => dispatch({ type: "open" }),
    discardDraft: () => dispatch({ type: "discard" }),
    resetDraft: () => dispatch({ type: "resetDraft", value: defaults }),
    resetApplied: () => dispatch({ type: "resetApplied", value: defaults }),
    apply,
  };
}
