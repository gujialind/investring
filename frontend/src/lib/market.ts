import { formatMarketName } from "@/lib/utils";

/**
 * 市场选项单一来源（#324 抽取共享；#502 起产品表单下拉也直接消费）：产品筛选弹窗 /
 * 产品管理页 / 提交交易产品选择器 / 产品表单下拉共用，label 由 formatMarketName 派生。
 */
export const MARKET_OPTIONS = ["CN_EXCHANGE", "CN_OTC", "HK_MUTUAL"].map((v) => ({
  value: v,
  label: formatMarketName(v),
}));
