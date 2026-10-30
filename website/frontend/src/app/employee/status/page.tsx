import type { Metadata } from "next";
import { Suspense } from "react";
import { apiFetch } from "@/lib/api";
import { formatTime } from "../delays/format";
import { DIRECTION_LABELS, type TrainStatus } from "../types";
import { BackendUnreachable, mutedClass } from "../ui";
import { StatusControls } from "./status-controls";

export const metadata: Metadata = {
  title: "Train status · Employee portal · ctr",
};

function describe(run: TrainStatus) {
  switch (run.state) {
    case "stopped":
      return {
        text: `Stopped at ${run.station_name}`,
        className: "bg-amber-500",
      };
    case "moving": {
      const left = run.stops.findIndex(
        (stop) => stop.station_id === run.station_id,
      );
      const next = run.stops[left + 1];
      return {
        text: `Moving: ${run.station_name} → ${next?.station_name ?? "?"}`,
        className: "bg-emerald-500",
      };
    }
    case "out_of_service":
      return { text: "Out of service", className: "bg-zinc-400" };
    case null:
      return { text: "Following the timetable", className: "bg-sky-500" };
  }
}

function TrainStatusCard({ run }: { run: TrainStatus }) {
  const first = run.stops[0];
  const last = run.stops.at(-1);
  const { text, className } = describe(run);

  return (
    <li className="flex flex-col gap-4 rounded-md border border-zinc-200 px-4 py-3 dark:border-zinc-800">
      <div className="flex flex-col gap-1">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p className="font-medium">
            Train {run.train_name}{" "}
            <span className="font-normal text-zinc-500">
              · {DIRECTION_LABELS[run.direction]}
            </span>
          </p>
          <p className="flex items-center gap-2 text-sm font-medium">
            <span
              aria-hidden="true"
              className={`size-2.5 rounded-full ${className}`}
            />
            {text}
            {run.updated_at && (
              <span className="font-normal text-zinc-500">
                since {formatTime(run.updated_at)}
              </span>
            )}
          </p>
        </div>
        {first && last && (
          <p className={`text-sm ${mutedClass}`}>
            {first.station_name} {formatTime(first.expected_at)} →{" "}
            {last.station_name} {formatTime(last.expected_at)}
          </p>
        )}
      </div>
      <StatusControls run={run} />
    </li>
  );
}

async function Statuses() {
  let runs: TrainStatus[];
  try {
    runs = await apiFetch<TrainStatus[]>("/api/train-statuses/");
  } catch {
    return <BackendUnreachable />;
  }
  const runsWithStops = runs.filter((run) => run.stops.length > 0);

  if (runsWithStops.length === 0) {
    return (
      <p className={mutedClass}>
        No trains scheduled yet. Add one on the Trains tab.
      </p>
    );
  }

  return (
    <ul className="flex flex-col gap-2">
      {runsWithStops.map((run) => (
        <TrainStatusCard key={run.train_id} run={run} />
      ))}
    </ul>
  );
}

export default function StatusPage() {
  return (
    <section className="flex flex-col gap-4">
      <h2 className="text-xl font-semibold">Train status</h2>
      <p className={mutedClass}>
        Tell the live map where today&apos;s trains are. Mark a train stopped
        when it pulls in to a station and depart it when it leaves; while it
        moves, the map edges it towards the next stop until it arrives. Trains
        you haven&apos;t marked follow the timetable, and out-of-service trains
        are left off the map.
      </p>
      <Suspense fallback={<p className="text-zinc-500">Loading…</p>}>
        <Statuses />
      </Suspense>
    </section>
  );
}
