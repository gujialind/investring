"use client";

import { Button } from "@/components/ui/button";
import { Power, Plus, ArrowRightLeft, CalendarClock, Camera } from "lucide-react";
import Link from "next/link";

interface PortfolioActionButtonsProps {
  portfolioCode: string;
  status: "draft" | "active" | "closed";
  /** 链接前缀，如 "/portfolio" 或 "/m/portfolio" */
  basePath: string;
  /** desktop: 行内按钮（可换行）；mobile: 单层 2 列网格，draft/closed 整行（#786 收敛嵌套 grid） */
  variant?: "desktop" | "mobile";
  onActivateClick: () => void;
  isActivatePending?: boolean;
}

/**
 * 组合详情页操作按钮组（草稿/活跃/关闭三态，issue #99）。
 * 详情页不再提供「关闭组合」入口（关闭仅在列表页）；业务逻辑两端一致，
 * 仅布局与链接前缀通过 variant / basePath 区分。
 * 注：后端不提供删除组合能力（外键 RESTRICT，生命周期由关闭/重新激活管理），故无删除入口。
 * #595 曾把份额变动/快照管理等低频入口收敛到「管理」入口卡（ManageLinksCard），
 * 操作区只保留高频动作（D1：页头仅 申购赎回 / 调仓）。
 * #786 部分反转 #595：份额变动事件（可见文案简称「事件」）与快照管理是常用入口，
 * 持仓多时从页底管理卡需滚动多屏才可达，故上移与申购赎回/调仓平行；
 * 管理卡按场景去重（见 ManageLinksCard），draft/closed 入口不丢失。
 */
export default function PortfolioActionButtons({
  portfolioCode,
  status,
  basePath,
  variant = "desktop",
  onActivateClick,
  isActivatePending,
}: PortfolioActionButtonsProps) {
  const cls = variant === "mobile" ? "w-full" : "";
  const container =
    variant === "mobile" ? "grid grid-cols-2 gap-2" : "ml-auto flex flex-wrap gap-2";

  if (status === "draft") {
    return (
      <div className={container}>
        <Link
          href={`${basePath}/${portfolioCode}/subscriptions`}
          className={variant === "mobile" ? "col-span-2" : ""}
        >
          <Button className={cls}>
            <Plus className="mr-2 h-4 w-4" />
            {variant === "mobile" ? "首次申购" : "首次申购激活"}
          </Button>
        </Link>
      </div>
    );
  }

  if (status === "active") {
    return (
      <div className={container}>
        <Link href={`${basePath}/${portfolioCode}/subscriptions`}>
          <Button className={cls}>
            <Plus className="mr-2 h-4 w-4" />
            申购赎回
          </Button>
        </Link>
        <Link href={`${basePath}/${portfolioCode}/trades`}>
          <Button variant="outline" className={cls}>
            <ArrowRightLeft className="mr-2 h-4 w-4" />
            {variant === "mobile" ? "调仓" : "调仓交易"}
          </Button>
        </Link>
        {/* #786：事件/快照上移；可见文案「事件」，accessible name 保留全称
            （WCAG 2.5.3 label-in-name，且现有 E2E 按「份额变动事件」定位不失效） */}
        <Link
          href={`${basePath}/${portfolioCode}/share-change-events`}
          aria-label="份额变动事件"
          title="份额变动事件"
        >
          <Button variant="outline" className={cls}>
            <CalendarClock className="mr-2 h-4 w-4" />
            事件
          </Button>
        </Link>
        <Link href={`${basePath}/${portfolioCode}/snapshots`}>
          <Button variant="outline" className={cls}>
            <Camera className="mr-2 h-4 w-4" />
            快照管理
          </Button>
        </Link>
      </div>
    );
  }

  // closed
  return (
    <div className={container}>
      <Button
        variant="outline"
        onClick={onActivateClick}
        disabled={isActivatePending}
        className={variant === "mobile" ? "col-span-2 w-full" : ""}
      >
        <Power className="mr-2 h-4 w-4 text-success" />
        重新激活
      </Button>
    </div>
  );
}
