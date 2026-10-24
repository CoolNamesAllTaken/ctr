"use server";

import { refresh } from "next/cache";
import { ApiError, apiFetch } from "@/lib/api";
import type { Train } from "./types";

export type ActionState =
  | { status: "idle" }
  | { status: "success"; message: string }
  | { status: "error"; message: string };

// TODO: check the employee's session here once employee login exists.
export async function createTrain(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
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
    if (error instanceof ApiError && typeof error.detail === "string") {
      // The form may be stale (e.g. a station was deleted since it loaded), so
      // reload the station and car lists alongside the error.
      refresh();
      return { status: "error", message: error.detail };
    }
    console.error(error);
    return { status: "error", message: "Couldn't add the train. Try again." };
  }
}

// TODO: check the employee's session here once employee login exists.
export async function loadStarterData(): Promise<ActionState> {
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
    if (error instanceof ApiError && typeof error.detail === "string") {
      return { status: "error", message: error.detail };
    }
    console.error(error);
    return { status: "error", message: "Couldn't load starter data." };
  }
}
