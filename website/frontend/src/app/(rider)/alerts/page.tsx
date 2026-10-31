import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";
import type { Timetable, TrainRun } from "@/app/employee/types";
import { apiFetch } from "@/lib/api";
import { AlertIcon } from "../site-tabs";
import { DelayTicker } from "./delay-ticker";

export const metadata: Metadata = { title: "Real-Time Alerts · Caltreat" };

// Styled after caltrain.com's Real-Time Alerts tab, and on purpose just as
// unhelpful: one delay at a time, rounded, with no reason given; a tip that
// points at the tab you're on; and a banner that doesn't say which train its
// alert is about.

/** The delay as the original would put it, or null if the train's on time. */
function delayMessage(run: TrainRun) {
  const stop = run.stops.find((s) => s.delay_minutes > 0);
  if (!stop) return null;
  const about = Math.max(5, Math.round(stop.delay_minutes / 5) * 5);
  return `Delayed: Train ${run.train_name} ${run.direction} is running about ${about} minutes late approaching ${stop.station_name}.`;
}

function InfoBox({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-4 rounded-[10px] bg-[#eaeaea] px-[10px] py-[10px]">
      <span
        aria-hidden="true"
        className="flex size-[25px] shrink-0 items-center justify-center rounded-full bg-[#dd1f29] font-serif text-base font-bold text-white"
      >
        i
      </span>
      <p className="text-base">{children}</p>
    </div>
  );
}

const pillClass =
  "rounded-full px-5 py-2 text-xs font-bold tracking-[0.15em] uppercase";

async function RealTimeAlerts() {
  let timetable: Timetable;
  try {
    timetable = await apiFetch<Timetable>("/api/timetable/");
  } catch {
    return (
      <p className="mx-auto w-full max-w-[1160px] px-4 py-6 text-lg text-[#dd1f29]">
        Real-time alerts aren&apos;t available right now.
      </p>
    );
  }
  // The timetable only carries active alerts, so canceled ones stay off here.
  const messages = [
    ...timetable.trains.map(delayMessage).filter((message) => message !== null),
    ...timetable.alerts.map(
      (alert) =>
        `${alert.train_name ? `Train ${alert.train_name}` : "All trains"}: ${alert.message}`,
    ),
  ];
  // Alerts come newest first; the banner only has room for one.
  const [latest] = timetable.alerts;

  return (
    <>
      <div className="mx-auto flex w-full max-w-[1160px] flex-col gap-6 px-4 py-4">
        <DelayTicker messages={messages} />
        <InfoBox>
          <i>
            <b>Tip:</b> Check the Service Alerts for live issues. For planned
            changes like construction or special schedules, refer to Caltreat
            Service Information. For real-time delays, use the Real-Time Alerts
            tab above.
          </i>
        </InfoBox>
        <InfoBox>
          These alerts are automatically generated in real time from
          Caltreat&apos;s operational data, such as delays, cancellations, or
          disruptions currently happening.
        </InfoBox>
        <div className="flex flex-wrap items-center justify-between gap-4 text-base">
          <div className="flex items-center gap-4">
            <Link
              href="/alerts/list"
              className={`${pillClass} bg-[#dd1f29] text-white`}
            >
              Alerts list
            </Link>
            Full List of Alerts and Service updates
          </div>
          <div className="flex items-center gap-4">
            <Link href="/alerts" className={`${pillClass} bg-[#eaeaea]`}>
              Subscribe
            </Link>
            Subscribe to Real Time Alerts
          </div>
        </div>
      </div>

      {latest && (
        <div className="mt-8 flex items-stretch bg-[#eaeaea] text-lg shadow-[0_-14px_20px_-14px_rgba(0,0,0,0.2)]">
          <div className="flex w-[50px] shrink-0 items-center justify-center bg-[#dd1f29] text-white">
            <AlertIcon />
          </div>
          <p className="px-[10px] py-[7px]">
            {latest.message}{" "}
            <Link href="/alerts/list" className="text-[#dd1f29] underline">
              More info
            </Link>
            .
          </p>
        </div>
      )}
    </>
  );
}

export default function AlertsPage() {
  return (
    <Suspense
      fallback={
        <p className="mx-auto w-full max-w-[1160px] px-4 py-6 text-lg">
          Loading…
        </p>
      }
    >
      <RealTimeAlerts />
    </Suspense>
  );
}
