import { request } from "./client";
import type {
  DividendConfig,
  DividendConfigCreate,
  DividendConfigKey,
  DividendConfigListResponse,
  DividendConfigResolveParams,
} from "@/types/dividend-config";

export const dividendConfigApi = {
  list: (params: DividendConfigKey & { page?: number; page_size?: number }) =>
    request<DividendConfigListResponse>({ method: "GET", url: "/dividend-configs", params }),

  /** 按权益登记日解析生效行（A12）：不滤 source，无命中返回 null。 */
  resolve: (params: DividendConfigResolveParams) =>
    request<DividendConfig | null>({ method: "GET", url: "/dividend-configs/resolve", params }),

  create: (data: DividendConfigCreate) =>
    request<DividendConfig>({ method: "POST", url: "/dividend-configs", data }),

  remove: (id: number) =>
    request<{ message: string }>({ method: "DELETE", url: `/dividend-configs/${id}` }),
};