"use client";

import { useActionState } from "react";
import type { ActionState } from "../action-state";
import type { Kiosk, Station } from "../types";
import {
  ActionMessage,
  inputClass,
  primaryButtonClass,
  secondaryButtonClass,
} from "../ui";
import { updateKiosk } from "./actions";
import { submitWithIntent } from "./submit";

export function KioskRow({
  kiosk,
  stations,
}: {
  kiosk: Kiosk;
  stations: Station[];
}) {
  const [state, formAction, pending] = useActionState<ActionState, FormData>(
    updateKiosk,
    { status: "idle" },
  );

  return (
    <li
      className={`rounded-md border px-4 py-3 ${
        kiosk.station_id === null
          ? "border-amber-400 dark:border-amber-600"
          : "border-zinc-200 dark:border-zinc-800"
      }`}
    >
      <form
        onSubmit={submitWithIntent(formAction)}
        className="flex flex-col gap-3"
      >
        <input type="hidden" name="id" value={kiosk.id} />
        <p className="text-sm text-zinc-500">
          Kiosk ID <span className="font-mono">{kiosk.id}</span>
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <label className="flex grow flex-col gap-1 text-sm font-medium">
            Name
            <input
              name="name"
              required
              defaultValue={kiosk.name}
              placeholder="e.g. Northbound platform"
              className={inputClass}
            />
          </label>
          <label className="flex grow flex-col gap-1 text-sm font-medium">
            Station
            <select
              name="station_id"
              defaultValue={kiosk.station_id ?? ""}
              className={inputClass}
            >
              <option value="">No station (out of service)</option>
              {stations.map((station) => (
                <option key={station.id} value={station.id}>
                  {station.name}
                </option>
              ))}
            </select>
          </label>
          <button
            type="submit"
            name="intent"
            value="save"
            disabled={pending}
            className={primaryButtonClass}
          >
            {pending ? "Saving…" : "Save"}
          </button>
          <button
            type="submit"
            name="intent"
            value="delete"
            disabled={pending}
            data-confirm={`Delete kiosk ${kiosk.id}? If it taps again, it'll be registered again.`}
            className={secondaryButtonClass}
          >
            Delete
          </button>
        </div>
        <ActionMessage state={state} />
      </form>
    </li>
  );
}
