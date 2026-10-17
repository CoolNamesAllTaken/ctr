import type { Metadata } from "next";
import { Suspense } from "react";
import { apiFetch } from "@/lib/api";
import { NewTrainForm } from "./new-train-form";
import { StarterDataButton } from "./starter-data-button";
import { BackendUnreachable, mutedClass } from "./ui";
import {
  DIRECTION_LABELS,
  type Station,
  type Train,
  type TrainCar,
} from "./types";

export const metadata: Metadata = { title: "Trains · Employee portal · ctr" };

function formatTime(time: string) {
  return time.slice(0, 5);
}

async function TrainsTab() {
  let data: [TrainCar[], Station[], Train[]];
  try {
    data = await Promise.all([
      apiFetch<TrainCar[]>("/api/traincars/"),
      apiFetch<Station[]>("/api/stations/"),
      apiFetch<Train[]>("/api/trains/"),
    ]);
  } catch {
    return <BackendUnreachable />;
  }
  const [traincars, stations, trains] = data;
  const carNumbers = new Map(traincars.map((car) => [car.id, car.number]));

  return (
    <>
      <section className="flex flex-col gap-4">
        <h2 className="text-xl font-semibold">Add a scheduled train</h2>
        {traincars.length === 0 || stations.length < 2 ? (
          <p className={mutedClass}>
            Add at least one train car and two stations before scheduling
            trains, either in the Django admin or by loading the starter data
            below.
          </p>
        ) : (
          <NewTrainForm traincars={traincars} stations={stations} />
        )}
      </section>

      <section className="flex flex-col gap-4">
        <h2 className="text-xl font-semibold">Scheduled trains</h2>
        {trains.length === 0 ? (
          <p className={mutedClass}>No trains yet.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {trains.map((train) => {
              const first = train.arrivals[0];
              const last = train.arrivals.at(-1);
              return (
                <li
                  key={train.id}
                  className="rounded-md border border-zinc-200 px-4 py-3 dark:border-zinc-800"
                >
                  <p className="font-medium">
                    Train {train.name}{" "}
                    <span className="font-normal text-zinc-500">
                      · {DIRECTION_LABELS[train.direction]} · Car{" "}
                      {carNumbers.get(train.traincar_id)}
                    </span>
                  </p>
                  {first && last && (
                    <p className="text-sm text-zinc-600 dark:text-zinc-400">
                      {first.station_name} {formatTime(first.time)} →{" "}
                      {last.station_name} {formatTime(last.time)} (
                      {train.arrivals.length} stops)
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className="flex flex-col gap-4">
        <h2 className="text-xl font-semibold">Starter data</h2>
        <p className={mutedClass}>
          Adds two train cars, two stations, and two kiosks at each station.
          Only missing records are added; existing ones are left unchanged.
        </p>
        <StarterDataButton />
      </section>
    </>
  );
}

export default function TrainsPage() {
  return (
    <Suspense fallback={<p className="text-zinc-500">Loading…</p>}>
      <TrainsTab />
    </Suspense>
  );
}
