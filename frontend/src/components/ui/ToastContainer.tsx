"use client";

import { useUIStore } from "@/stores/uiStore";
import { X, CheckCircle, AlertCircle, Info, AlertTriangle } from "lucide-react";
import { useEffect } from "react";

export default function ToastContainer() {
  const { toasts, removeToast } = useUIStore();

  return (
    <div className="fixed top-4 right-4 z-[100] flex flex-col gap-2 w-full max-w-sm">
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} onClose={() => removeToast(toast.id)} />
      ))}
    </div>
  );
}

function ToastItem({
  toast,
  onClose,
}: {
  toast: { id: string; type: string; title: string; message?: string; duration?: number };
  onClose: () => void;
}) {
  useEffect(() => {
    const timer = setTimeout(() => {
      onClose();
    }, toast.duration || 3000);
    return () => clearTimeout(timer);
  }, [toast.duration, onClose]);

  // Toast 四型 → 语义 token（#127，visual-spec §11：info 不单独设色，与品牌蓝同源）
  const icons = {
    success: <CheckCircle className="h-5 w-5 text-success" />,
    error: <AlertCircle className="h-5 w-5 text-destructive" />,
    warning: <AlertTriangle className="h-5 w-5 text-warning" />,
    info: <Info className="h-5 w-5 text-success" />,
  };

  const borders = {
    success: "border-success/30",
    error: "border-destructive/30",
    warning: "border-warning/30",
    info: "border-success/30",
  };

  const bgColors = {
    success: "bg-success-soft",
    error: "bg-destructive-soft",
    warning: "bg-warning-soft",
    info: "bg-success-soft",
  };

  return (
    <div
      data-testid="toast-card"
      className={`rounded-lg border shadow-lg p-4 ${borders[toast.type as keyof typeof borders]} ${bgColors[toast.type as keyof typeof bgColors]}`}
      style={{ animation: 'slideIn 0.3s ease-out' }}
    >
      <div className="flex items-start gap-3">
        {icons[toast.type as keyof typeof icons]}
        <div className="flex-1 min-w-0">
          <h4 className="font-medium text-sm text-foreground">{toast.title}</h4>
          {toast.message && (
            <p className="text-sm text-muted-foreground mt-1">{toast.message}</p>
          )}
        </div>
        <button
          onClick={onClose}
          className="text-muted-foreground hover:text-foreground transition-colors"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
