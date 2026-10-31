"use server";

import { refresh } from "next/cache";
import { apiFetch } from "@/lib/api";
import { requireEmployee } from "@/lib/employee";
import { type ActionState, errorState } from "../action-state";
import type { Rider } from "../types";
import { formatRides } from "../ui";

export async function createRider(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
  await requireEmployee();
  try {
    const rider = await apiFetch<Rider>("/api/riders/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: formData.get("name"),
        costume: formData.get("costume"),
        card_uid: formData.get("card_uid"),
      }),
    });
    refresh();
    return {
      status: "success",
      message: `Set up card ${rider.card_uid} for ${rider.name} with ${formatRides(Number(formData.get("rides")))}.`,
    };
  } catch (error) {
    return errorState(error, "Couldn't set up the card. Try again.");
  }
}

/** The name of the rider this card is set up for, or null if it's new. */
export async function cardRiderName(uid: string): Promise<string | null> {
  await requireEmployee();
  // The list is newest first and capped, so ask for all of them.
  const riders = await apiFetch<Rider[]>(
    `/api/riders/?limit=${Number.MAX_SAFE_INTEGER}`,
  );
  return riders.find((rider) => rider.card_uid === uid)?.name ?? null;
}
