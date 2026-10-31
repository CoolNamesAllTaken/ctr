"use server";

import { refresh } from "next/cache";
import { apiFetch } from "@/lib/api";
import { type ActionState, errorState } from "../action-state";
import type { Rider } from "../types";
import { formatMoney } from "../ui";

// TODO: check the employee's session here once employee login exists.
export async function createRider(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
  try {
    const rider = await apiFetch<Rider>("/api/riders/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: formData.get("name"),
        costume: formData.get("costume"),
        card_uid: formData.get("card_uid"),
        balance: formData.get("balance"),
      }),
    });
    refresh();
    return {
      status: "success",
      message: `Set up card ${rider.card_uid} for ${rider.name} with ${formatMoney(rider.balance ?? "0")}.`,
    };
  } catch (error) {
    return errorState(error, "Couldn't set up the card. Try again.");
  }
}
