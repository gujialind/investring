"use client";

import { usePathname } from "next/navigation";
import BottomNav from "@/components/shared/BottomNav";
import { mobileTabPaths } from "@/components/shared/navItems";
import { cn } from "@/lib/utils";

// 一级 Tab 页（底部导航项对应页面）；viewer 的导航项是其子集，无需按角色区分——
// BottomNav 内部已按角色出项。路径集合由导航单源派生（#650），不再手抄一份
const TOP_LEVEL_PATHS = new Set(mobileTabPaths("/m"));

/** 产品详情（/m/portfolio/{组合}/product/{产品}）：钻取页里唯一保留底部导航的形态（#944 Phase 6.2） */
const MOBILE_PRODUCT_DETAIL_PATTERN =
  /^\/m\/portfolio\/[^/]+\/product\/[^/]+\/?$/;

export default function MobileLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  // 仅一级 Tab 页与产品详情页显示底部导航；其余钻取页（/m/portfolio/P001 等）隐藏。
  // 管理页返回出口自 #904 起由页头 PageHeader 面包屑承载（布局不再渲染返回行）
  const showNav =
    TOP_LEVEL_PATHS.has(pathname) || MOBILE_PRODUCT_DETAIL_PATTERN.test(pathname);

  return (
    <div className={cn("min-h-screen bg-background", showNav && "pb-16")}>
      <main className="p-4">{children}</main>
      {showNav && <BottomNav basePath="/m" />}
    </div>
  );
}
