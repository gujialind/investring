"use client";

import { useCallback, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/authStore";
import { authApi, getErrorMessage } from "@/lib/api";
import { LoginRequest, ChangePasswordRequest } from "@/types/auth";
import { useUIStore } from "@/stores/uiStore";

// 登录 Hook
export function useLogin() {
  const router = useRouter();
  const login = useAuthStore((state) => state.login);
  const addToast = useUIStore((state) => state.addToast);

  return useMutation({
    mutationFn: (data: LoginRequest) => authApi.login(data),
    onSuccess: (data) => {
      login(data.token, data.user, data.expires_at);
      addToast({
        type: "success",
        title: "登录成功",
        message: `欢迎回来，${data.user.name}`,
      });
      router.push("/dashboard");
    },
    onError: (error: unknown) => {
      addToast({
        type: "error",
        title: "登录失败",
        message: getErrorMessage(error, "用户名或密码错误"),
      });
    },
  });
}

// 登出时立即清本地状态，阻止在途续期恢复凭证；服务端撤销使用捕获的 token。
export function useLogout() {
  const router = useRouter();
  const logout = useAuthStore((state) => state.logout);
  const queryClient = useQueryClient();
  const addToast = useUIStore((state) => state.addToast);

  return useCallback(async () => {
    const token = localStorage.getItem("token");
    logout();
    queryClient.clear();
    addToast({
      type: "info",
      title: "已退出登录",
    });
    router.push(window.location.pathname.startsWith("/m/") ? "/m/login" : "/login");
    try {
      await authApi.logout(token);
    } catch {
      // Local logout remains effective when the server cannot be reached.
    }
  }, [logout, queryClient, router, addToast]);
}

// 获取当前用户 Hook
export function useCurrentUser() {
  const token = useAuthStore((state) => state.token);
  const setUser = useAuthStore((state) => state.setUser);

  return useQuery({
    queryKey: ["auth", "me"],
    queryFn: async () => {
      const user = await authApi.getCurrentUser();
      setUser(user);
      return user;
    },
    enabled: !!token,
    staleTime: 5 * 60 * 1000, // 5 分钟
  });
}

// 修改密码 Hook
export function useChangePassword() {
  const addToast = useUIStore((state) => state.addToast);
  const queryClient = useQueryClient();
  const router = useRouter();

  return useMutation({
    mutationFn: async (data: ChangePasswordRequest) => {
      const token = localStorage.getItem("token");
      await authApi.changePassword(data, token);
      return token;
    },
    onSuccess: (token, data) => {
      const state = useAuthStore.getState();
      if (localStorage.getItem("token") === token
          && (!data.target_code || data.target_code === state.user?.code)) {
        state.logout();
        queryClient.clear();
        router.push(window.location.pathname.startsWith("/m/") ? "/m/login" : "/login");
      }
      addToast({
        type: "success",
        title: "密码修改成功",
        message: "请使用新密码重新登录",
      });
    },
    onError: (error: unknown) => {
      addToast({
        type: "error",
        title: "密码修改失败",
        message: getErrorMessage(error, "请检查原密码是否正确"),
      });
    },
  });
}

// 认证守卫 Hook
export function useAuthGuard(requireAuth: boolean = true) {
  const router = useRouter();
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  const isLoading = useAuthStore((state) => state.isLoading);

  useEffect(() => {
    if (isLoading) return;

    if (requireAuth && !isAuthenticated) {
      router.replace("/login");
    }

    if (!requireAuth && isAuthenticated) {
      router.replace("/dashboard");
    }
  }, [isAuthenticated, isLoading, requireAuth, router]);

  return { isAuthenticated, isLoading };
}

// 角色权限检查 Hook
export function useRoleCheck() {
  const user = useAuthStore((state) => state.user);

  const isAdmin = user?.role === "admin";
  const isViewer = user?.role === "viewer";

  const checkRole = useCallback(
    (allowedRoles: string[]) => {
      if (!user) return false;
      return allowedRoles.includes(user.role);
    },
    [user]
  );

  return { isAdmin, isViewer, role: user?.role, checkRole };
}
