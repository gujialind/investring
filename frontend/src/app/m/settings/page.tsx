"use client";

import AdminGuard from "@/components/shared/AdminGuard";
import { MobileSettingsList } from "@/components/shared/settingsSections";

// 薄壳页：MobileLayout 由 app/m/layout.tsx 统一提供。移动设置为分组列表 + 行级钻取
// 子页（/m/settings/[section]），不再用桌面 Tabs（#878）。设置 admin-only，viewer
// 深链由 AdminGuard 拦截（#878 验收）；全角色改密走移动首页账户区。
export default function MobileSettingsPage() {
  return (
    <AdminGuard>
      <MobileSettingsList />
    </AdminGuard>
  );
}