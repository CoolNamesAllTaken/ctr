"use server";

import { refresh } from "next/cache";
import { ApiError, apiFetch } from "@/lib/api";
import { requireEmployee } from "@/lib/employee";
import { type ActionState, errorState } from "./action-state";
import type { Train } from "./types";

const JSON_HEADERS = { "Content-Type": "application/json" };

function failed(error: unknown, fallback: string): ActionState {
  // The form may be stale (e.g. a station or train was deleted since it
  // loaded), so reload the lists alongside the error.
  if (error instanceof ApiError) refresh();
  return errorState(error, fallback);
}

export async function createTrains(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
  await requireEmployee();
  try {
    const trains = await apiFetch<Train[]>("/api/trains/series/", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({
        first_number: Number(formData.get("first_number")),
        count: Number(formData.get("count")),
        direction: formData.get("direction"),
        traincar_id: Number(formData.get("traincar_id")),
        station_ids: formData.getAll("station_id").map(Number),
        first_departure: formData.get("first_departure"),
        minutes_between_trains: Number(formData.get("minutes_between_trains")),
        minutes_between_stops: Number(formData.get("minutes_between_stops")),
      }),
    });
    refresh();
    const [first] = trains;
    const stops = `${first.arrivals.length} stops`;
    return {
      status: "success",
      message:
        trains.length === 1
          ? `Train ${first.name} added with ${stops}.`
          : `Trains ${first.name}–${trains.at(-1)?.name} added with ${stops} each.`,
    };
  } catch (error) {
    return failed(error, "Couldn't add the trains. Try again.");
  }
}

export async function renumberTrain(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
  await requireEmployee();
  try {
    const train = await apiFetch<Train>(`/api/trains/${formData.get("id")}/`, {
      method: "PATCH",
      headers: JSON_HEADERS,
      body: JSON.stringify({ name: formData.get("name") }),
    });
    refresh();
    return { status: "success", message: `Now train ${train.name}.` };
  } catch (error) {
    return failed(error, "Couldn't change the train number. Try again.");
  }
}
