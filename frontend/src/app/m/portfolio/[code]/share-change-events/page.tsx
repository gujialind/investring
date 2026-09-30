"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import ShareChangeEventsContent from "@/components/shared/ShareChangeEventsContent";

/** #646 三级详情页「事件」按钮预填参数（?product=<code>&market=<market>[&platform=][&action=create]） */
function MobileShareChangeEventsInner() {
  const searchParams = useSearchParams();
  const product = searchParams.get("product");
  const market = searchParams.get("market");
  const platform = searchParams.get("platform");
  const action = searchParams.get("action");
  const initialProduct = product ? { code: product, market: market ?? "" } : undefined;
  return (
    // key 由预填值组成：initial* 是「一次性初值」契约，同路由仅 query 变化时
    // 不重挂载会把上一个来源页的预填留在表单里（同 trades 页，#638 L2 Nit3）
    <ShareChangeEventsContent
      key={`${product}|${market}|${platform}|${action}`}
      basePath="/m/portfolio"
      variant="mobile"
      initialProduct={initialProduct}
      initialPlatform={platform ?? undefined}
      initialAction={action ?? undefined}
    />
  );
}

export default function MobileShareChangeEventsPage() {
  return (
    <Suspense fallback={null}>
      <MobileShareChangeEventsInner />
    </Suspense>
  );
}
