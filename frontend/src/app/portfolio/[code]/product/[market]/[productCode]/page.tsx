"use client";

import MainLayout from "@/components/layout/MainLayout";
import ProductDetailContent from "@/components/shared/ProductDetailContent";

/** #595 步骤③ 持仓产品详情页（D3，桌面端） */
export default function ProductDetailPage() {
  return (
    <MainLayout>
      <ProductDetailContent basePath="/portfolio" variant="desktop" />
    </MainLayout>
  );
}
