import { cn } from "@/lib/utils";

interface BrandLogoProps {
  /** mark 边长（px），常用 16/20/24/32/48 */
  size?: number;
  /** 是否带 InvestRing 字标（侧栏展开 / 登录页）；false 仅图形（侧栏收起） */
  withWordmark?: boolean;
  /** 单色形态：环与箭头都取 currentColor（favicon 之外的单色场景） */
  mono?: boolean;
  className?: string;
}

/**
 * InvestRing 品牌标志（#878）：开口年轮环（投资周期，右上缺口=开放申赎）+ 破环上扬箭头
 * （净值增长 breakout）。环走 var(--primary)、箭头走 var(--foreground)，随浅/暗主题自适应
 * （globals.css token）；mono 形态两者都取 currentColor。字标 Invest=foreground、Ring=primary。
 * 图形几何与尺寸档登记于 docs/design/visual-spec.md §20.5 与 issue #878 定稿。
 */
export function LogoMark({
  size = 24,
  mono = false,
  className,
}: {
  size?: number;
  mono?: boolean;
  className?: string;
}) {
  // token 是裸 HSL 三元组（globals.css），须 hsl() 包裹才是合法颜色；
  // 直接 var(--primary) 会落回 stroke:none 使图形不可见
  const ring = mono ? "currentColor" : "hsl(var(--primary))";
  const arrow = mono ? "currentColor" : "hsl(var(--foreground))";
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      role="img"
      aria-label="InvestRing"
      className={cn("shrink-0", className)}
    >
      <path
        d="M26.63 13.15A11 11 0 1 1 18.85 5.37"
        style={{ stroke: ring }}
        strokeWidth="3.6"
        strokeLinecap="round"
      />
      <path
        d="M12.5 19.5 24.5 7.5M17.8 7.5h6.7v6.7"
        style={{ stroke: arrow }}
        strokeWidth="3.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default function BrandLogo({
  size = 24,
  withWordmark = false,
  mono = false,
  className,
}: BrandLogoProps) {
  if (!withWordmark) {
    return <LogoMark size={size} mono={mono} className={className} />;
  }
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <LogoMark size={size} mono={mono} />
      <span
        aria-hidden
        className="font-bold tracking-tight text-foreground"
        style={{ fontSize: size * 0.72, lineHeight: 1 }}
      >
        Invest<span className="text-primary">Ring</span>
      </span>
    </span>
  );
}