/** The backend's dashboard, optionally at another time today (an ISO
 * datetime, for a demo or to look back). */
export function dashboardPath(at: string | null | undefined) {
  return at
    ? `/api/dashboard/?${new URLSearchParams({ at })}`
    : "/api/dashboard/";
}
