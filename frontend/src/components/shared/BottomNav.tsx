"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuthStore } from "@/stores/authStore";
import { cn } from "@/lib/utils";
import { navItemsForRole } from "./navItems";

interface BottomNavProps {
  /**
   * 路径前缀：桌面站 `""`、移动站 `"/m"`。导航项 `href` 自带前导 `/`，故前缀**不得带尾
   * 斜杠**——写成 `"/"` 会把 `"/dashboard"` 拼成 `"//dashboard"`，浏览器按协议相对 URL
   * 解析成名为 `dashboard` 的 host，链接离开本站、激活判定也恒假。类型只留两个合法值，
   * 让这类错误在编译期不可表示。
   */
  basePath: "" | "/m";
}

/**
 * 底部导航（#650 合并自 layout/MobileNav 与 mobile/BottomNav 两份实现，样式取较新一套：
 * backdrop-blur、图标胶囊、激活圆点。合并说明原先还把 `safe-area-pb` 列为「较新一套」的
 * 依据之一，但该类全仓无定义（globals.css 只有 number-cell / amount-large / amount-small
 * 三个 @utility），是个不生效的空类名；它在本组件与已删的 mobile/BottomNav 里都存在，
 * 属既存、不作为选型理由，也不要在别处照抄）。
 * 桌面窄屏（视口 <1024px）由 MainLayout 常驻渲染；移动站由 MobileLayout 仅在一级 Tab 页渲染。
 */
export default function BottomNav({ basePath }: BottomNavProps) {
  const pathname = usePathname();
  const { user } = useAuthStore();

  const items = navItemsForRole(user?.role).filter((item) => item.mobileTab);

  return (
    // data-testid 是 E2E 的容器锚点：Navbar 同样是 <nav> 且带 /dashboard 链接，裸
    // nav a[href=...] 会命中两处（#650 待实现项「必要时补锚点」）
    <nav
      data-testid="bottom-nav"
      className="lg:hidden fixed bottom-0 left-0 right-0 border-t bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60 z-50 safe-area-pb"
    >
      <div className="flex justify-around items-center h-16">
        {items.map((item) => {
          const Icon = item.icon;
          const href = `${basePath}${item.href}`;
          // 前缀匹配（#650 统一判定，口径见 issue 正文「active 判定」）：桌面窄屏在钻取页
          // （如 /portfolio/P001）高亮其一级项；移动站只在一级 Tab 页渲染本组件，故那边
          // 前缀与精确等价。aria-current 既是读屏的当前页标记，也是 E2E 判高亮的锚点——
          // 高亮本身是类名，定位器契约（locatorContractSelectors）禁止按它定位。
          const isActive = pathname === href || pathname.startsWith(`${href}/`);

          return (
            <Link
              key={item.href}
              href={href}
              aria-current={isActive ? "page" : undefined}
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
