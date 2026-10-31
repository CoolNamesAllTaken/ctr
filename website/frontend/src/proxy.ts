import { NextResponse, type NextRequest } from "next/server";
import { EMPLOYEE_COOKIE, isEmployeeToken } from "@/lib/employee";

// Hosts that serve the Chipper Card site from app/chipper. chipper.localhost
// is for development; browsers resolve it like localhost.
const CHIPPER_HOSTS = new Set([
  "chippercard.com",
  "www.chippercard.com",
  "chipper.localhost",
]);

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const url = request.nextUrl.clone();
  // nextUrl.hostname is the server's own hostname, not the one requested.
  const hostname = request.headers.get("host")?.replace(/:\d+$/, "");
  const isApi = pathname === "/api" || pathname.startsWith("/api/");

  if (hostname && CHIPPER_HOSTS.has(hostname)) {
    // The Chipper site never calls Django, so don't proxy it there.
    if (isApi) {
      return new NextResponse(null, { status: 404 });
    }

    // Every page on chippercard.com lives under /chipper, so the Caltreat and
    // employee pages aren't reachable from it.
    url.pathname = pathname === "/" ? "/chipper" : `/chipper${pathname}`;
    return NextResponse.rewrite(url);
  }

  // And the Chipper pages aren't reachable from the Caltreat site.
  if (pathname === "/chipper" || pathname.startsWith("/chipper/")) {
    url.pathname = "/_not-found";
    return NextResponse.rewrite(url, { status: 404 });
  }

  // The employee portal needs the passphrase, apart from the page to enter it.
  if (
    (pathname === "/employee" || pathname.startsWith("/employee/")) &&
    pathname !== "/employee/login" &&
    !isEmployeeToken(request.cookies.get(EMPLOYEE_COOKIE)?.value)
  ) {
    url.pathname = "/employee/login";
    url.search = new URLSearchParams({ next: pathname }).toString();
    return NextResponse.redirect(url);
  }

  // Everything else, including /api on the Caltreat site, carries on to the
  // rewrites in next.config.ts.
}

export const config = {
  // Skip Next's own assets and files with an extension (public/ and icons),
  // but not /api, which has to be closed on the Chipper hosts.
  matcher: ["/((?!api(?:/|$)|_next/|.*\\.).*)", "/api/:path*"],
};
