import type { Metadata } from "next";
import { Suspense } from "react";
import { apiFetch } from "@/lib/api";
import type { Alert, TrainRun } from "../types";
import { BackendUnreachable, mutedClass } from "../ui";
import { AlertForm } from "./alert-form";
import { CancelAlertButton } from "./cancel-alert-button";
import { DelayForm } from "./delay-form";
import { TrainRunCard } from "./train-run-card";

export const metadata: Metadata = {
  title: "Alerts & delays · Employee portal · ctr",
};

const dateFormat = new Intl.DateTimeFormat("en-US", {
  dateStyle: "full",
  timeZone: "America/Los_Angeles",
});

const dateTimeFormat = new Intl.DateTimeFormat("en-US", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "America/Los_Angeles",
});

async function AlertsAndDelays() {
  let data: [TrainRun[], TrainRun[], Alert[], Alert[]];
  try {
    data = await Promise.all([
      apiFetch<TrainRun[]>("/api/delays/"),
      // Trains still to finish their run, counting delays; there's no point
      // delaying or alerting about the rest.
      apiFetch<TrainRun[]>("/api/delays/?upcoming=true"),
      apiFetch<Alert[]>("/api/alerts/"),
      apiFetch<Alert[]>("/api/alerts/?active=true&limit=100"),
    ]);
  } catch {
    return <BackendUnreachable />;
  }
  const [runs, upcomingRuns, alerts, activeAlerts] = data;
  const runsWithStops = runs.filter((run) => run.stops.length > 0);

  return (
    <>
      <section className="flex flex-col gap-4">
        <h2 className="text-xl font-semibold">Issue an alert</h2>
        <p className={mutedClass}>
          Tell riders about a delay or disruption. Pick a train, or leave it as
          the whole line for something like track work.
        </p>
        <AlertForm runs={upcomingRuns} />
      </section>

      <section className="flex flex-col gap-4">
        <h2 className="text-xl font-semibold">Recent alerts</h2>
        {alerts.length === 0 ? (
          <p className={mutedClass}>No alerts yet.</p>
        ) : (
          <table className="w-full text-left">
            <thead className="text-sm text-zinc-500">
              <tr className="border-b border-zinc-200 dark:border-zinc-800">
                <th className="py-2 font-medium">Issued</th>
                <th className="py-2 font-medium">Train</th>
                <th className="py-2 font-medium">Message</th>
                <th className="py-2 text-right font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {alerts.map((alert) => (
                <tr
                  key={alert.id}
                  className={`border-b border-zinc-100 align-top dark:border-zinc-900 ${alert.is_active ? "" : "text-zinc-500"}`}
                >
                  <td className="py-2 pr-4 text-sm whitespace-nowrap tabular-nums">
                    {dateTimeFormat.format(new Date(alert.created_at))}
                  </td>
                  <td className="py-2 pr-4 whitespace-nowrap">
                    {alert.train_name ?? (
                      <span className="text-zinc-500">Whole line</span>
                    )}
                  </td>
                  <td className="py-2 pr-4 whitespace-pre-line">
                    {alert.message}
                  </td>
                  <td className="py-2 text-right">
                    {alert.is_active ? (
                      <CancelAlertButton alertId={alert.id} />
                    ) : (
                      <span className="text-sm">Canceled</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="flex flex-col gap-4">
        <h2 className="text-xl font-semibold">Set a delay</h2>
        {runsWithStops.length === 0 ? (
          <p className={mutedClass}>
            No trains scheduled yet. Add one on the Trains tab.
          </p>
        ) : upcomingRuns.length === 0 ? (
          <p className={mutedClass}>No more trains today.</p>
        ) : (
          <>
            <p className={mutedClass}>
              Delays today&apos;s run of a train from a station to the end of
              the line; earlier stops keep the delay they had. Set it to 0 to
              put the train back on time from that station. Optionally link an
              active alert that explains the delay.
            </p>
            <DelayForm runs={upcomingRuns} alerts={activeAlerts} />
          </>
        )}
      </section>

      {runsWithStops.length > 0 && (
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
      )}
    </>
  );
}

export default function AlertsPage() {
  return (
    <Suspense fallback={<p className="text-zinc-500">Loading…</p>}>
      <AlertsAndDelays />
    </Suspense>
  );
}
