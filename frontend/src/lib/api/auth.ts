import { request } from "./client";
import {
  LoginRequest,
  LoginResponse,
  ChangePasswordRequest,
  UserInfo,
} from "@/types/auth";

export const authApi = {
  login: (data: LoginRequest) =>
    request<LoginResponse>({ method: "POST", url: "/auth/login", data }),

  // 使用调用时捕获的 token，允许立即清理本地状态而不丢失服务端撤销。
  // 无凭证时不发请求："Bearer null" 会被服务端 401，且与已清空的本地 token
  // 比较相等，曾在 401 拦截器里触发二次登出 + 硬跳转打断 SPA 导航。
  logout: (token: string | null) =>
    token
      ? request<{ message: string }>({ method: "POST", url: "/auth/logout", headers: { Authorization: `Bearer ${token}` } })
      : Promise.resolve({ message: "已退出登录" }),

  // 与 logout 同款短路：无凭证不发 "Bearer null"（服务端必 401，白跑一轮）。
  changePassword: (data: ChangePasswordRequest, token: string | null) =>
    token
      ? request<{ message: string }>({ method: "PUT", url: "/auth/password", data, headers: { Authorization: `Bearer ${token}` } })
      : Promise.reject(new Error("登录状态已失效，请重新登录")),

  // 防御非 JSON 响应（反向代理 200 回 HTML 等）：user 缺失会让 setUser(undefined)
  // 使续期 owner 校验永久失败，必须在入口抛错而不是把坏数据交给 store。
  getCurrentUser: async () => {
    const data = await request<{ user?: UserInfo }>({ method: "GET", url: "/auth/status" });
    if (!data?.user?.code) throw new Error("登录状态响应无效，请重新登录");
    return data.user;
  },
};
