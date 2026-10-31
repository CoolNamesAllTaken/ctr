"use client";

import { useActionState, useState } from "react";
import type { ActionState } from "../action-state";
import type { TrainState, TrainStatus } from "../types";
import {
  ActionMessage,
  inputClass,
  primaryButtonClass,
  secondaryButtonClass,
} from "../ui";
import { setTrainStatus } from "./actions";

const STATE_LABELS: Record<TrainState, string> = {
  stopped: "Stopped at",
  moving: "Moving on from",
  out_of_service: "Out of service",
  canceled: "Canceled",
};

type Step = {
  label: string;
  state: TrainState;
  stationId: number | null;
};

/** What the conductor most likely does next: stop at the first station,
 * depart, arrive at the next, and so on until the end of the run. */
function nextStep(run: TrainStatus): Step | null {
  const stops = run.stops;
  // Nothing to do until it's put back on the timetable.
  if (run.state === "canceled") return null;
  const at = stops.findIndex((stop) => stop.station_id === run.station_id);
  if (run.state === null || run.state === "out_of_service" || at === -1) {
    return stops[0]
      ? {
          label: `Stopped at ${stops[0].station_name}`,
          state: "stopped",
          stationId: stops[0].station_id,
        }
      : null;
  }
  if (run.state === "moving" && stops[at + 1]) {
    return {
      label: `Arrived at ${stops[at + 1].station_name}`,
      state: "stopped",
      stationId: stops[at + 1].station_id,
    };
  }
  if (at === stops.length - 1) {
    return { label: "End run", state: "out_of_service", stationId: null };
  }
  return {
    label: `Depart ${stops[at].station_name}`,
    state: "moving",
    stationId: stops[at].station_id,
  };
}

function HiddenFields({
  trainId,
  state,
  stationId,
}: {
  trainId: number;
  state: TrainState | "timetable";
  stationId?: number | null;
}) {
  return (
    <>
      <input type="hidden" name="train_id" value={trainId} />
      <input type="hidden" name="state" value={state} />
      {stationId != null && (
        <input type="hidden" name="station_id" value={stationId} />
      )}
    </>
  );
}

/** Pick any state and station, e.g. to correct a mis-tap. */
function ManualForm({
  run,
  formAction,
  pending,
}: {
  run: TrainStatus;
  formAction: (formData: FormData) => void;
  pending: boolean;
}) {
  const [state, setState] = useState<TrainState>(run.state ?? "stopped");
  const lastStop = run.stops.at(-1);

  return (
    <form action={formAction} className="flex flex-wrap items-end gap-2">
      <input type="hidden" name="train_id" value={run.train_id} />
      <label className="flex flex-col gap-1 text-sm font-medium">
        Status
        <select
          name="state"
          value={state}
          onChange={(e) => setState(e.target.value as TrainState)}
          className={inputClass}
        >
          {Object.entries(STATE_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
      {state !== "out_of_service" && state !== "canceled" && (
        <label className="flex flex-col gap-1 text-sm font-medium">
          Station
          <select
            name="station_id"
            defaultValue={run.station_id ?? run.stops[0]?.station_id}
            className={inputClass}
          >
            {run.stops.map((stop) => (
              <option
                key={stop.station_id}
                value={stop.station_id}
                // Nowhere to move on to from the last stop.
                disabled={state === "moving" && stop === lastStop}
              >
                {stop.station_name}
              </option>
            ))}
          </select>
        </label>
      )}
      <button type="submit" disabled={pending} className={secondaryButtonClass}>
        Set
      </button>
    </form>
  );
}

export function StatusControls({ run }: { run: TrainStatus }) {
  const [state, formAction, pending] = useActionState<ActionState, FormData>(
    setTrainStatus,
    { status: "idle" },
  );
  const step = nextStep(run);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        {step && (
          <form action={formAction}>
            <HiddenFields
              trainId={run.train_id}
              state={step.state}
              stationId={step.stationId}
            />
            <button
              type="submit"
              disabled={pending}
              className={`${primaryButtonClass} min-w-48 py-3 text-lg`}
            >
              {pending ? "Updating…" : step.label}
            </button>
          </form>
        )}
        {run.state !== null && (
          <form action={formAction}>
            <HiddenFields trainId={run.train_id} state="timetable" />
            <button
              type="submit"
              disabled={pending}
              className={secondaryButtonClass}
            >
              Follow the timetable
            </button>
          </form>
        )}
      </div>

      <details>
        <summary className="cursor-pointer text-sm text-zinc-500">
          Set the status by hand
        </summary>
        <div className="pt-3">
          {/* Keyed so it shows the latest status after each change. */}
          <ManualForm
            key={run.updated_at ?? "timetable"}
            run={run}
            formAction={formAction}
            pending={pending}
          />
        </div>
      </details>

      <ActionMessage state={state} />
    </div>
  );
}
