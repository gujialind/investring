import { create } from "zustand";
import { persist } from "zustand/middleware";
import { LoginResponse, UserInfo } from "@/types/auth";

// cookie 属性写删共用：删除缺 Secure 在当前浏览器仍生效，但属性不对称
// 在改名 __Host- 前缀（强制 Secure）时会踩坑。
function cookieAttributes(): string {
  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  return `path=/; SameSite=Lax${secure}`;
}

function persistCredential(token: string, expiresAt: string) {
  if (typeof window === "undefined") return;
  localStorage.setItem("token", token);
  localStorage.setItem("token-expires-at", expiresAt);
  document.cookie = `token=${encodeURIComponent(token)}; expires=${new Date(expiresAt).toUTCString()}; ${cookieAttributes()}`;
}

interface AuthState {
  token: string | null;
  expiresAt: string | null;
  user: UserInfo | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (token: string, user: UserInfo, expiresAt: string) => void;
  renew: (expectedToken: string, data: LoginResponse) => boolean;
  logout: () => void;
  setUser: (user: UserInfo) => void;
  setLoading: (loading: boolean) => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      expiresAt: null,
      user: null,
      isAuthenticated: false,
      isLoading: false,

      login: (token, user, expiresAt) => {
        persistCredential(token, expiresAt);
        set({ token, user, expiresAt, isAuthenticated: true, isLoading: false });
      },

      renew: (expectedToken, data) => {
        // A delayed renewal must never restore a logged-out or replaced account.
        if (typeof window === "undefined" || localStorage.getItem("token") !== expectedToken) return false;
        persistCredential(data.token, data.expires_at);
        set({ token: data.token, user: data.user, expiresAt: data.expires_at });
        return true;
      },

      logout: () => {
        if (typeof window !== "undefined") {
          localStorage.removeItem("token");
          localStorage.removeItem("token-expires-at");
          document.cookie = `token=; Max-Age=0; ${cookieAttributes()}`;
        }
        set({ token: null, expiresAt: null, user: null, isAuthenticated: false, isLoading: false });
      },

      setUser: (user) => set({ user }),
      setLoading: (isLoading) => set({ isLoading }),
    }),
    {
      name: "auth-storage",
      partialize: (state) => ({
        token: state.token,
        expiresAt: state.expiresAt,
        user: state.user,
        isAuthenticated: state.isAuthenticated,
      }),
    }
  )
);
