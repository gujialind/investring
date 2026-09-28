"use client";

import ProductDetailContent from "@/components/shared/ProductDetailContent";

/** #595 步骤③ 持仓产品详情页（M3，移动端；布局由 app/m/layout.tsx 的 MobileLayout 承接） */
export default function MobileProductDetailPage() {
  return <ProductDetailContent basePath="/m/portfolio" variant="mobile" />;
}
