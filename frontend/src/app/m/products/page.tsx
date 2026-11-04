"use client";

import AdminGuard from "@/components/shared/AdminGuard";
import ProductsContent from "@/components/shared/ProductsContent";

// 薄壳页：MobileLayout 由 app/m/layout.tsx 统一提供，此处只渲染共享内容
export default function MobileProductsPage() {
  return (
    <AdminGuard>
      <ProductsContent variant="mobile" basePath="/m" />
    </AdminGuard>
  );
}
