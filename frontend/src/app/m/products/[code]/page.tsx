import { Suspense } from "react";
import AdminGuard from "@/components/shared/AdminGuard";
import ProductManageDetailContent from "@/components/shared/ProductManageDetailContent";

/** #824 产品管理-净值型产品详情页（移动端；布局由 app/m/layout.tsx 的 MobileLayout 承接）；
 *  market 维度走 ?market= searchParams，故包 Suspense */
export default function MobileProductManageDetailPage() {
  return (
    <AdminGuard>
      <Suspense fallback={null}>
        <ProductManageDetailContent basePath="/m" variant="mobile" />
      </Suspense>
    </AdminGuard>
  );
}
