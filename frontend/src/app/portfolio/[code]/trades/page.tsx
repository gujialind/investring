"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import MainLayout from "@/components/layout/MainLayout";
import TradesContent from "@/components/shared/TradesContent";

/** #595 三级详情页预填参数（?product=<code>&market=<market>[&platform=][&trade_type=]） */
function TradesInner() {
  const searchParams = useSearchParams();
  const product = searchParams.get("product");
  const market = searchParams.get("market");
  const platform = searchParams.get("platform");
  const tradeType = searchParams.get("trade_type");
  return (
    <TradesContent
      basePath="/portfolio"
      variant="desktop"
      initialProduct={product ? { code: product, market: market ?? "" } : undefined}
      initialPlatform={platform ?? undefined}
      initialTradeType={tradeType ?? undefined}
    />
  );
}

export default function TradesPage() {
  return (
    <MainLayout>
      <Suspense fallback={null}>
        <TradesInner />
      </Suspense>
    </MainLayout>
  );
}
