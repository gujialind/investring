import axios, { AxiosError, AxiosInstance, AxiosRequestConfig, AxiosResponse } from "axios";
import { ApiError, ApiValidationError } from "@/types/common";
import { useAuthStore } from "@/stores/authStore";
import { LoginResponse } from "@/types/auth";

/**
 * 检测运行时基础路径（用于 ModelScope Studio 等子路径部署场景）。
 * 魔搭 Studio 将应用挂载在 /studios/{owner}/{repo}/ 下，
 * 平台反向代理会剥离前缀后转发给容器，但浏览器端发起的 API 请求
 * 必须携带完整前缀才能经代理抵达本应用的 Next.js 服务器。
 */
export function getBasePath(): string {
  if (typeof window === "undefined") return "";
  const match = window.location.pathname.match(/^(\/studios\/[^/]+\/[^/]+)/);
  return match ? match[1] : "";
}

/**
 * 登录页路由：401 拦截器与 useLogout / useChangePassword 共用，统一移动端判定，
 * 避免三处复制漂移（子路径部署先剥离 basePath 再判定 /m 前缀）。
 */
export function loginRedirectPath(): string {
  const base = getBasePath();
  const { pathname } = window.location;
  const path = base && pathname.startsWith(base) ? pathname.slice(base.length) : pathname;
  return path === "/m" || path.startsWith("/m/") ? "/m/login" : "/login";
}

// 创建 axios 实例
const api: AxiosInstance = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL || `${getBasePath()}/api`,
  headers: {
    "Content-Type": "application/json",
  },
  timeout: 30000,
});

let renewal: { token: string; promise: Promise<string> } | null = null;

/**
 * 续期窗口：剩余有效期不足 24h 才续期。与后端 TOKEN_EXPIRE_DAYS（默认 7 天，可配）耦合——
 * 若运维把凭证寿命配到 ≤24h，此条件在续期成功后立即不再满足，每个业务请求都会多打一次
 * POST /auth/renew（流量与 DB 写入翻倍）；调整凭证寿命时须同步评估该窗口。
 */
const RENEW_WINDOW_MS = 86_400_000;

