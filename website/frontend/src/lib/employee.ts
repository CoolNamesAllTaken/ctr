import "server-only";

import { createHash, timingSafeEqual } from "node:crypto";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

// The employee portal is behind one shared passphrase. Logging in sets this
// cookie to a hash of it, so changing the passphrase logs everyone out.
export const EMPLOYEE_COOKIE = "employee";

function tokenFor(passphrase: string) {
  return createHash("sha256")
    .update(`ctr employee ${passphrase}`)
    .digest("hex");
}

function sameToken(a: string, b: string) {
  const [x, y] = [Buffer.from(a), Buffer.from(b)];
  return x.length === y.length && timingSafeEqual(x, y);
}

/** The cookie value for the right passphrase, or null for a wrong one. */
export function employeeToken(passphrase: string): string | null {
  const expected = process.env.EMPLOYEE_PASSPHRASE;
  if (!expected) return null;
  const token = tokenFor(passphrase);
  return sameToken(token, tokenFor(expected)) ? token : null;
}

export function isEmployeeToken(token: string | undefined): boolean {
  const expected = process.env.EMPLOYEE_PASSPHRASE;
  return Boolean(expected && token && sameToken(token, tokenFor(expected)));
}

/**
 * Send anyone who isn't logged in to the login page. src/proxy.ts already does
 * this for the portal's pages; call it at the top of every employee Server
 * Function too, since those can be called from any page.
 */
export async function requireEmployee() {
  if (!isEmployeeToken((await cookies()).get(EMPLOYEE_COOKIE)?.value)) {
    redirect("/employee/login");
  }
}
