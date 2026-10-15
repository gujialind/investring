"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import MainLayout from "@/components/layout/MainLayout";
import TradesContent from "@/components/shared/TradesContent";

/** #595 三级详情页预填参数（?product=<code>&market=<market>[&platform=][&trade_type=]）；#646 增 [&action=create] */
function TradesInner() {
  const searchParams = useSearchParams();
  const product = searchParams.get("product");
  const market = searchParams.get("market");
  const platform = searchParams.get("platform");
  const tradeType = searchParams.get("trade_type");
  const action = searchParams.get("action");
  // #719：现金腿的 market 是**有效空串**（`?product=CASH&market=` 深链要筛它），而「URL 漏拼
  // market 段」在 `get()` 上同样是空——两种空只在 page 层可分（has()），故在这里分流：
  // 缺段直接不预填，等价 #638「宁可不筛」的旧行为，显式空段则放行
  const initialProduct =
    product && searchParams.has("market") ? { code: product, market: market ?? "" } : undefined;
  return (
    // key 由预填值组成：initial* 是「一次性初值」契约，同路由仅 query 变化时
    // 不重挂载会把上一个产品的筛选留在页面上（#638 L2 Nit3）
    <TradesContent
      key={`${product}|${market}|${platform}|${tradeType}|${action}`}
      basePath="/portfolio"
      variant="desktop"
      initialProduct={initialProduct}
      initialPlatform={platform ?? undefined}
      initialTradeType={tradeType ?? undefined}
      initialAction={action ?? undefined}
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
