"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { useAuthStore } from "@/stores/authStore";
import Navbar from "./Navbar";
import Sidebar from "./Sidebar";
import BottomNav from "@/components/shared/BottomNav";

export default function MainLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const { isAuthenticated } = useAuthStore();
  // issue #68：persist 水合竞态——首帧 isAuthenticated 恒为初始值 false，
  // 必须等 localStorage 水合完成后再做鉴权判断，否则全页加载/刷新会被误踢回 /login。
  // 初始值固定 false：SSR 预渲染时 persist API 不存在（无 localStorage），
  // 且首帧与服务端 HTML 保持一致可避免 hydration mismatch
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    const unsub = useAuthStore.persist?.onFinishHydration?.(() => setHydrated(true));
    // 订阅前可能已完成水合（同步 storage 在建 store 时即水合），再同步检查一次；
    // persist 不可用时直接视为已就绪，避免永久停留在 loading
    setHydrated(useAuthStore.persist?.hasHydrated?.() ?? true);
    return unsub;
  }, []);

  useEffect(() => {
    if (hydrated && !isAuthenticated) {
      router.replace("/login");
    }
  }, [hydrated, isAuthenticated, router]);

  if (!hydrated) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  if (!isAuthenticated) {
    return null;
  }

  return (
    <div className="min-h-screen bg-background">
      <Navbar />
      <div className="flex">
        <Sidebar />
        <main className="flex-1 p-4 lg:p-8 pb-20 lg:pb-8">
          {children}
        </main>
      </div>
      <BottomNav basePath="" />
    </div>
  );
}
