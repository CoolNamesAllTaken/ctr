"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { EMPLOYEE_COOKIE, employeeToken } from "@/lib/employee";
import type { ActionState } from "../action-state";

export async function logIn(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const token = employeeToken(String(formData.get("passphrase") ?? ""));
  if (!token) {
    return { status: "error", message: "That's not the passphrase." };
  }

  (await cookies()).set(EMPLOYEE_COOKIE, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    maxAge: 60 * 60 * 24 * 30,
  });
  // Back to the page that sent them here, as long as it's in the portal.
  const next = String(formData.get("next") ?? "");
  redirect(next.startsWith("/employee/") ? next : "/employee");
}
