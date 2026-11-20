import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface ProductIdentityProps {
  /** 产品全名（消费方传入既有回退结果：name || codeLabel），自然换行、不截断 */
  name: string;
  /** 身份说明行（显示码 · 市场等），消费方传入既有格式化结果，不在此重新格式化 */
  meta: string;
  /** 平台上下文（平台-产品详情页）：独立一行，与产品名分开 */
  platformContext?: string;
  /** 非默认态 badge（已停用）：与名称分别占位，窄屏可换行、不挤压名字 */
  badge?: ReactNode;
  className?: string;
}

/**
 * #935 S2：三类产品详情的非吸顶全文身份区（双端共享，轻量展示组件）。
 * 页眉只留短标题「产品详情」，完整识别信息由本区承载：h2 全名 16/600/24 自然换行
 * （长连续串必要时断行，不 ellipsis/clamp/tooltip），代码/市场 14px 在名称下方。
 * 不新增请求、缓存、路由判断或 scroll state；管理操作行由管理消费方在本区下方维护。
 */
export default function ProductIdentity({
  name,
  meta,
  platformContext,
  badge,
  className,
}: ProductIdentityProps) {
  return (
    <section data-testid="product-identity" className={cn("space-y-1", className)}>
      {platformContext && (
        <p
          data-testid="product-platform-context"
          className="text-sm leading-5.5 text-muted-foreground"
        >
          {platformContext}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <h2
          data-testid="product-full-name"
          className="min-w-0 break-words text-base font-semibold leading-6"
        >
          {name}
        </h2>
        {badge}
      </div>
      <p data-testid="product-identity-meta" className="text-sm leading-5.5 text-muted-foreground">
        {meta}
      </p>
    </section>
  );
}