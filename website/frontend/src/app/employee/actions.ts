"use server";

import { refresh } from "next/cache";
import { ApiError, apiFetch } from "@/lib/api";
import { requireEmployee } from "@/lib/employee";
import { type ActionState, errorState } from "./action-state";
import type { Train } from "./types";

export async function createTrain(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
  await requireEmployee();
  // Each station has a "stop-<id>" time input; blank means the train skips it.
  const arrivals = [...formData.entries()]
    .filter(([key, value]) => key.startsWith("stop-") && value !== "")
    .map(([key, value]) => ({
      station_id: Number(key.slice("stop-".length)),
      time: String(value),
    }));

  try {
    const train = await apiFetch<Train>("/api/trains/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: formData.get("name"),
        direction: formData.get("direction"),
        traincar_id: Number(formData.get("traincar_id")),
        arrivals,
      }),
    });
    refresh();
    return {
      status: "success",
      message: `Train ${train.name} added with ${train.arrivals.length} stops.`,
    };
  } catch (error) {
    // The form may be stale (e.g. a station was deleted since it loaded), so
    // reload the station and car lists alongside the error.
    if (error instanceof ApiError) refresh();
    return errorState(error, "Couldn't add the train. Try again.");
  }
}

export async function loadStarterData(): Promise<ActionState> {
  await requireEmployee();
  try {
    const created = await apiFetch<Record<string, number>>(
      "/api/starter-data/",
      { method: "POST" },
    );
    refresh();
    const added = Object.entries(created)
      .filter(([, count]) => count > 0)
      .map(
        ([name, count]) =>
          `${count} ${name === "traincars" ? "train cars" : name}`,
      );
    return {
      status: "success",
      message: added.length
        ? `Added ${added.join(", ")}.`
        : "Starter data was already loaded.",
    };
  } catch (error) {
    return errorState(error, "Couldn't load starter data.");
  }
}
