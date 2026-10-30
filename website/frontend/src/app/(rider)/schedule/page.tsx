import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import { apiFetch } from "@/lib/api";
import type { Direction, Station, Timetable } from "@/app/employee/types";
import { SERVICE_TYPES } from "./format";
import { TimetableTable } from "./timetable-table";
import { TripFilter } from "./trip-filter";

export const metadata: Metadata = { title: "Schedules · Caltreat" };

function Legend() {
  return (
    <ul className="flex flex-wrap items-center text-xs">
      {Object.values(SERVICE_TYPES).map(({ label, className }) => (
        <li key={label} className={`px-[9px] py-[7px] ${className}`}>
          {label}
        </li>
      ))}
      <li className="px-[9px] py-[7px]">-- Train Bypasses Station</li>
    </ul>
  );
}

async function Schedule({
  searchParams,
}: {
  searchParams: PageProps<"/schedule">["searchParams"];
}) {
  const params = await searchParams;

  let timetable: Timetable;
  try {
    timetable = await apiFetch<Timetable>("/api/timetable/");
  } catch {
    return (
      <p className="text-lg text-[#dd1f29]">
        The schedule isn&apos;t available right now. Please try again shortly.
      </p>
    );
  }
  // Stations come ordered south to north.
  const { stations, trains } = timetable;
  const station = (param: string | string[] | undefined) =>
    stations.find((s) => String(s.id) === param) ?? null;
  const from = station(params.from);
  const to = station(params.to);

  // With an origin and a destination, show just the trains between them.
  const trip = from && to && from.id !== to.id ? { from, to } : null;
  const directions: Direction[] = trip
    ? [trip.from.position < trip.to.position ? "northbound" : "southbound"]
    : ["northbound", "southbound"];

  function tableProps(direction: Direction) {
    let stops: Station[] =
      direction === "northbound" ? stations : stations.toReversed();
    let runs = trains.filter(
      (run) => run.direction === direction && run.stops.length > 0,
    );
    if (trip) {
      const start = stops.indexOf(trip.from);
      stops = stops.slice(start, stops.indexOf(trip.to) + 1);
      runs = runs.filter((run) =>
        [trip.from.id, trip.to.id].every((id) =>
          run.stops.some((stop) => stop.station_id === id),
        ),
      );
    }
    return { direction, stations: stops, trains: runs };
  }

  return (
    <div className="flex flex-col gap-10">
      <section className="flex flex-col gap-4 text-lg">
        <p className="text-center">
          Find real-time data by selecting a Station below or the{" "}
          <Link href="/alerts" className="text-[#dd1f29] hover:underline">
            Real-Time Alerts tab
          </Link>
          .
        </p>
        <hr className="mt-4 border-[#dddddd]" />
      </section>

      <section className="flex flex-col gap-5">
        <h1 className="text-[40px] leading-tight font-black text-[#dd1f29]">
          Schedules
        </h1>
        <TripFilter
          stations={stations}
          from={from?.id ?? null}
          to={to?.id ?? null}
        />
        <Legend />
      </section>

      {directions.map((direction) => (
        <TimetableTable key={direction} {...tableProps(direction)} />
      ))}
    </div>
  );
}

export default function SchedulePage({ searchParams }: PageProps<"/schedule">) {
  return (
    <div className="mx-auto w-full max-w-[1196px] flex-1 bg-[#f6f6f6] px-4 py-6 sm:px-[34px]">
      <Suspense fallback={<p className="text-lg">Loading schedules…</p>}>
        <Schedule searchParams={searchParams} />
      </Suspense>
    </div>
  );
}
