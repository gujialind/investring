import MainLayout from "@/components/layout/MainLayout";
import PlatformDetailContent from "@/components/shared/PlatformDetailContent";

export default function PlatformDetailPage() {
  return (
    <MainLayout>
      <PlatformDetailContent basePath="/portfolio" variant="desktop" />
    </MainLayout>
  );
}
