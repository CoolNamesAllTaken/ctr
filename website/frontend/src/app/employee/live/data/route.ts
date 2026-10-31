import type { NextRequest } from "next/server";
import { apiFetch } from "@/lib/api";
import { EMPLOYEE_COOKIE, isEmployeeToken } from "@/lib/employee";
import type { Dashboard } from "../../types";
import { dashboardPath } from "../path";

// What the Live tab polls. The browser can't call /api/dashboard/ itself,
// since that needs the employee passphrase, which stays on the server.
export async function GET(request: NextRequest) {
  // src/proxy.ts already sends the logged-out to the login page; this is in
  // case that ever changes.
  if (!isEmployeeToken(request.cookies.get(EMPLOYEE_COOKIE)?.value)) {
    return Response.json({ detail: "Log in first." }, { status: 401 });
  }
  try {
    const at = request.nextUrl.searchParams.get("at");
    return Response.json(await apiFetch<Dashboard>(dashboardPath(at)));
  } catch {
    return Response.json(
      { detail: "Couldn't reach the backend." },
      { status: 502 },
    );
  }
}
