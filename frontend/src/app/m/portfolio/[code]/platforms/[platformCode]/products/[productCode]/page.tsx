import { Suspense } from "react";
import PlatformProductDetailContent from "@/components/shared/PlatformProductDetailContent";

export default function MobilePlatformProductDetailPage() {
  return (
    <Suspense fallback={null}>
      <PlatformProductDetailContent basePath="/m/portfolio" variant="mobile" />
    </Suspense>
  );
}
