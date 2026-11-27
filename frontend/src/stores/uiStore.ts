import { create } from "zustand";

interface UIState {
  // 全局加载状态
  globalLoading: boolean;
  setGlobalLoading: (loading: boolean) => void;

  // Toast 通知
  toasts: ToastItem[];
  addToast: (toast: Omit<ToastItem, "id">) => void;
  removeToast: (id: string) => void;
  clearToasts: () => void;
}

export interface ToastItem {
  id: string;
  type: "success" | "error" | "warning" | "info";
  title: string;
  message?: string;
  duration?: number;
}

function generateId(): string {
  return Math.random().toString(36).substring(2, 9);
}

export const useUIStore = create<UIState>()((set) => ({
  // 全局加载
  globalLoading: false,
  setGlobalLoading: (loading) => set({ globalLoading: loading }),

  // Toast 通知
  toasts: [],
  addToast: (toast) =>
    set((state) => ({
      toasts: [
        ...state.toasts,
        { ...toast, id: generateId(), duration: toast.duration ?? 3000 },
      ],
    })),
  removeToast: (id) =>
    set((state) => ({
      toasts: state.toasts.filter((t) => t.id !== id),
    })),
  clearToasts: () => set({ toasts: [] }),
}));
