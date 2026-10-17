"use server";

import { refresh } from "next/cache";
import { ApiError, apiFetch } from "@/lib/api";
import { type ActionState, errorState } from "../action-state";
import type { Alert } from "../types";

// TODO: check the employee's session here once employee login exists.
export async function issueAlert(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
  // A blank train means the alert is about the whole line.
  const trainId = formData.get("train_id");

  try {
    const alert = await apiFetch<Alert>("/api/alerts/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: formData.get("message"),
        train_id: trainId ? Number(trainId) : null,
      }),
    });
    refresh();
    return {
      status: "success",
      message: alert.train_name
        ? `Alert issued for ${alert.train_name}.`
        : "Alert issued for the whole line.",
    };
  } catch (error) {
    // The train list may be stale, so reload it with the error.
    if (error instanceof ApiError) refresh();
    return errorState(error, "Couldn't issue the alert. Try again.");
  }
}
