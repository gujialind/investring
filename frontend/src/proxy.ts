import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

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