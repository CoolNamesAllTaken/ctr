"use server";

import { refresh } from "next/cache";
import { ApiError, apiFetch } from "@/lib/api";
import { requireEmployee } from "@/lib/employee";
import { type ActionState, errorState } from "../action-state";
import type { TrainStatus } from "../types";

function describe(status: TrainStatus) {
  const train = `Train ${status.train_name}`;
  switch (status.state) {
    case "stopped":
      return `${train} is stopped at ${status.station_name}.`;
    case "moving": {
      const left = status.stops.findIndex(
        (stop) => stop.station_id === status.station_id,
      );
      const next = status.stops[left + 1];
      return `${train} has left ${status.station_name}${next ? ` for ${next.station_name}` : ""}.`;
    }
    case "out_of_service":
      return `${train} is out of service and off the live map.`;
    case null:
      return `${train} is following the timetable again.`;
  }
}

export async function setTrainStatus(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
  await requireEmployee();
  const trainId = Number(formData.get("train_id"));
  // "timetable" clears the status, putting the train back on the timetable.
  const state = formData.get("state");
  const stationId = formData.get("station_id");

  try {
    const status =
      state === "timetable"
        ? await apiFetch<TrainStatus>(`/api/train-statuses/${trainId}/`, {
            method: "DELETE",
          })
        : await apiFetch<TrainStatus>("/api/train-statuses/", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              train_id: trainId,
              state,
              station_id: stationId ? Number(stationId) : null,
            }),
          });
    refresh();
    return { status: "success", message: describe(status) };
  } catch (error) {
    // The train or its stops may be stale, so reload them with the error.
    if (error instanceof ApiError) refresh();
    return errorState(error, "Couldn't update the train. Try again.");
  }
}
