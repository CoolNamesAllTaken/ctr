"use client";

import { startTransition, useActionState, useRef, useState } from "react";
import type { ActionState } from "./action-state";
import { createTrain } from "./actions";
import {
  DIRECTION_LABELS,
  type Direction,
  type Station,
  type TrainCar,
} from "./types";
import { ActionMessage, inputClass, primaryButtonClass } from "./ui";

export function NewTrainForm({
  traincars,
  stations,
}: {
  traincars: TrainCar[];
  stations: Station[];
}) {
  const formRef = useRef<HTMLFormElement>(null);
  // Stop times by station id. Controlled, so a stop can be cleared with one
  // click; native time inputs make that fiddly.
  const [times, setTimes] = useState<Record<number, string>>({});
  const [state, formAction, pending] = useActionState<ActionState, FormData>(
    async (prev, formData) => {
      const result = await createTrain(prev, formData);
      if (result.status === "success") {
        formRef.current?.reset();
        setTimes({});
      }
      return result;
    },
    { status: "idle" },
  );
  const [direction, setDirection] = useState<Direction>("northbound");

  // Stations come ordered south to north; list them in the order the train
  // reaches them.
  const stops = direction === "northbound" ? stations : [...stations].reverse();

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
      <div className="grid gap-4 sm:grid-cols-[1fr_auto_1fr]">
        <label className="flex flex-col gap-1 text-sm font-medium">
          Train name
          <input
            name="name"
            required
            placeholder="e.g. 101"
            className={inputClass}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm font-medium">
          Direction
          <select
            name="direction"
            value={direction}
            onChange={(e) => setDirection(e.target.value as Direction)}
            className={inputClass}
          >
            {Object.entries(DIRECTION_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm font-medium">
          Train car
          <select name="traincar_id" required className={inputClass}>
            {traincars.map((car) => (
              <option key={car.id} value={car.id}>
                Car {car.number} ({car.capacity} seats)
              </option>
            ))}
          </select>
        </label>
      </div>

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-sm font-medium">
          Stops{" "}
          <span className="font-normal text-zinc-500">
            (leave a time blank to skip that station)
          </span>
        </legend>
        {stops.map((station) => (
          <div
            key={station.id}
            className="flex items-center justify-between gap-4 rounded-md border border-zinc-200 px-3 py-2 dark:border-zinc-800"
          >
            <label htmlFor={`stop-${station.id}`}>
              {station.name}{" "}
              <span className="text-sm text-zinc-500">Zone {station.zone}</span>
            </label>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setTimes({ ...times, [station.id]: "" })}
                // Hidden rather than removed, so the time inputs stay aligned.
                className={`text-sm text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100 ${times[station.id] ? "" : "invisible"}`}
                aria-label={`Clear ${station.name} time`}
              >
                Clear
              </button>
              <input
                type="time"
                id={`stop-${station.id}`}
                name={`stop-${station.id}`}
                value={times[station.id] ?? ""}
                onChange={(e) =>
                  setTimes({ ...times, [station.id]: e.target.value })
                }
                className={inputClass}
              />
            </div>
          </div>
        ))}
      </fieldset>

      <div className="flex items-center gap-4">
        <button type="submit" disabled={pending} className={primaryButtonClass}>
          {pending ? "Adding…" : "Add train"}
        </button>
        <ActionMessage state={state} />
      </div>
    </form>
  );
}
