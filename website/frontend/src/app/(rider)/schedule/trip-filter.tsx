"use client";

import { useRouter } from "next/navigation";
import type { Station } from "@/app/employee/types";

const selectClass =
  "h-[38px] w-full appearance-none rounded bg-[#eaeaea] pr-10 pl-3 text-lg text-[#555]";

function Chevron() {
  return (
    <svg
      viewBox="0 0 20 20"
      fill="none"
      stroke="#212529"
      strokeWidth="1.8"
      aria-hidden="true"
      className="pointer-events-none absolute top-1/2 right-3 size-6 -translate-y-1/2"
    >
      <path d="m4 7 6 6 6-6" />
    </svg>
  );
}

/** Origin and destination pickers; choosing both narrows the timetable to
 * trains between them. */
export function TripFilter({
  stations,
  from,
  to,
}: {
  stations: Station[];
  from: number | null;
  to: number | null;
}) {
  const router = useRouter();

  function go(nextFrom: number | null, nextTo: number | null) {
    const params = new URLSearchParams();
    if (nextFrom) params.set("from", String(nextFrom));
    if (nextTo) params.set("to", String(nextTo));
    const query = params.toString();
    router.push(query ? `/schedule?${query}` : "/schedule", { scroll: false });
  }

  const options = stations.map((station) => (
    <option key={station.id} value={station.id}>
      {station.name}
    </option>
  ));

  return (
    <div className="grid items-center gap-3 sm:grid-cols-[1fr_auto_1fr_1fr] sm:gap-5">
      <div className="relative">
        <select
          aria-label="Origin"
          value={from ?? ""}
          onChange={(e) => go(Number(e.target.value) || null, to)}
          className={selectClass}
        >
          <option value="">- origin -</option>
          {options}
        </select>
        <Chevron />
      </div>
      <button
        type="button"
        aria-label="Swap origin and destination"
        onClick={() => go(to, from)}
        className="justify-self-center p-1"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="#212529"
          strokeWidth="2.6"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
          className="size-8"
        >
          <path d="M5 21V8a4 4 0 0 1 8 0v8a4 4 0 0 0 8 0V3M2 18l3 3 3-3M18 6l3-3 3 3" />
        </svg>
      </button>
      <div className="relative">
        <select
          aria-label="Destination"
          value={to ?? ""}
          onChange={(e) => go(from, Number(e.target.value) || null)}
          className={selectClass}
        >
          <option value="">- destination -</option>
          {options}
        </select>
        <Chevron />
      </div>
    </div>
  );
}
