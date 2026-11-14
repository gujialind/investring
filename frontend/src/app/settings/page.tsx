"use client";

import MainLayout from "@/components/layout/MainLayout";
import AdminGuard from "@/components/shared/AdminGuard";
import SettingsContent from "@/components/shared/SettingsContent";

// 设置为 admin-only（navItems.adminOnly，#878 验收：viewer 深链须 AdminGuard 拦截）。
// 全角色改密由设置外的 AccountMenu 提供，不依赖本页可达。
export default function SettingsPage() {
  return (
    <MainLayout>
      <AdminGuard>
        <SettingsContent />
      </AdminGuard>
    </MainLayout>
  );
}
