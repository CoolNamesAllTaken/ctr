"use server";

import { refresh } from "next/cache";
import { ApiError, apiFetch } from "@/lib/api";
import { type ActionState, errorState } from "../action-state";
import type { TrainRun } from "../types";

// TODO: check the employee's session here once employee login exists.
export async function setDelay(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
  const minutes = Number(formData.get("delay_minutes"));
  // Blank means from the train's first stop, and no alert.
  const fromStationId = formData.get("from_station_id");
  const alertId = formData.get("alert_id");

  try {
    const run = await apiFetch<TrainRun>("/api/delays/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        train_id: Number(formData.get("train_id")),
        delay_minutes: minutes,
        from_station_id: fromStationId ? Number(fromStationId) : null,
        alert_id: alertId ? Number(alertId) : null,
      }),
    });
    refresh();
    const from = fromStationId
      ? run.stops.find((stop) => stop.station_id === Number(fromStationId))
      : run.stops[0];
    const where = from ? ` from ${from.station_name}` : "";
    return {
      status: "success",
      message:
        minutes === 0
          ? `Train ${run.train_name} is back on time${where}.`
          : `Train ${run.train_name} is now running ${minutes} min late${where}.`,
    };
  } catch (error) {
    // The train, stop or alert lists may be stale, so reload them with the
    // error.
    if (error instanceof ApiError) refresh();
    return errorState(error, "Couldn't update the delay. Try again.");
  }
}
