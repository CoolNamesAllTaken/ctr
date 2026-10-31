import type { Metadata } from "next";
import { Suspense } from "react";
import { apiFetch } from "@/lib/api";
import { NewTrainForm } from "./new-train-form";
import { RenumberTrainForm } from "./renumber-train-form";
import { Timetable } from "./timetable";
import type { Station, Train, TrainCar } from "./types";
import { BackendUnreachable, mutedClass } from "./ui";

export const metadata: Metadata = { title: "Trains · Employee portal · ctr" };

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

  return (
    <>
      <section className="flex flex-col gap-4">
        <h2 className="text-xl font-semibold">Add trains</h2>
        <p className={mutedClass}>
          Adds one train, or several leaving a fixed number of minutes apart.
          Each takes the same number of minutes between stops, and they&apos;re
          numbered up from the first.
        </p>
        {traincars.length === 0 || stations.length < 2 ? (
          <p className={mutedClass}>
            Add at least one train car and two stations before scheduling
            trains, either with <code>make starter-data</code> or in the Django
            admin. Stations can also be added on the Stations &amp; kiosks tab.
          </p>
        ) : (
          <NewTrainForm traincars={traincars} stations={stations} />
        )}
      </section>

      <section className="flex flex-col gap-4">
        <h2 className="text-xl font-semibold">Schedule</h2>
        {trains.length === 0 ? (
          <p className={mutedClass}>No trains yet.</p>
        ) : (
          <>
            <Timetable
              direction="northbound"
              stations={stations}
              trains={trains}
            />
            <Timetable
              direction="southbound"
              stations={stations}
              trains={trains}
            />
          </>
        )}
      </section>

      {trains.length > 0 && (
        <section className="flex flex-col gap-4">
          <h2 className="text-xl font-semibold">
            Change a train&apos;s number
          </h2>
          <RenumberTrainForm trains={trains} />
        </section>
      )}
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
