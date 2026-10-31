"use client";

import { startTransition, useActionState, useState } from "react";
import type { ActionState } from "../action-state";
import { formatTime } from "../format";
import type { Alert, TrainRun } from "../types";
import { ActionMessage, inputClass, primaryButtonClass } from "../ui";
import { setDelay } from "./actions";

export function DelayForm({
  runs,
  alerts,
}: {
  runs: TrainRun[];
  alerts: Alert[];
}) {
  const [trainId, setTrainId] = useState(runs[0].train_id);
  const [state, formAction, pending] = useActionState<ActionState, FormData>(
    setDelay,
    { status: "idle" },
  );

  // Fall back to the first train if the selected one has been deleted.
  const run = runs.find((r) => r.train_id === trainId) ?? runs[0];
  const trainAlerts = alerts.filter(
    (alert) => alert.train_id === null || alert.train_id === run.train_id,
  );

  return (
    <form
      // Submit via onSubmit rather than action={formAction}, so React doesn't
      // reset the form and the employee can keep adjusting the same train.
      onSubmit={(e) => {
        e.preventDefault();
        const formData = new FormData(e.currentTarget);
        startTransition(() => formAction(formData));
      }}
      className="flex flex-col gap-5"
    >
      <div className="grid gap-4 sm:grid-cols-[1fr_1fr_auto]">
        <label className="flex flex-col gap-1 text-sm font-medium">
          Train
          <select
            name="train_id"
            value={run.train_id}
            onChange={(e) => setTrainId(Number(e.target.value))}
            className={inputClass}
          >
            {runs.map((r) => (
              <option key={r.train_id} value={r.train_id}>
                {r.train_name}
                {r.stops[0] &&
                  ` · ${r.stops[0].station_name} ${formatTime(r.stops[0].scheduled_at)}`}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm font-medium">
          From station
          {/* Keyed by train so it resets to the first stop on a new train. */}
          <select
            key={run.train_id}
            name="from_station_id"
            className={inputClass}
          >
            {run.stops.map((stop) => (
              <option key={stop.station_id} value={stop.station_id}>
                {stop.station_name} · {formatTime(stop.scheduled_at)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm font-medium">
          Delay (min)
          <input
            name="delay_minutes"
            type="number"
            required
            min="0"
            max="1440"
            step="1"
            defaultValue="5"
            className={`${inputClass} sm:w-28`}
          />
        </label>
      </div>
      <label className="flex flex-col gap-1 text-sm font-medium">
        Explaining alert
        <select key={run.train_id} name="alert_id" className={inputClass}>
          <option value="">No alert</option>
          {trainAlerts.map((alert) => (
            <option key={alert.id} value={alert.id}>
              {alert.train_name ?? "Whole line"} · {alert.message}
            </option>
          ))}
        </select>
      </label>

      <div className="flex items-center gap-4">
        <button type="submit" disabled={pending} className={primaryButtonClass}>
          {pending ? "Updating…" : "Set delay"}
        </button>
        <ActionMessage state={state} />
      </div>
    </form>
  );
}
