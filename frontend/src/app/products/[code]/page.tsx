import { Suspense } from "react";
import MainLayout from "@/components/layout/MainLayout";
import AdminGuard from "@/components/shared/AdminGuard";
import ProductManageDetailContent from "@/components/shared/ProductManageDetailContent";

/** #824 产品管理-净值型产品详情页（桌面端）；market 维度走 ?market= searchParams，故包 Suspense */
export default function ProductManageDetailPage() {
  return (
    <MainLayout>
      <AdminGuard>
        <Suspense fallback={null}>
          <ProductManageDetailContent basePath="" variant="desktop" />
        </Suspense>
      </AdminGuard>
    </MainLayout>
  );
}
