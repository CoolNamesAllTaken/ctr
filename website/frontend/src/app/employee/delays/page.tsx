import type { Metadata } from "next";
import { Suspense } from "react";
import { apiFetch } from "@/lib/api";
import { type Alert, DIRECTION_LABELS, type TrainRun } from "../types";
import { BackendUnreachable, mutedClass } from "../ui";
import { ClearDelayButton } from "./clear-delay-button";
import { DelayForm } from "./delay-form";
import { formatTime } from "./format";

export const metadata: Metadata = {
  title: "Live delays · Employee portal · ctr",
};

const dateFormat = new Intl.DateTimeFormat("en-US", {
  dateStyle: "full",
  timeZone: "America/Los_Angeles",
});

function TrainRunCard({ run }: { run: TrainRun }) {
  const maxDelay = Math.max(0, ...run.stops.map((stop) => stop.delay_minutes));
  const first = run.stops[0];
  const last = run.stops.at(-1);
  const alertMessages = [
    ...new Set(run.stops.map((stop) => stop.alert_message).filter(Boolean)),
  ];

  return (
    <li className="flex flex-col gap-3 rounded-md border border-zinc-200 px-4 py-3 dark:border-zinc-800">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-medium">
          Train {run.train_name}{" "}
          <span className="font-normal text-zinc-500">
            · {DIRECTION_LABELS[run.direction]}
          </span>
        </p>
        <span
          className={`text-sm font-medium ${maxDelay ? "text-amber-600" : "text-emerald-600"}`}
        >
          {maxDelay ? `Up to ${maxDelay} min late` : "On time"}
        </span>
      </div>

      {maxDelay === 0 ? (
        first &&
        last && (
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            {first.station_name} {formatTime(first.scheduled_at)} →{" "}
            {last.station_name} {formatTime(last.scheduled_at)} (
            {run.stops.length} stops)
          </p>
        )
      ) : (
        <>
          <table className="w-full text-left text-sm">
            <thead className="text-zinc-500">
              <tr className="border-b border-zinc-200 dark:border-zinc-800">
                <th className="py-1 font-medium">Station</th>
                <th className="py-1 text-right font-medium">Scheduled</th>
                <th className="py-1 text-right font-medium">Expected</th>
              </tr>
            </thead>
            <tbody>
              {run.stops.map((stop) => (
                <tr
                  key={stop.station_id}
                  className="border-b border-zinc-100 dark:border-zinc-900"
                >
                  <td className="py-1">{stop.station_name}</td>
                  <td className="py-1 text-right tabular-nums">
                    {formatTime(stop.scheduled_at)}
                  </td>
                  <td
                    className={`py-1 text-right tabular-nums ${stop.delay_minutes ? "text-amber-600" : ""}`}
                  >
                    {formatTime(stop.expected_at)}
                    {stop.delay_minutes > 0 && ` (+${stop.delay_minutes})`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {alertMessages.map((message) => (
            <p key={message} className={`text-sm ${mutedClass}`}>
              Alert: {message}
            </p>
          ))}
          <ClearDelayButton trainId={run.train_id} />
        </>
      )}
    </li>
  );
}

async function Delays() {
  let data: [TrainRun[], Alert[]];
  try {
    data = await Promise.all([
      apiFetch<TrainRun[]>("/api/delays/"),
      apiFetch<Alert[]>("/api/alerts/?active=true&limit=100"),
    ]);
  } catch {
    return <BackendUnreachable />;
  }
  const [runs, alerts] = data;
  const runsWithStops = runs.filter((run) => run.stops.length > 0);

  if (runsWithStops.length === 0) {
    return (
      <p className={mutedClass}>
        No trains scheduled yet. Add one on the Trains tab.
      </p>
    );
  }

  return (
    <>
      <section className="flex flex-col gap-4">
        <h2 className="text-xl font-semibold">Set a delay</h2>
        <p className={mutedClass}>
          Delays today&apos;s run of a train from a station to the end of the
          line; earlier stops keep the delay they had. Set it to 0 to put the
          train back on time from that station. Optionally link an active alert
          that explains the delay.
        </p>
        <DelayForm runs={runsWithStops} alerts={alerts} />
      </section>

      <section className="flex flex-col gap-4">
        <h2 className="text-xl font-semibold">
          Today&apos;s trains{" "}
          <span className="text-base font-normal text-zinc-500">
            ·{" "}
            {dateFormat.format(
              new Date(runsWithStops[0].stops[0].scheduled_at),
            )}
          </span>
        </h2>
        <ul className="flex flex-col gap-2">
          {runsWithStops.map((run) => (
            <TrainRunCard key={run.train_id} run={run} />
          ))}
        </ul>
      </section>
    </>
  );
}

export default function DelaysPage() {
  return (
    <Suspense fallback={<p className="text-zinc-500">Loading…</p>}>
      <Delays />
    </Suspense>
  );
}
