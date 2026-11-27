"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Loader2 } from "lucide-react";
import { useAuthStore } from "@/stores/authStore";
import TopNav from "./TopNav";
import NotificationBell from "./NotificationBell";
import BottomNav from "@/components/shared/BottomNav";
import BrandLogo from "@/components/shared/BrandLogo";
import AccountMenu from "@/components/shared/AccountMenu";

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
    <div className="flex min-h-screen flex-col bg-background">
      <TopNav />
      {/* 窄屏（<lg）紧凑顶栏：Logo/通知/账户入口保持可达；宽屏由静态 TopNav 承载。 */}
      <header
        data-testid="compact-header"
        className="flex h-14 items-center justify-between border-b bg-background px-4 lg:hidden"
      >
        <Link href="/dashboard" aria-label="InvestRing 首页">
          <BrandLogo size={22} withWordmark />
        </Link>
        <div className="flex items-center gap-1">
          <NotificationBell />
          <AccountMenu collapsed align="end" />
        </div>
      </header>
      {/* 保留 min-w-0 作为内容宽度防御；纵向 flex 由 flex-1 承接剩余高度，窄内容由各自滚动容器约束。 */}
      <main className="min-w-0 flex-1 p-4 lg:p-8 pb-20 lg:pb-8">
        {children}
      </main>
      <BottomNav basePath="" />
    </div>
  );
}
