"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuthStore } from "@/stores/authStore";
import { cn } from "@/lib/utils";
import { sidebarItemsForRole } from "@/components/shared/navItems";
import BrandLogo from "@/components/shared/BrandLogo";
import AccountMenu from "@/components/shared/AccountMenu";
import NotificationBell from "./NotificationBell";

/**
 * 桌面横向顶部导航（#944 V2-2）：≥lg 显示，静态不 sticky（避免与 PageHeader 的 viewport
 * 吸顶层冲突）；品牌在左、主导航居中、账户与通知在右。<lg 由 MainLayout 紧凑顶栏 + BottomNav 兜底。
 */
export default function TopNav() {
  const pathname = usePathname();
  const { user } = useAuthStore();
  const navItems = sidebarItemsForRole(user?.role);

  return (
    <header data-testid="top-nav" className="hidden border-b bg-background lg:block">
      <div className="flex h-14 items-center gap-4 px-4 lg:px-8">
        <Link
          href="/dashboard"
          aria-label="InvestRing 首页"
          className="flex shrink-0 items-center"
        >
          <BrandLogo size={24} withWordmark />
        </Link>

        <nav
          aria-label="主导航"
          className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto"
        >
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = pathname === item.href || pathname.startsWith(`${item.href}/`);

            return (
              <Link
                key={item.href}
                href={item.href}
                aria-label={item.label}
                aria-current={isActive ? "page" : undefined}
                className={cn(
                  "flex h-9 shrink-0 items-center gap-2 rounded-lg px-3 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-secondary text-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                )}
              >
                <Icon className={cn("h-5 w-5 shrink-0", isActive && "text-primary")} />
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex shrink-0 items-center gap-1">
          <AccountMenu collapsed align="end" />
          <NotificationBell />
        </div>
      </div>
    </header>
  );
}
