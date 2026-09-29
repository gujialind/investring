import { Suspense } from "react";
import MainLayout from "@/components/layout/MainLayout";
import ProductDetailContent from "@/components/shared/ProductDetailContent";

/** #595 步骤③ 持仓产品详情页（D3，桌面端）；market 维度走 ?market= searchParams（步骤⑤起） */
export default function ProductDetailPage() {
  return (
    <MainLayout>
      <Suspense fallback={null}>
        <ProductDetailContent basePath="/portfolio" variant="desktop" />
      </Suspense>
    </MainLayout>
  );
}
