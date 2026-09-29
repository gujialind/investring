"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuthStore } from "@/stores/authStore";
import { cn } from "@/lib/utils";
import { navItemsForRole } from "@/components/shared/navItems";

export default function Sidebar() {
  const pathname = usePathname();
  const { user } = useAuthStore();

  // 桌面宽屏出全部导航项（含不进移动底部 Tab 的平台/分类/任务，#650 单源派生）
  const navItems = navItemsForRole(user?.role);

  return (
    <aside className="hidden lg:block w-64 border-r bg-background">
      <nav className="flex flex-col gap-2 p-4">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive = pathname === item.href || pathname.startsWith(`${item.href}/`);

          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-accent-foreground"
              )}
            >
              <Icon className="h-4 w-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
