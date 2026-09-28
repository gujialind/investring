"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import TradesContent from "@/components/shared/TradesContent";

/** #595 三级详情页预填参数（?product=<code>&market=<market>[&platform=][&trade_type=]） */
function MobileTradesInner() {
  const searchParams = useSearchParams();
  const product = searchParams.get("product");
  const market = searchParams.get("market");
  const platform = searchParams.get("platform");
  const tradeType = searchParams.get("trade_type");
  return (
    <TradesContent
      basePath="/m/portfolio"
      variant="mobile"
      initialProduct={product ? { code: product, market: market ?? "" } : undefined}
      initialPlatform={platform ?? undefined}
      initialTradeType={tradeType ?? undefined}
    />
  );
}

export default function MobileTradesPage() {
  return (
    <Suspense fallback={null}>
      <MobileTradesInner />
    </Suspense>
  );
}
