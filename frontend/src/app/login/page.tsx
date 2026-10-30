"use client";

import { useState } from "react";
import { authApi, ApiException, getBasePath } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Loader2 } from "lucide-react";

export default function LoginPage() {
  const { login } = useAuthStore();
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const response = await authApi.login({ code, password });
      const { token, user } = response;
      // expires_at 与 token 同批校验：缺失/畸形会让 persistCredential 写入字面量
      // "undefined"，activeToken 里 Date.parse 得 NaN，导致每个请求都触发续期。
      if (!token || Number.isNaN(Date.parse(response.expires_at))) {
        setError("登录响应无效，请检查后端服务是否可达");
        return;
      }
      login(token, user, response.expires_at);
      // 使用完整路径跳转，兼容 ModelScope Studio 等子路径部署
      window.location.href = `${getBasePath()}/dashboard`;
    } catch (err) {
      if (err instanceof ApiException) {
        setError(err.message);
      } else if (err instanceof Error) {
        setError(err.message);
      } else {
        setError("登录失败");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-gradient-to-br from-blue-50 to-indigo-100 p-4">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <CardTitle className="text-2xl font-bold text-gray-900">
            InvestRing
          </CardTitle>
          <CardDescription className="text-gray-600">
            资产组合管理系统
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            {error && (
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}
            <div className="space-y-2">
              <Label htmlFor="code">用户名</Label>
              <Input
                id="code"
                type="text"
                placeholder="请输入用户名"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">密码</Label>
              <Input
                id="password"
                type="password"
                placeholder="请输入密码"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  登录中...
                </>
              ) : (
                "登录"
              )}
            </Button>
          </form>
        </CardContent>
      </Card>
      <footer className="mt-8 pb-4 text-center text-xs text-gray-500">
        <a
          href="https://beian.miit.gov.cn/"
          target="_blank"
          rel="noopener noreferrer"
          className="hover:text-gray-700"
        >
          浙ICP备2026062469号
        </a>
      </footer>
    </div>
  );
}
