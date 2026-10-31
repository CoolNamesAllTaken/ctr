"use server";

import { refresh } from "next/cache";
import { ApiError, apiFetch } from "@/lib/api";
import { requireEmployee } from "@/lib/employee";
import { type ActionState, errorState } from "../action-state";
import type { TapResult } from "../types";
import { formatMoney } from "../ui";

export async function simulateTap(
  _prev: ActionState,
  formData: FormData,
): Promise<ActionState> {
  await requireEmployee();
  try {
    const { tap, from_station, trip_fare } = await apiFetch<TapResult>(
      "/api/taps/",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          card_uid: formData.get("card_uid"),
          kiosk_id: Number(formData.get("kiosk_id")),
        }),
      },
    );
    refresh();
    const who = tap.rider_name ?? `Card ${tap.card_uid}`;
    const balance = `Balance ${formatMoney(tap.balance_after)}.`;
    if (tap.kind === "tap_on") {
      return {
        status: "success",
        message: `${who} tapped on at ${tap.station_name}. Charged ${formatMoney(tap.card_charge)} (the maximum fare). ${balance}`,
      };
    }
    const refunded = formatMoney(String(-Number(tap.card_charge)));
    if (from_station === tap.station_name) {
      return {
        status: "success",
        message: `${who} tapped off where they tapped on at ${tap.station_name}, so the trip was cancelled and ${refunded} refunded. ${balance}`,
      };
    }
    const trip = `The trip from ${from_station} cost ${formatMoney(trip_fare ?? "0")}`;
    const refund =
      Number(tap.card_charge) < 0
        ? `, so ${refunded} was refunded.`
        : ", the maximum fare, so there's no refund.";
    return {
      status: "success",
      message: `${who} tapped off at ${tap.station_name}. ${trip}${refund} ${balance}`,
    };
  } catch (error) {
    // The card or kiosk lists may be stale, so reload them with the error.
    if (error instanceof ApiError) refresh();
    return errorState(error, "Couldn't record the tap. Try again.");
  }
}
