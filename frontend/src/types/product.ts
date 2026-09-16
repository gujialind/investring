export interface Product {
  code: string;
  market?: string;
  name: string;
  product_type: string;
  // 五维度标签（issue #128）：股票必填 region/style/size；债券必填 region+segment；
  // 商品/现金 region/style/size 为 NULL；IN_TRANSIT 虚拟产品全 NULL
  asset_class_code?: string | null;
  region_code?: string | null;
  style_code?: string | null;
  size_code?: string | null;
  segment_code?: string | null;
  confirm_days: number;
  /** issue #228：快照估值取价滞后交易日数（0=当日、N=前第 N 个交易日；场外 QDII/互认基金=1） */
  nav_lag_days: number;
  /** 展示标签（issue #228 起不参与快照取价判断，取价看 nav_lag_days） */
  is_qdii: boolean;
  data_source?: string;
  /** 列可空：迁移 0006 裸 SQL 种入的 IN_TRANSIT 行恒为 null（#487 评审订正） */
  data_source_status: string | null;
  last_sync_at?: string;
  created_at?: string;
  updated_at?: string;
}

export interface ProductCreate {
  code: string;
  market?: string;
  name: string;
  product_type: string;
  asset_class_code?: string;
  region_code?: string;
  style_code?: string;
  size_code?: string;
  segment_code?: string;
  /** #231/#236/#241：显式传入优先（后端校验）；不传由后端按市场+QDII 推导 */
  confirm_days?: number;
  /** issue #228：快照估值取价滞后交易日数（默认 0） */
  nav_lag_days?: number;
  is_qdii?: boolean;
  /** 数据源（后端默认 tushare） */
  data_source?: string;
  /** issue #90：创建后立即回填历史净值（同步失败不阻断创建） */
  sync_history?: boolean;
}

export interface ProductUpdate {
  name?: string;
  // issue #232：产品类型可纠错（后端枚举校验，非法值 422）
  product_type?: string;
  // 维度字段允许显式 null（清空）：后端 exclude_unset 语义下 null 会进入合并校验
  asset_class_code?: string | null;
  region_code?: string | null;
  style_code?: string | null;
  size_code?: string | null;
  segment_code?: string | null;
  confirm_days?: number;
  /** issue #228：纯显式更新（不传不改） */
  nav_lag_days?: number;
  is_qdii?: boolean;
}
