"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useAuthStore } from "@/stores/authStore";
import { useUIStore } from "@/stores/uiStore";
import { cn } from "@/lib/utils";
import { sidebarItemsForRole } from "@/components/shared/navItems";
import BrandLogo from "@/components/shared/BrandLogo";
import AccountMenu from "@/components/shared/AccountMenu";
import NotificationBell from "@/components/layout/NotificationBell";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

/**
 * 桌面侧栏（#878 chrome 整合）：宽 224px（w-56）/ 收起 56px（w-14），收起偏好持久化
 * （uiStore.sidebarCollapsed）。顶部品牌 Logo（展开 mark+字标 / 收起仅 mark）+ 收起按钮，
 * 中部主导航（语境分布入口不列出，见 navItems.inNav），底部账户区（账户菜单 + 通知铃铛，
 * 收起时竖排）。原顶部 Navbar 移除，Logo/账户/通知都收进侧栏。全高 sticky，账户区贴底。
 */
export default function Sidebar() {
  const pathname = usePathname();
  const { user } = useAuthStore();
  const collapsed = useUIStore((state) => state.sidebarCollapsed);
  const toggleCollapse = useUIStore((state) => state.toggleSidebarCollapse);

  const navItems = sidebarItemsForRole(user?.role);

  return (
    <TooltipProvider delayDuration={200}>
      <aside
        className={cn(
          "sticky top-0 hidden h-screen shrink-0 flex-col border-r bg-background transition-[width] duration-200 lg:flex",
          collapsed ? "w-14" : "w-56",
        )}
      >
        {/* 顶部：品牌 Logo + 收起/展开 */}
        <div className={cn("flex items-center gap-2 border-b p-3", collapsed && "flex-col")}>
          <Link href="/dashboard" className={cn("flex min-w-0 items-center", collapsed && "justify-center")} aria-label="InvestRing 首页">
            <BrandLogo size={26} withWordmark={!collapsed} />
          </Link>
          <Button
            variant="ghost"
            size="icon"
            className={cn("h-8 w-8 shrink-0 text-muted-foreground", !collapsed && "ml-auto")}
            onClick={toggleCollapse}
            aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
            title={collapsed ? "展开侧栏" : "收起侧栏"}
          >
            {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
          </Button>
        </div>

        {/* 中部：主导航 */}
        <nav className={cn("flex flex-1 flex-col gap-1 overflow-y-auto p-2", collapsed && "items-center")}>
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = pathname === item.href || pathname.startsWith(`${item.href}/`);
            const link = (
              <Link
                key={item.href}
                href={item.href}
                aria-label={item.label}
                aria-current={isActive ? "page" : undefined}
                className={cn(
                  "flex items-center rounded-lg text-sm font-medium transition-colors",
                  collapsed ? "h-10 w-10 justify-center" : "gap-3 px-3 py-2",
                  isActive
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                )}
              >
                <Icon className="h-5 w-5 shrink-0" />
                {!collapsed && item.label}
              </Link>
            );
            return collapsed ? (
              <Tooltip key={item.href}>
                <TooltipTrigger asChild>{link}</TooltipTrigger>
                <TooltipContent side="right">{item.label}</TooltipContent>
              </Tooltip>
            ) : (
              link
            );
          })}
        </nav>

        {/* 底部：账户区（账户菜单 + 通知铃铛），收起竖排 */}
        <div className={cn("border-t p-2", collapsed ? "flex flex-col items-center gap-1" : "flex items-center gap-1")}>
          <AccountMenu collapsed={collapsed} align={collapsed ? "center" : "start"} className={collapsed ? undefined : "flex-1"} />
          <NotificationBell />
        </div>
      </aside>
    </TooltipProvider>
  );
}