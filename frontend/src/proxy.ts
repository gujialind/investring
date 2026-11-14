import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

// 移动设置钻取子页 section → 桌面 /settings 的 Tab（#878 L2 Blocker 2）。桌面设置是
// 单页三 Tab，没有 /settings/[section] 同形动态路由，机械去 /m 会 404；appearance/
// datasource/system 都落在「通用」Tab，calendar/logs 各自成 Tab。未知 section 落 general。
const SETTINGS_SECTION_TAB: Record<string, string> = {
  appearance: "general",
  datasource: "general",
  system: "general",
  calendar: "calendar",
  logs: "logs",
};

export function proxy(request: NextRequest) {
  const userAgent = request.headers.get("user-agent") || "";
  const isMobile = /Mobile|Android|iPhone|iPad|iPod/i.test(userAgent);
  const pathname = request.nextUrl.pathname;
  const token = request.cookies.get("token")?.value;

  // 重定向目标保留 query：/m 重写丢参会静默破坏 URL 驱动态（#595 D-7 的 ?view= 深链）
  const redirectWithQuery = (path: string) => {
    const url = new URL(path, request.url);
    url.search = request.nextUrl.search;
    return NextResponse.redirect(url);
  };

  if (pathname.startsWith("/_next") || pathname.startsWith("/api")) {
    return NextResponse.next();
  }

  if (pathname === "/") {
    const target = isMobile ? "/m/dashboard" : "/dashboard";
    return redirectWithQuery(target);
  }

  if (isMobile && !pathname.startsWith("/m") && pathname !== "/login") {
    const mobilePath = "/m" + pathname;
    return redirectWithQuery(mobilePath);
  }

  if (!isMobile && pathname.startsWith("/m")) {
    // 设置钻取子页显式映射到 /settings?tab=（保留原 query），不落到不存在的同形路径
    const section = pathname.match(/^\/m\/settings\/([^/]+)$/)?.[1];
    if (section) {
      const url = new URL("/settings", request.url);
      url.search = request.nextUrl.search;
      url.searchParams.set("tab", SETTINGS_SECTION_TAB[section] ?? "general");
      return NextResponse.redirect(url);
    }
    const desktopPath = pathname.slice(2) || "/";
    return redirectWithQuery(desktopPath);
  }

  const isLoginPage = pathname === "/m/login" || pathname === "/login";
  if (!token && !isLoginPage) {
    const loginPath = isMobile ? "/m/login" : "/login";
    return redirectWithQuery(loginPath);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico).*)"],
};