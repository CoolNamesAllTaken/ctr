import type { Metadata } from "next";
import { Suspense } from "react";
import type { Timetable } from "@/app/employee/types";
import { apiFetch } from "@/lib/api";

export const metadata: Metadata = { title: "Service Alerts · Caltreat" };

const postedFormat = new Intl.DateTimeFormat("en-US", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "America/Los_Angeles",
});

async function AlertList() {
  let timetable: Timetable;
  try {
    timetable = await apiFetch<Timetable>("/api/timetable/");
  } catch {
    return (
      <p className="text-lg text-[#dd1f29]">
        Service alerts aren&apos;t available right now.
      </p>
    );
  }
  if (timetable.alerts.length === 0) {
    return <p className="text-lg">There are no service alerts at this time.</p>;
  }

  return (
    <ul className="flex flex-col">
      {timetable.alerts.map((alert) => (
        <li
          key={alert.id}
          className="flex flex-col gap-1 border-b border-[#dddddd] py-4"
        >
          <p className="text-sm text-[#515151]">
            Posted {postedFormat.format(new Date(alert.created_at))}
          </p>
          <p className="text-lg">
            <b>
              {alert.train_name ? `Train ${alert.train_name}` : "All trains"}:
            </b>{" "}
            {alert.message}
          </p>
        </li>
      ))}
    </ul>
  );
}

export default function AlertListPage() {
  return (
    <div className="mx-auto flex w-full max-w-[1160px] flex-col gap-4 px-4 py-6">
      <h1 className="text-[40px] leading-tight font-black text-[#dd1f29]">
        Service Alerts
      </h1>
      <Suspense fallback={<p className="text-lg">Loading…</p>}>
        <AlertList />
      </Suspense>
    </div>
  );
}
