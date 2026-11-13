"use client";

import { MobileSettingsList } from "@/components/shared/settingsSections";

// 薄壳页：MobileLayout 由 app/m/layout.tsx 统一提供。移动设置为分组列表 + 行级钻取
// 子页（/m/settings/[section]），不再用桌面 Tabs（#878）。
export default function MobileSettingsPage() {
  return <MobileSettingsList />;
}