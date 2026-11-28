"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import AccountMenu from "@/components/shared/AccountMenu";
import BottomNav from "@/components/shared/BottomNav";
import BrandLogo from "@/components/shared/BrandLogo";
import { mobileTabPaths } from "@/components/shared/navItems";
import NotificationBell from "@/components/layout/NotificationBell";
import { cn } from "@/lib/utils";

// 一级 Tab 页（底部导航项对应页面）；viewer 的导航项是其子集，无需按角色区分——
// BottomNav 内部已按角色出项。路径集合由导航单源派生（#650），不再手抄一份
const TOP_LEVEL_PATHS = new Set(mobileTabPaths("/m"));

export default function MobileLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  // 仅一级 Tab 页显示底部导航；钻取页（/m/portfolio/P001）等隐藏。
  // 管理页返回出口自 #904 起由页头 PageHeader 面包屑承载（布局不再渲染返回行）
  const showNav = TOP_LEVEL_PATHS.has(pathname);

  return (
    <div className={cn("min-h-screen bg-background", showNav && "safe-area-pb")}>
      {/* 所有 /m 内容页共用顶栏；静态定位，避免与 PageHeader 的 sticky top-0 争位。 */}
      <header
        data-testid="mobile-top-bar"
        className="flex h-14 items-center justify-between border-b bg-background px-4"
      >
        <Link href="/m/dashboard" aria-label="InvestRing 首页" className="flex shrink-0 items-center">
          <BrandLogo size={22} withWordmark />
        </Link>
        <div className="flex items-center gap-1">
          <NotificationBell />
          <AccountMenu collapsed align="end" />
        </div>
      </header>
      <main className={cn("p-4", showNav && "pb-16")}>{children}</main>
      {showNav && <BottomNav basePath="/m" />}
    </div>
  );
}
