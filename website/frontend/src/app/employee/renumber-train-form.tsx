"use client";

import { startTransition, useActionState, useRef } from "react";
import type { ActionState } from "./action-state";
import { renumberTrain } from "./actions";
import type { Train } from "./types";
import { ActionMessage, inputClass, primaryButtonClass } from "./ui";

function formatTime(time: string) {
  return time.slice(0, 5);
}

export function RenumberTrainForm({ trains }: { trains: Train[] }) {
  const formRef = useRef<HTMLFormElement>(null);
  const [state, formAction, pending] = useActionState<ActionState, FormData>(
    async (prev, formData) => {
      const result = await renumberTrain(prev, formData);
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
      className="flex flex-col gap-3"
    >
      <div className="flex flex-wrap items-end gap-4">
        <label className="flex grow flex-col gap-1 text-sm font-medium">
          Train
          <select name="id" required className={inputClass}>
            {trains.map((train) => {
              const first = train.arrivals[0];
              return (
                <option key={train.id} value={train.id}>
                  {train.name}
                  {first &&
                    ` · ${first.station_name} ${formatTime(first.time)}`}
                </option>
              );
            })}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm font-medium">
          New number
          <input
            name="name"
            required
            placeholder="e.g. 102"
            className={`${inputClass} sm:w-36`}
          />
        </label>
        <button type="submit" disabled={pending} className={primaryButtonClass}>
          {pending ? "Saving…" : "Save"}
        </button>
      </div>
      <ActionMessage state={state} />
    </form>
  );
}
