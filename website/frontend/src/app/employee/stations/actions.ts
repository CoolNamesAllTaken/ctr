"use server";

import { refresh } from "next/cache";
import { ApiError, apiFetch } from "@/lib/api";
import { requireEmployee } from "@/lib/employee";
import { type ActionState, errorState } from "../action-state";
import type { Kiosk, Station } from "../types";

const JSON_HEADERS = { "Content-Type": "application/json" };

function failed(error: unknown, fallback: string): ActionState {
  // The lists may be stale (e.g. another employee deleted a station), so
  // reload them alongside the error.
  if (error instanceof ApiError) refresh();
  return errorState(error, fallback);
}

export async function createStation(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
  await requireEmployee();
  try {
    const station = await apiFetch<Station>("/api/stations/", {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({
        name: formData.get("name"),
        zone: Number(formData.get("zone")),
        position: Number(formData.get("position")),
      }),
    });
    refresh();
    return { status: "success", message: `${station.name} added.` };
  } catch (error) {
    return failed(error, "Couldn't add the station. Try again.");
  }
}

/** Saves, moves or deletes a station, depending on the button clicked. */
export async function updateStation(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
  await requireEmployee();
  const path = `/api/stations/${formData.get("id")}/`;
  const intent = formData.get("intent");
  const position = Number(formData.get("position"));

  try {
    if (intent === "delete") {
      await apiFetch(path, { method: "DELETE" });
      refresh();
      return { status: "success", message: "Station deleted." };
    }
    const moves: Record<string, number> = {
      south: position - 1,
      north: position + 1,
    };
    const station = await apiFetch<Station>(path, {
      method: "PATCH",
      headers: JSON_HEADERS,
      body: JSON.stringify(
        typeof intent === "string" && intent in moves
          ? { position: moves[intent] }
          : { name: formData.get("name"), zone: Number(formData.get("zone")) },
      ),
    });
    refresh();
    return {
      status: "success",
      message:
        intent === "save" ? "Saved." : `Moved ${station.name} ${intent}.`,
    };
  } catch (error) {
    return failed(error, "Couldn't update the station. Try again.");
  }
}

/** Saves or deletes a kiosk, depending on the button clicked. */
export async function updateKiosk(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
  await requireEmployee();
  const path = `/api/kiosks/${encodeURIComponent(String(formData.get("id")))}/`;

  try {
    if (formData.get("intent") === "delete") {
      await apiFetch(path, { method: "DELETE" });
      refresh();
      return { status: "success", message: "Kiosk deleted." };
    }
    const stationId = formData.get("station_id");
    const kiosk = await apiFetch<Kiosk>(path, {
      method: "PUT",
      headers: JSON_HEADERS,
      body: JSON.stringify({
        name: formData.get("name"),
        station_id: stationId ? Number(stationId) : null,
      }),
    });
    refresh();
    return {
      status: "success",
      message: kiosk.station_name
        ? `Saved. Taps here now charge fares from ${kiosk.station_name}.`
        : "Saved. This kiosk is out of service until it has a station.",
    };
  } catch (error) {
    return failed(error, "Couldn't update the kiosk. Try again.");
  }
}
