"use client";

import { startTransition, useActionState } from "react";
import type { ActionState } from "../action-state";
import type { Kiosk, Rider } from "../types";
import {
  ActionMessage,
  formatMoney,
  inputClass,
  primaryButtonClass,
} from "../ui";
import { simulateTap } from "./actions";

export function TapForm({
  riders,
  kiosks,
}: {
  riders: Rider[];
  kiosks: Kiosk[];
}) {
  const [state, formAction, pending] = useActionState<ActionState, FormData>(
    simulateTap,
    { status: "idle" },
  );

  // Kiosks come ordered by station; group them under their station.
  const stations = Map.groupBy(kiosks, (kiosk) => kiosk.station_name);

  return (
    <form
      // Submit via onSubmit rather than action={formAction}, so React doesn't
      // reset the form and the same card can tap on and then off.
      onSubmit={(e) => {
        e.preventDefault();
        const formData = new FormData(e.currentTarget);
        startTransition(() => formAction(formData));
      }}
      className="flex flex-col gap-5"
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-sm font-medium">
          Card
          <select name="card_uid" required className={inputClass}>
            {riders.map((rider) => (
              <option key={rider.id} value={rider.card_uid ?? ""}>
                {rider.name} · {rider.card_uid} ·{" "}
                {formatMoney(rider.balance ?? "0")}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm font-medium">
          Kiosk
          <select name="kiosk_id" required className={inputClass}>
            {[...stations].map(([station, stationKiosks]) => (
              <optgroup key={station} label={station}>
                {stationKiosks.map((kiosk) => (
                  <option key={kiosk.id} value={kiosk.id}>
                    {station} · {kiosk.name}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </label>
      </div>

      <div className="flex items-center gap-4">
        <button type="submit" disabled={pending} className={primaryButtonClass}>
          {pending ? "Tapping…" : "Tap card"}
        </button>
        <ActionMessage state={state} />
      </div>
    </form>
  );
}
