"use client";

import { ReactNode } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import { useRoleCheck } from "@/hooks/useAuth";
import EmptyState from "@/components/shared/EmptyState";
import { Button } from "@/components/ui/button";

/**
 * 管理员页面守卫：viewer 直接输入 URL 访问 admin 页面时给出友好提示，
 * 不再渲染操作按钮 + 一串 403 toast（后端权限是硬校验，此处仅体验层防护）。
 * 布局层（MainLayout / m/layout）已处理 persist 水合，此处可直接读角色。
 * #904 L2 打回修复：出口迁页头后 children 不渲染时viewer 会「可达即被困」
 * （移动非 Tab 页无 BottomNav），故无权限态自带回家出口（§8 空态变体③）。
 */
export default function AdminGuard({ children }: { children: ReactNode }) {
  const { isAdmin } = useRoleCheck();
  const pathname = usePathname();
  const home = pathname.startsWith("/m") ? "/m/dashboard" : "/dashboard";

  if (!isAdmin) {
    return (
      <EmptyState
        message="无权限访问"
        description="该页面仅管理员可用，请联系管理员或返回首页"
        action={
          <Button asChild variant="outline">
            <Link href={home}>返回首页</Link>
          </Button>
        }
      />
    );
  }

  return <>{children}</>;
}
