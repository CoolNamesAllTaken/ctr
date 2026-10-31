"use client";

import { startTransition, useActionState, useRef, useState } from "react";
import type { ActionState } from "./action-state";
import { createTrains } from "./actions";
import {
  DIRECTION_LABELS,
  type Direction,
  type Station,
  type TrainCar,
} from "./types";
import { ActionMessage, inputClass, primaryButtonClass } from "./ui";

function toMinutes(time: string) {
  const [hours, minutes] = time.split(":").map(Number);
  return hours * 60 + minutes;
}

function fromMinutes(minutes: number) {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}`;
}

/** What the form will add, in a sentence, or null until it's filled in. */
function describe(formData: FormData, stops: Station[]) {
  const firstNumber = Number(formData.get("first_number"));
  const count = Number(formData.get("count"));
  const departure = String(formData.get("first_departure") ?? "");
  const betweenTrains = Number(formData.get("minutes_between_trains"));
  const betweenStops = Number(formData.get("minutes_between_stops"));
  const checked = new Set(formData.getAll("station_id").map(Number));
  const served = stops.filter((station) => checked.has(station.id));
  if (
    !formData.get("first_number") ||
    !departure ||
    count < 1 ||
    betweenTrains < 1 ||
    betweenStops < 1 ||
    served.length < 2
  ) {
    return null;
  }

  const start = toMinutes(departure);
  const departures = Array.from({ length: count }, (_, i) =>
    fromMinutes(start + betweenTrains * i),
  );
  const journey = betweenStops * (served.length - 1);
  const first = served[0].name;
  const last = served.at(-1)?.name;
  const trains =
    count === 1
      ? `Train ${firstNumber} leaves ${first} at ${departures[0]}`
      : `Trains ${firstNumber}–${firstNumber + count - 1} leave ${first} every ${betweenTrains} min from ${departures[0]} to ${departures.at(-1)}`;
  const pastMidnight =
    start + betweenTrains * (count - 1) + journey >= 24 * 60
      ? " The last one would run past midnight, so shorten the run."
      : "";
  return `${trains}, reaching ${last} ${journey} min later.${pastMidnight}`;
}

export function NewTrainForm({
  traincars,
  stations,
}: {
  traincars: TrainCar[];
  stations: Station[];
}) {
  const formRef = useRef<HTMLFormElement>(null);
  const [direction, setDirection] = useState<Direction>("northbound");
  // The form's current values, for the summary under it.
  const [values, setValues] = useState<FormData | null>(null);
  const [state, formAction, pending] = useActionState<ActionState, FormData>(
    async (prev, formData) => {
      const result = await createTrains(prev, formData);
      if (result.status === "success" && formRef.current) {
        formRef.current.reset();
        setValues(new FormData(formRef.current));
      }
      return result;
    },
    { status: "idle" },
  );

  // Stations come ordered south to north; list them in the order the train
  // reaches them.
  const stops = direction === "northbound" ? stations : stations.toReversed();
  const summary = values && describe(values, stops);

  return (
    <form
      ref={formRef}
      onChange={(e) => setValues(new FormData(e.currentTarget))}
      // Submit via onSubmit rather than action={formAction}, so React doesn't
      // reset the form after an error and wipe what the employee typed.
      onSubmit={(e) => {
        e.preventDefault();
        const formData = new FormData(e.currentTarget);
        startTransition(() => formAction(formData));
      }}
      className="flex flex-col gap-5"
    >
      <div className="grid gap-4 sm:grid-cols-[auto_1fr_1fr]">
        <label className="flex flex-col gap-1 text-sm font-medium">
          First train number
          <input
            name="first_number"
            type="number"
            required
            min={0}
            placeholder="e.g. 101"
            className={`${inputClass} sm:w-36`}
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

      <div className="grid gap-4 sm:grid-cols-4">
        <label className="flex flex-col gap-1 text-sm font-medium">
          First departure
          <input
            name="first_departure"
            type="time"
            required
            className={inputClass}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm font-medium">
          Number of trains
          <input
            name="count"
            type="number"
            required
            min={1}
            max={100}
            defaultValue={1}
            className={inputClass}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm font-medium">
          Min between trains
          <input
            name="minutes_between_trains"
            type="number"
            required
            min={1}
            max={1440}
            defaultValue={30}
            className={inputClass}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm font-medium">
          Min between stops
          <input
            name="minutes_between_stops"
            type="number"
            required
            min={1}
            max={1440}
            defaultValue={5}
            className={inputClass}
          />
        </label>
      </div>

      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1 text-sm font-medium">
          Stops{" "}
          <span className="font-normal text-zinc-500">
            (untick a station to skip it)
          </span>
        </legend>
        {stops.map((station) => (
          <label
            key={station.id}
            className="flex items-center gap-3 rounded-md border border-zinc-200 px-3 py-2 dark:border-zinc-800"
          >
            <input
              type="checkbox"
              name="station_id"
              value={station.id}
              defaultChecked
            />
            <span>
              {station.name}{" "}
              <span className="text-sm text-zinc-500">Zone {station.zone}</span>
            </span>
          </label>
        ))}
      </fieldset>

      {summary && <p className="text-sm text-zinc-500">{summary}</p>}

      <div className="flex items-center gap-4">
        <button type="submit" disabled={pending} className={primaryButtonClass}>
          {pending ? "Adding…" : "Add trains"}
        </button>
        <ActionMessage state={state} />
      </div>
    </form>
  );
}
