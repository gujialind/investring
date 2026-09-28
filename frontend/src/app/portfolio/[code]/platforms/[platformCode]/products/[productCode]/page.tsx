import { Suspense } from "react";
import MainLayout from "@/components/layout/MainLayout";
import PlatformProductDetailContent from "@/components/shared/PlatformProductDetailContent";

export default function PlatformProductDetailPage() {
  return (
    <MainLayout>
      <Suspense fallback={null}>
        <PlatformProductDetailContent basePath="/portfolio" variant="desktop" />
      </Suspense>
    </MainLayout>
  );
}
