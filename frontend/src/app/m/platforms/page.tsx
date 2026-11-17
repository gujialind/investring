"use client";

import AdminGuard from "@/components/shared/AdminGuard";
import PlatformsContent from "@/components/shared/PlatformsContent";

// 薄壳页：MobileLayout 由 app/m/layout.tsx 统一提供，此处只渲染共享内容
export default function MobilePlatformsPage() {
  return (
    <AdminGuard>
      <PlatformsContent variant="mobile" />
    </AdminGuard>
  );
}