async function activeToken(token: string): Promise<string> {
  const expiresAt = localStorage.getItem("token-expires-at");
  if (expiresAt && Date.parse(expiresAt) - Date.now() > RENEW_WINDOW_MS) return token;
  if (renewal?.token === token) return renewal.promise;
  const ownerCode = useAuthStore.getState().user?.code;
  const promise = (async () => {
    try {
      const response = await api.post<LoginResponse>("/auth/renew", undefined, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = response.data;
      if (!data || typeof data.token !== "string" || !data.token
          || typeof data.expires_at !== "string" || !(Date.parse(data.expires_at) > Date.now())
          || !data.user || typeof data.user.code !== "string" || data.user.code !== ownerCode) {
        throw new Error("续期响应无效，原凭证未替换");
      }
      if (!useAuthStore.getState().renew(token, data)) {
        throw new Error("登录状态已改变，请重新操作");
      }
      return response.data.token;
    } catch (error) {
      // A transient renewal failure can use the still-valid token, never replay a write.
      if (axios.isAxiosError(error) && (!error.response || error.response.status >= 500)
          && expiresAt && Date.parse(expiresAt) > Date.now()
          && localStorage.getItem("token") === token) return token;
      throw error;
    }
  })();
  renewal = { token, promise };
  try {
    return await promise;
  } finally {
    if (renewal?.promise === promise) renewal = null;
  }
}

// Renew before sending the business request; a financial write is sent at most once.
api.interceptors.request.use(
  async (config) => {
    if (typeof window !== "undefined" && !config.headers.Authorization && config.url !== "/auth/login") {
      const token = localStorage.getItem("token");
      if (token) {
        // 防御性冗余：这三个 URL 目前只由显式携带 Authorization 的调用点发出，
        // 上方 !config.headers.Authorization 已短路，本行实际不会命中；
        // 保留以防未来出现无头调用点触发递归续期。
        const skipRenewal = ["/auth/renew", "/auth/logout", "/auth/password"].includes(config.url || "");
        const current = skipRenewal ? token : await activeToken(token);
        if (localStorage.getItem("token") !== current) throw new Error("登录状态已改变，请重新操作");
        config.headers.Authorization = `Bearer ${current}`;
      }
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// 响应拦截器：统一错误处理 + 401 跳转
api.interceptors.response.use(
  (response: AxiosResponse) => response,
  (error: AxiosError<ApiError>) => {
    if (typeof window !== "undefined") {
      const sentToken = error.config?.headers?.Authorization;
      const currentToken = localStorage.getItem("token");
      // 会话僵死：本地凭证已消失（另 tab 登出、清缓存）但 zustand 仍认为已登录，
      // 请求根本没带 Authorization，其 401 也必须清理，否则回到本处理器注释
      // 声称要防的"页面认为已登录但所有请求 401"僵死态。
      // 未登录场景（如登录页密码错误的 401）不清理也不跳转，交给页面自己展示错误。
      const sessionDead = !currentToken && !sentToken
        && useAuthStore.getState().isAuthenticated;
      if (error.response?.status === 401
          && (sessionDead
              || (sentToken && currentToken && sentToken === `Bearer ${currentToken}`))) {
        // 统一走 authStore.logout()：同时清理 localStorage / cookie / zustand，
        // 避免三处存储状态不一致导致"页面认为已登录但所有请求 401"的僵死态。
        // 携带过期 sentToken 的迟到回执（续期/重新登录之后才 401）仍被豁免，不打断新登录。
        useAuthStore.getState().logout();
        window.location.href = `${getBasePath()}${loginRedirectPath()}`;
      }
    }
    return Promise.reject(error);
  }
);

// 统一错误处理封装
export class ApiException extends Error {
  public code: string;
  public status: number;
  /** 后端 BusinessError 附带的结构化上下文（如 MARKET_AMBIGUOUS 的 available_markets） */
  public details?: Record<string, unknown>;

  constructor(code: string, message: string, status: number, details?: Record<string, unknown>) {
    super(message);
    this.code = code;
    this.status = status;
    this.details = details;
    this.name = "ApiException";
  }
}

/**
 * FastAPI RequestValidationError 的 detail 是数组：[{loc, msg, type}, ...]（#643）。
 * 拼成字段级可读文案（loc 末段作字段名）。
 */
function formatValidationDetail(detail: ApiValidationError[]): string {
  const parts = detail
    .map((entry) => {
      // 载荷来自网络（系统边界），字段可能缺失或形态漂移：退化到能用的部分，不抛新错
      const loc = Array.isArray(entry?.loc) ? entry.loc : [];
      const field = loc.length > 0 ? String(loc[loc.length - 1]) : "";
      const msg = typeof entry?.msg === "string" ? entry.msg : "";
      if (field && msg) return `${field}: ${msg}`;
      return msg || field;
    })
    .filter(Boolean);
  return parts.join("; ");
}

/**
 * 从响应 detail 提取用户可读消息；三种形态（裸字符串 / 校验数组 / 结构化对象）
 * 统一在此解析，无法解析返回 null（#643）。
 */
function detailMessage(detail: unknown): string | null {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) return formatValidationDetail(detail) || null;
  if (detail && typeof detail === "object") {
    const msg = (detail as { message?: unknown }).message;
    if (typeof msg === "string" && msg) return msg;
  }
  return null;
}

export function handleApiError(error: unknown): ApiException {
  if (axios.isAxiosError(error)) {
    const axiosError = error as AxiosError<ApiError>;
    const status = axiosError.response?.status || 500;
    const detail = axiosError.response?.data?.detail;
    // 后端存在三种 detail 形态：结构化 {error, message, details}、裸字符串
    // （HTTPException(detail="...")）与校验失败数组 [{loc, msg, type}, ...]（#643）
    if (Array.isArray(detail)) {
      // 能拿到 detail 数组即说明有响应，无需再回 axios 原文（#655 L2 S2）
      const message = formatValidationDetail(detail) || "请求失败";
      return new ApiException("VALIDATION_ERROR", message, status);
    }
    const code =
      (detail && typeof detail === "object" && detail.error) || "UNKNOWN_ERROR";
    // 有响应但 detail 解析不出（对象无 message 等异常形态）→ 本地化兜底文案；
    // 仅无响应（网络/超时错误）才回 axios 原文，避免裸 HTTP 文案上桌（#655 L2 S2）。
    // 无响应一支**刻意保留英文原文**（"Network Error" / "timeout of …ms exceeded"）：
    // 断网/超时与服务端故障在界面上就是靠这句区分——「加载失败：Network Error」把方向
    // 指向客户端网络，而不是让人去翻后端日志。口径由 #664 定案、由本文件 test 的
    // 「无响应 → axios 原文」两条断言钉住；要本地化必须同批改那两条，不得新旧并存。
    const message =
      detailMessage(detail) ||
      (axiosError.response ? "请求失败" : axiosError.message || "请求失败");
    return new ApiException(
      code,
      message,
      status,
      detail && typeof detail === "object" ? detail.details : undefined
    );
  }
  if (error instanceof Error) {
    return new ApiException("UNKNOWN_ERROR", error.message, 500);
  }
  return new ApiException("UNKNOWN_ERROR", "未知错误", 500);
}

// 通用请求封装
export async function request<T>(config: AxiosRequestConfig): Promise<T> {
  try {
    const response = await api.request<T>(config);
    return response.data;
  } catch (error) {
    throw handleApiError(error);
  }
}

/**
 * 从未知错误中提取用户可读消息。
 * 覆盖 ApiException / Error / axios 错误结构，失败时返回 fallback。
 * 用于 React Query 的 onError 回调，避免使用 any。
 */
export function getErrorMessage(error: unknown, fallback = "操作失败"): string {
  // axios 错误必须先走 detail 解析：AxiosError 是 Error 子类，若先落
  // `instanceof Error` 分支会把裸 HTTP 文案（"Request failed with status
  // code 422"）当用户消息返回——正是 #643 文案裸奔的根因之一
  if (axios.isAxiosError(error)) {
    const detail = (error.response?.data as ApiError | undefined)?.detail;
    // 有响应但 detail 解析不出（空数组、对象无 message 等异常形态）→ 调用方 fallback；
    // 仅无响应（网络/超时错误）才回 axios 原文（#655 L2 S2）
    return (
      detailMessage(detail) ||
      (error.response ? fallback : error.message || fallback)
    );
  }
  if (error instanceof Error && error.message) return error.message;
  const e = error as { response?: { data?: { detail?: unknown } }; message?: string };
  return detailMessage(e?.response?.data?.detail) || e?.message || fallback;
}

export default api;
