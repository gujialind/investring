"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import BottomNav from "@/components/shared/BottomNav";
import { mobileExitPaths, mobileTabPaths } from "@/components/shared/navItems";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// 一级 Tab 页（底部导航项对应页面）；viewer 的导航项是其子集，无需按角色区分——
// BottomNav 内部已按角色出项。路径集合由导航单源派生（#650），不再手抄一份
const TOP_LEVEL_PATHS = new Set(mobileTabPaths("/m"));
// 不进底部 Tab 的 admin 页（平台/分类/任务）→ 返回宿主页。这些页自身没有返回入口、
// 此处又不渲染底部导航，缺这一条就是「可达即被困」（#650 验收第 4 条）
const EXIT_PAGES = new Map(mobileExitPaths("/m").map((e) => [e.path, e]));

export default function MobileLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  // 仅一级 Tab 页显示底部导航；钻取页（/m/portfolio/P001）、/m/platforms 等隐藏
  const showNav = TOP_LEVEL_PATHS.has(pathname);
  // 钻取页由页面内容自带返回入口，故只在派生出的 admin 页补这一条，避免两个返回并排
  const exit = EXIT_PAGES.get(pathname);

  return (
    <div className={cn("min-h-screen bg-background", showNav && "pb-16")}>
      {exit && (
        <div className="px-4 pt-4">
          <Button asChild variant="ghost" size="sm" className="-ml-2 text-muted-foreground">
            <Link href={exit.href} aria-label={`返回${exit.label}`}>
              <ArrowLeft className="mr-2 h-4 w-4" />
              返回{exit.label}
            </Link>
          </Button>
        </div>
      )}
      <main className={cn("p-4", exit && "pt-0")}>{children}</main>
      {showNav && <BottomNav basePath="/m" />}
    </div>
  );
}
