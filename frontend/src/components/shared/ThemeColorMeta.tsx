"use client";

import { useEffect } from "react";
import { useTheme } from "next-themes";

/**
 * 浏览器/Android chrome 的 theme-color 随 resolvedTheme 更新（#872 PR2，visual-spec §8 B5）。
 * manifest 的 theme_color/background_color 是构建期静态值（启动屏 fallback），
 * 动态 chrome 由本组件覆盖；iOS standalone 状态栏为静态配置平台限制（R2 登记）。
 */
export default function ThemeColorMeta() {
  const { resolvedTheme } = useTheme();

  useEffect(() => {
    let meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) {
      meta = document.createElement("meta");
      meta.setAttribute("name", "theme-color");
      document.head.appendChild(meta);
    }
    meta.setAttribute("content", resolvedTheme === "dark" ? "#0F1420" : "#2B5CD7");
  }, [resolvedTheme]);

  return null;
}