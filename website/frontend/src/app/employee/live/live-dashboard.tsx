"use client";

import { useEffect, useState } from "react";
import { formatTime } from "../format";
import type { Dashboard, RiderRef } from "../types";
import { mutedClass } from "../ui";
import { KioskTapsChart, KioskTapsTable, SERIES, ticks } from "./taps-chart";

const POLL_MS = 5000;

const clockFormat = new Intl.DateTimeFormat("en-US", {
  timeStyle: "medium",
  hourCycle: "h23",
  timeZone: "America/Los_Angeles",
});

function who(rider: RiderRef) {
  if (!rider.name) return `Card ${rider.card_uid}`;
  return rider.costume ? `${rider.name} (${rider.costume})` : rider.name;
}

/** A status that's never shown by colour alone. */
function Warning({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 text-sm font-medium">
      <span aria-hidden="true" className="text-(--viz-warning)">
        ▲
      </span>
      {children}
    </span>
  );
}

function Stat({
  label,
  value,
  note,
}: {
  label: string;
  value: string | number;
  note?: string;
}) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-zinc-200 p-4 dark:border-zinc-800">
      <span className="text-sm text-zinc-500">{label}</span>
      <span className="text-2xl font-semibold">{value}</span>
      {note && <span className="text-xs text-zinc-500">{note}</span>}
    </div>
  );
}

function KioskTaps({ data }: { data: Dashboard }) {
  const { kiosks, bucket_minutes } = data;
  if (kiosks.length === 0) {
    return (
      <p className={mutedClass}>
        No kiosks yet. Tap a card on one to register it.
      </p>
    );
  }
  if (data.taps === 0) {
    return <p className={mutedClass}>No taps yet tonight.</p>;
  }
  // One scale for every kiosk, so a taller column is a busier kiosk.
  const yTicks = ticks(
    Math.max(
      ...kiosks.flatMap((kiosk) =>
        kiosk.buckets.map((b) => b.tap_ons + b.tap_offs),
      ),
    ),
  );
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-4 text-sm">
        <span className="font-semibold">
          Taps per {bucket_minutes} minutes, by kiosk
        </span>
        {SERIES.map((series) => (
          <span key={series.key} className="flex items-center gap-1.5">
            <span className={`size-2.5 rounded-[2px] ${series.color}`} />
            <span className={mutedClass}>{series.label}</span>
          </span>
        ))}
      </div>
      <div className="grid gap-x-8 gap-y-6 sm:grid-cols-2">
        {kiosks.map((kiosk) => (
          <KioskTapsChart
            key={kiosk.kiosk_id}
            kiosk={kiosk}
            yTicks={yTicks}
            bucketMinutes={bucket_minutes}
          />
        ))}
      </div>
      <KioskTapsTable kiosks={kiosks} bucketMinutes={bucket_minutes} />
    </div>
  );
}

function Costumes({ costumes }: { costumes: Dashboard["costumes"] }) {
  if (costumes.length === 0) {
    return <p className={mutedClass}>No costumes yet.</p>;
  }
  const most = costumes[0].riders;
  return (
    <ul className="flex flex-col gap-2">
      {costumes.map((costume) => (
        <li key={costume.costume} className="flex items-center gap-3">
          <span className="w-32 shrink-0 text-sm">{costume.costume}</span>
          <span className="flex flex-1 items-center gap-2">
            <span
              className="h-3 rounded-r-[4px] bg-(--viz-series-1)"
              style={{ width: `${(costume.riders / most) * 100}%` }}
            />
            <span className="text-sm tabular-nums">{costume.riders}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

function Tonight({ data }: { data: Dashboard }) {
  return (
    <section className="flex flex-col gap-8">
      <h2 className="text-xl font-semibold">Tonight</h2>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Riders" value={data.riders} note="Cards tapped tonight" />
        <Stat
          label="Trips completed"
          value={data.trips_completed}
          note={`${data.trips_cancelled} cancelled`}
        />
        <Stat
          label="Riding now"
          value={data.riding_now}
          note="Tapped on, not off"
        />
        <Stat label="Chipper cards" value={data.cards} note="Issued so far" />
      </div>

      <KioskTaps data={data} />

      <div className="grid gap-6 sm:grid-cols-2">
        <div className="flex flex-col gap-3">
          <h3 className="font-semibold">Frequent riders</h3>
          {data.frequent_riders.length === 0 ? (
            <p className={mutedClass}>No finished trips yet.</p>
          ) : (
            <ol className="flex flex-col">
              {data.frequent_riders.map((rider, i) => (
                <li
                  key={rider.card_uid}
                  className="flex items-baseline gap-3 border-b border-zinc-100 py-2 dark:border-zinc-900"
                >
                  <span className="w-4 text-sm text-zinc-500 tabular-nums">
                    {i + 1}
                  </span>
                  <span className="flex-1">{who(rider)}</span>
                  <span className="text-sm tabular-nums">
                    {rider.trips} {rider.trips === 1 ? "trip" : "trips"}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </div>

        <div className="flex flex-col gap-3">
          <h3 className="font-semibold">Costumes tonight</h3>
          <Costumes costumes={data.costumes} />
        </div>
      </div>
    </section>
  );
}

/** The Live tab, refreshed every few seconds. */
export function LiveDashboard({
  initial,
  pollPath,
}: {
  initial: Dashboard;
  pollPath: string;
}) {
  const [data, setData] = useState(initial);
  const [stale, setStale] = useState(false);

  useEffect(() => {
    const id = setInterval(async () => {
      try {
        const response = await fetch(pollPath, { cache: "no-store" });
        // Logged out: the proxy sends the request to the login page.
        if (!response.ok || response.redirected) {
          throw new Error(`${response.status}`);
        }
        setData(await response.json());
        setStale(false);
      } catch {
        setStale(true);
      }
    }, POLL_MS);
    return () => clearInterval(id);
  }, [pollPath]);

  return (
    <div className="viz flex flex-col gap-6">
      <p aria-live="polite" className="text-sm text-zinc-500">
        {stale ? (
          <Warning>
            Couldn&apos;t refresh. Showing {formatTime(data.as_of)}; still
            trying.
          </Warning>
        ) : (
          `Updated ${clockFormat.format(new Date(data.as_of))} · refreshes every ${POLL_MS / 1000} seconds`
        )}
      </p>
      <div className={`transition-opacity ${stale ? "opacity-60" : ""}`}>
        <Tonight data={data} />
      </div>
    </div>
  );
}
