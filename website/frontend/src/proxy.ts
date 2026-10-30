import { NextResponse, type NextRequest } from "next/server";

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

  if (hostname && CHIPPER_HOSTS.has(hostname)) {
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
}

export const config = {
  // Skip the Django proxy, Next's own assets and files with an extension
  // (public/ and icons).
  matcher: ["/((?!api(?:/|$)|_next/|.*\\.).*)"],
};
