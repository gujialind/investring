"use client";

import { usePathname } from "next/navigation";
import BottomNav from "@/components/shared/BottomNav";
import { mobileTabPaths } from "@/components/shared/navItems";
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
  // 仅一级 Tab 页显示底部导航；钻取页（/m/portfolio/P001）、/m/platforms 等隐藏
  const showNav = TOP_LEVEL_PATHS.has(pathname);

  return (
    <div className={cn("min-h-screen bg-background", showNav && "pb-16")}>
      <main className="p-4">{children}</main>
      {showNav && <BottomNav basePath="/m" />}
    </div>
  );
}
