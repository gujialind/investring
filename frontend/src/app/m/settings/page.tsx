"use client";

import SettingsContent from "@/components/shared/SettingsContent";

// 薄壳页：MobileLayout 由 app/m/layout.tsx 统一提供，此处只渲染共享内容
export default function MobileSettingsPage() {
  return <SettingsContent variant="mobile" />;
}
