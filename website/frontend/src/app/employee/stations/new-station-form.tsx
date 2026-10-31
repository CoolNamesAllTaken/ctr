"use client";

import { startTransition, useActionState, useRef } from "react";
import type { ActionState } from "../action-state";
import type { Station } from "../types";
import { ActionMessage, inputClass, primaryButtonClass } from "../ui";
import { createStation } from "./actions";

/** Where a new station can go: before the first, between each pair, or after
 * the last. Values are positions along the line, from the south end. */
function placeOptions(stations: Station[]) {
  if (stations.length === 0) return [{ value: 0, label: "First station" }];
  return [
    { value: 0, label: `South end, before ${stations[0].name}` },
    ...stations.slice(1).map((station, i) => ({
      value: i + 1,
      label: `Between ${stations[i].name} and ${station.name}`,
    })),
    {
      value: stations.length,
      label: `North end, after ${stations[stations.length - 1].name}`,
    },
  ];
}

export function NewStationForm({ stations }: { stations: Station[] }) {
  const formRef = useRef<HTMLFormElement>(null);
  const [state, formAction, pending] = useActionState<ActionState, FormData>(
    async (prev, formData) => {
      const result = await createStation(prev, formData);
      if (result.status === "success") formRef.current?.reset();
      return result;
    },
    { status: "idle" },
  );

  return (
    <form
      ref={formRef}
      // Submit via onSubmit rather than action={formAction}, so React doesn't
      // reset the form after an error and wipe what the employee typed.
      onSubmit={(e) => {
        e.preventDefault();
        const formData = new FormData(e.currentTarget);
        startTransition(() => formAction(formData));
      }}
      className="flex flex-col gap-5"
    >
      <div className="grid gap-4 sm:grid-cols-[1fr_5rem]">
        <label className="flex flex-col gap-1 text-sm font-medium">
          Station name
          <input
            name="name"
            required
            placeholder="e.g. Palo Alto"
            className={inputClass}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm font-medium">
          Zone
          <input
            name="zone"
            type="number"
            required
            min={1}
            max={99}
            defaultValue={1}
            className={inputClass}
          />
        </label>
      </div>
      <label className="flex flex-col gap-1 text-sm font-medium">
        Where on the line
        {/* Keyed so the default follows the north end as stations are added. */}
        <select
          key={stations.length}
          name="position"
          defaultValue={stations.length}
          className={inputClass}
        >
          {placeOptions(stations).map(({ value, label }) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>

      <div className="flex items-center gap-4">
        <button type="submit" disabled={pending} className={primaryButtonClass}>
          {pending ? "Adding…" : "Add station"}
        </button>
        <ActionMessage state={state} />
      </div>
    </form>
  );
}
