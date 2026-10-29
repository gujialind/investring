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
  logout: (token: string | null) =>
    request<{ message: string }>({ method: "POST", url: "/auth/logout", headers: { Authorization: `Bearer ${token}` } }),

  changePassword: (data: ChangePasswordRequest, token: string | null) =>
    request<{ message: string }>({ method: "PUT", url: "/auth/password", data, headers: { Authorization: `Bearer ${token}` } }),

  getCurrentUser: async () =>
    (await request<{ user: UserInfo }>({ method: "GET", url: "/auth/status" })).user,
};
