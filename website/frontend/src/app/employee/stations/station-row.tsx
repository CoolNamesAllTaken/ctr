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
import { updateStation } from "./actions";
import { submitWithIntent } from "./submit";

export function StationRow({
  station,
  index,
  count,
  kiosks,
}: {
  station: Station;
  // Where it is along the line, from the south end, out of count stations.
  index: number;
  count: number;
  kiosks: Kiosk[];
}) {
  const [state, formAction, pending] = useActionState<ActionState, FormData>(
    updateStation,
    { status: "idle" },
  );

  return (
    <li className="rounded-md border border-zinc-200 px-4 py-3 dark:border-zinc-800">
      <form
        onSubmit={submitWithIntent(formAction)}
        className="flex flex-col gap-3"
      >
        <input type="hidden" name="id" value={station.id} />
        <input type="hidden" name="position" value={index} />
        <div className="flex flex-wrap items-end gap-2">
          <label className="flex grow flex-col gap-1 text-sm font-medium">
            Name
            <input
              name="name"
              required
              defaultValue={station.name}
              className={inputClass}
            />
          </label>
          <label className="flex w-20 flex-col gap-1 text-sm font-medium">
            Zone
            <input
              name="zone"
              type="number"
              required
              min={1}
              max={99}
              defaultValue={station.zone}
              className={inputClass}
            />
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
        </div>
        <p className="text-sm text-zinc-500">
          {kiosks.length === 0
            ? "No kiosks"
            : `Kiosks: ${kiosks.map((kiosk) => kiosk.name).join(", ")}`}
        </p>
        <div className="flex flex-wrap gap-2">
          <button
            type="submit"
            name="intent"
            value="south"
            disabled={pending || index === 0}
            className={secondaryButtonClass}
          >
            ↑ Move south
          </button>
          <button
            type="submit"
            name="intent"
            value="north"
            disabled={pending || index === count - 1}
            className={secondaryButtonClass}
          >
            ↓ Move north
          </button>
          <button
            type="submit"
            name="intent"
            value="delete"
            disabled={pending}
            data-confirm={`Delete ${station.name}?`}
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
