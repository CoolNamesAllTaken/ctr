import type { Metadata } from "next";
import { Suspense } from "react";
import { apiFetch } from "@/lib/api";
import type { Alert, Train } from "../types";
import { BackendUnreachable, mutedClass } from "../ui";
import { AlertForm } from "./alert-form";

export const metadata: Metadata = {
  title: "Alerts · Employee portal · ctr",
};

const dateTimeFormat = new Intl.DateTimeFormat("en-US", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "America/Los_Angeles",
});

async function Alerts() {
  let data: [Train[], Alert[]];
  try {
    data = await Promise.all([
      apiFetch<Train[]>("/api/trains/"),
      apiFetch<Alert[]>("/api/alerts/"),
    ]);
  } catch {
    return <BackendUnreachable />;
  }
  const [trains, alerts] = data;

  return (
    <>
      <section className="flex flex-col gap-4">
        <h2 className="text-xl font-semibold">Issue an alert</h2>
        <p className={mutedClass}>
          Tell riders about a delay or disruption. Pick a train, or leave it as
          the whole line for something like track work.
        </p>
        <AlertForm trains={trains} />
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
              </tr>
            </thead>
            <tbody>
              {alerts.map((alert) => (
                <tr
                  key={alert.id}
                  className="border-b border-zinc-100 align-top dark:border-zinc-900"
                >
                  <td className="py-2 pr-4 text-sm whitespace-nowrap tabular-nums">
                    {dateTimeFormat.format(new Date(alert.created_at))}
                  </td>
                  <td className="py-2 pr-4 whitespace-nowrap">
                    {alert.train_name ?? (
                      <span className="text-zinc-500">Whole line</span>
                    )}
                  </td>
                  <td className="py-2 whitespace-pre-line">{alert.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}

export default function AlertsPage() {
  return (
    <Suspense fallback={<p className="text-zinc-500">Loading…</p>}>
      <Alerts />
    </Suspense>
  );
}
