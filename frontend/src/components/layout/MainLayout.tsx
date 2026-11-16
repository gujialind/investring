"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowLeft, Loader2 } from "lucide-react";
import { useAuthStore } from "@/stores/authStore";
import Sidebar from "./Sidebar";
import NotificationBell from "./NotificationBell";
import BottomNav from "@/components/shared/BottomNav";
import BrandLogo from "@/components/shared/BrandLogo";
import AccountMenu from "@/components/shared/AccountMenu";
import { Button } from "@/components/ui/button";
import { exitPaths } from "@/components/shared/navItems";

// 不在侧栏主导航的管理页（投资人/平台/分类）→ 返回宿主页（#894）。这些页自身没有返回
// 入口：宽屏侧栏不列出它们、也无高亮；窄屏侧栏隐藏且底部导航按 mobileTab 过滤同样不含，
// 页内唯一出路只剩指向 /dashboard 的 Logo——不是宿主页。缺这一条就是「可达即被困」
// （#650 验收第 4 条在桌面侧的补齐）。与 MobileLayout 共用同一份派生，判据按端取轴。
const EXIT_PAGES = new Map(exitPaths("").map((e) => [e.path, e]));

export default function MainLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const pathname = usePathname();
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

  const exit = EXIT_PAGES.get(pathname);

  return (
    <div className="min-h-screen bg-background">
      {/* 窄屏（<lg）紧凑顶栏：侧栏此时隐藏，Logo/通知/账户入口收进这里，保证账户可达。
          宽屏由侧栏承载（#878 移除原常驻 Navbar），本栏 lg:hidden 不渲染可见态。 */}
      <header className="flex h-14 items-center justify-between border-b bg-background px-4 lg:hidden">
        <Link href="/dashboard" aria-label="InvestRing 首页">
          <BrandLogo size={22} withWordmark />
        </Link>
        <div className="flex items-center gap-1">
          <NotificationBell />
          <AccountMenu collapsed align="end" />
        </div>
      </header>
      <div className="flex">
        <Sidebar />
        {/* min-w-0 解除 flex 子项的内容自动最小宽：否则页内的表/长数值会把 main 顶到
            自身最小内容宽，先挤掉 sidebar，再挤破视口（#772）。窄内容改由各自的滚动容器承接。 */}
        <main className="min-w-0 flex-1 p-4 lg:p-8 pb-20 lg:pb-8">
          {exit && (
            <Button asChild variant="ghost" size="sm" className="-ml-2 mb-4 text-muted-foreground">
              <Link href={exit.href} aria-label={`返回${exit.label}`}>
                <ArrowLeft className="mr-2 h-4 w-4" />
                返回{exit.label}
              </Link>
            </Button>
          )}
          {children}
        </main>
      </div>
      <BottomNav basePath="" />
    </div>
  );
}
