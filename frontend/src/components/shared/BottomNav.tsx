"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuthStore } from "@/stores/authStore";
import { cn } from "@/lib/utils";
import { navItemsForRole } from "./navItems";

interface BottomNavProps {
  /** 站点前缀：桌面站 "/"、移动站 "/m"；导航项 href 相对该前缀拼接 */
  basePath: "/" | "/m";
}

/**
 * 底部导航（#650 合并自 layout/MobileNav 与 mobile/BottomNav 两份实现，样式取较新一套：
 * backdrop-blur、safe-area-pb、图标胶囊、激活圆点）。
 * 桌面窄屏由 MainLayout 常驻渲染；移动站由 MobileLayout 仅在一级 Tab 页渲染。
 */
export default function BottomNav({ basePath }: BottomNavProps) {
  const pathname = usePathname();
  const { user } = useAuthStore();

  const items = navItemsForRole(user?.role).filter((item) => item.mobileTab);

  return (
    <nav className="lg:hidden fixed bottom-0 left-0 right-0 border-t bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 z-50 safe-area-pb">
      <div className="flex justify-around items-center h-16">
        {items.map((item) => {
          const Icon = item.icon;
          const href = `${basePath}${item.href}`;
          // 前缀匹配（#650 统一判定）：桌面窄屏在钻取页（如 /portfolio/P001）仍需
          // 高亮其一级项；移动站仅一级页渲染本组件，前缀匹配与精确匹配等价
          const isActive = pathname === href || pathname.startsWith(`${href}/`);

          return (
            <Link
              key={item.href}
              href={href}
              className={cn(
                "flex flex-col items-center justify-center gap-0.5 min-w-[3.5rem] h-full px-2 text-xs font-medium transition-colors",
                isActive
                  ? "text-primary"
                  : "text-muted-foreground hover:text-foreground"
              )}
            >
              <div
                className={cn(
                  "flex items-center justify-center h-8 w-8 rounded-lg transition-colors",
                  isActive && "bg-primary/10"
                )}
              >
                <Icon className="h-5 w-5" />
              </div>
              <span className="scale-90 origin-top">{item.label}</span>
              {isActive && (
                <span className="absolute bottom-1 h-1 w-1 rounded-full bg-primary" />
              )}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
