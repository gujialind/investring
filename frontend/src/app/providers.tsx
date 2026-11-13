"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { ThemeProvider } from "next-themes";
import ToastContainer from "@/components/ui/ToastContainer";
import ThemeColorMeta from "@/components/shared/ThemeColorMeta";

export default function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30 * 1000,
            refetchOnWindowFocus: false,
            retry: 1,
          },
        },
      })
  );

  return (
    // #872 PR2（D1/D2）：class 通道挂 .dark；默认跟随系统；storageKey=theme；
    // 切换瞬间禁用过渡防半暗中间帧（visual-spec §14 克制动效）。
    // 内联脚本首绘前设 html.class，与 MainLayout #68 水合门解耦（SPEC §3.2/§3.3）。
    <ThemeProvider
      attribute="class"
      defaultTheme="system"
      storageKey="theme"
      disableTransitionOnChange
    >
      <QueryClientProvider client={queryClient}>
        {children}
        <ToastContainer />
      </QueryClientProvider>
      <ThemeColorMeta />
    </ThemeProvider>
  );
}
