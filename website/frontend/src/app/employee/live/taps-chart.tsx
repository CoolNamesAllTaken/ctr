"use client";

import { useState } from "react";
import { formatTime } from "../format";
import type { KioskActivity, TapBucket } from "../types";

export const SERIES = [
  { key: "tap_ons", label: "Tap-ons", color: "bg-(--viz-series-1)" },
  { key: "tap_offs", label: "Tap-offs", color: "bg-(--viz-series-2)" },
] as const;

/** Whole-number ticks from zero to a clean top at or above max. */
export function ticks(max: number) {
  const rough = Math.max(max, 1) / 4;
  const power = 10 ** Math.floor(Math.log10(rough));
  const step = Math.max(
    1,
    [1, 2, 5, 10].map((m) => m * power).find((s) => s >= rough) ?? power * 10,
  );
  const top = Math.max(step, Math.ceil(max / step) * step);
  return Array.from({ length: top / step + 1 }, (_, i) => i * step);
}

function stationLabel(kiosk: KioskActivity) {
  return kiosk.station_name ?? "No station";
}

/** Which buckets get a time under them: on the hour (every other hour on a
 * long night), plus the first unless an hour label is close behind it. */
function timeLabels(buckets: TapBucket[], bucketMinutes: number) {
  const hours = (buckets.length * bucketMinutes) / 60;
  const every = hours > 8 ? 2 : 1;
  const onTheHour = buckets.map((b) => {
    const [hour, minute] = formatTime(b.start).split(":").map(Number);
    return minute === 0 && hour % every === 0;
  });
  const gap = Math.max(3, Math.ceil(buckets.length / 6));
  return onTheHour.map(
    (hour, i) => hour || (i === 0 && !onTheHour.slice(1, gap).includes(true)),
  );
}

/** One kiosk's taps tonight, tap-ons stacked under tap-offs. `yTicks` is
 * shared by every kiosk so their heights compare. */
export function KioskTapsChart({
  kiosk,
  yTicks,
  bucketMinutes,
}: {
  kiosk: KioskActivity;
  yTicks: number[];
  bucketMinutes: number;
}) {
  const [hovered, setHovered] = useState<number | null>(null);
  const { buckets } = kiosk;
  const top = yTicks[yTicks.length - 1];
  const active = hovered === null ? null : buckets[hovered];
  const labelled = timeLabels(buckets, bucketMinutes);
  const name = `${stationLabel(kiosk)}, ${kiosk.kiosk_name}`;

  function period(bucket: TapBucket) {
    const end = new Date(
      new Date(bucket.start).getTime() + bucketMinutes * 60_000,
    );
    return `${formatTime(bucket.start)}–${formatTime(end.toISOString())}`;
  }

  return (
    <figure className="flex min-w-0 flex-col gap-2">
      <figcaption className="flex flex-wrap items-baseline justify-between gap-x-3">
        <span>
          <span className="font-medium">{stationLabel(kiosk)}</span>
          <span className="text-sm text-zinc-500"> · {kiosk.kiosk_name}</span>
        </span>
        <span className="text-sm text-zinc-500 tabular-nums">
          {kiosk.tap_ons} on · {kiosk.tap_offs} off
        </span>
      </figcaption>

      <div className="flex">
        {/* y-axis labels */}
        <div className="relative h-36 w-7 shrink-0 text-xs text-zinc-500 tabular-nums">
          {yTicks.map((tick) => (
            <span
              key={tick}
              className="absolute right-2 translate-y-1/2"
              style={{ bottom: `${(tick / top) * 100}%` }}
            >
              {tick}
            </span>
          ))}
        </div>

        <div className="flex min-w-0 flex-1 flex-col">
          <div
            role="group"
            aria-label={`Taps per ${bucketMinutes} minutes tonight at ${name}`}
            className="relative h-36"
            onPointerLeave={() => setHovered(null)}
          >
            {yTicks.map((tick) => (
              <div
                key={tick}
                className={`absolute inset-x-0 h-px ${tick === 0 ? "bg-(--viz-axis)" : "bg-(--viz-grid)"}`}
                style={{ bottom: `${(tick / top) * 100}%` }}
              />
            ))}

            <div className="absolute inset-0 flex">
              {buckets.map((bucket, i) => {
                const total = bucket.tap_ons + bucket.tap_offs;
                // Drawn top to bottom; only the top segment is rounded.
                const segments = [...SERIES]
                  .reverse()
                  .filter((series) => bucket[series.key] > 0);
                return (
                  <div
                    key={bucket.start}
                    tabIndex={0}
                    aria-label={`${period(bucket)}: ${bucket.tap_ons} tap-ons, ${bucket.tap_offs} tap-offs`}
                    className="flex h-full min-w-0 flex-1 cursor-default flex-col items-center justify-end px-px outline-none focus-visible:bg-zinc-500/10"
                    onPointerEnter={() => setHovered(i)}
                    onFocus={() => setHovered(i)}
                    onBlur={() => setHovered(null)}
                  >
                    {total > 0 && (
                      <div
                        className={`flex w-full max-w-6 flex-col gap-[2px] ${hovered === i ? "brightness-110" : ""}`}
                        style={{ height: `${(total / top) * 100}%` }}
                      >
                        {segments.map((series, j) => (
                          <div
                            key={series.key}
                            className={`${series.color} ${j === 0 ? "rounded-t-[4px]" : ""}`}
                            style={{ flex: `${bucket[series.key]} 1 0` }}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>

            {active && hovered !== null && (
              <div
                className="pointer-events-none absolute bottom-full z-10 mb-2 flex flex-col gap-1 rounded-md border border-zinc-200 bg-white px-3 py-2 text-sm whitespace-nowrap shadow-sm dark:border-zinc-800 dark:bg-zinc-950"
                style={{
                  left: `${((hovered + 0.5) / buckets.length) * 100}%`,
                  transform: `translateX(-${Math.min(Math.max(((hovered + 0.5) / buckets.length) * 100, 15), 85)}%)`,
                }}
              >
                <span className="text-xs text-zinc-500">{period(active)}</span>
                {SERIES.map((series) => (
                  <span key={series.key} className="flex items-center gap-2">
                    <span
                      className={`h-0.5 w-3 rounded-full ${series.color}`}
                    />
                    <span className="font-semibold tabular-nums">
                      {active[series.key]}
                    </span>
                    <span className="text-zinc-500">
                      {series.label.toLowerCase()}
                    </span>
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* x-axis */}
          <div className="relative h-5 text-xs text-zinc-500 tabular-nums">
            {buckets.map((bucket, i) =>
              labelled[i] ? (
                <span
                  key={bucket.start}
                  className="absolute top-1 -translate-x-1/2 whitespace-nowrap"
                  style={{ left: `${((i + 0.5) / buckets.length) * 100}%` }}
                >
                  {formatTime(bucket.start)}
                </span>
              ) : null,
            )}
          </div>
        </div>
      </div>
    </figure>
  );
}

/** Every kiosk's taps as one table, leaving out the quiet periods. */
export function KioskTapsTable({
  kiosks,
  bucketMinutes,
}: {
  kiosks: KioskActivity[];
  bucketMinutes: number;
}) {
  const rows = kiosks.flatMap((kiosk) =>
    kiosk.buckets
      .filter((bucket) => bucket.tap_ons + bucket.tap_offs > 0)
      .map((bucket) => ({ kiosk, bucket })),
  );
  rows.sort((a, b) => a.bucket.start.localeCompare(b.bucket.start));
  return (
    <details className="text-sm">
      <summary className="cursor-pointer text-zinc-500">
        Show as a table
      </summary>
      {rows.length === 0 ? (
        <p className="mt-2 text-zinc-500">No taps yet tonight.</p>
      ) : (
        <table className="mt-2 w-full text-left">
          <thead className="text-zinc-500">
            <tr className="border-b border-zinc-200 dark:border-zinc-800">
              <th className="py-1 font-medium">{bucketMinutes} min from</th>
              <th className="py-1 font-medium">Kiosk</th>
              <th className="py-1 text-right font-medium">Tap-ons</th>
              <th className="py-1 text-right font-medium">Tap-offs</th>
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {rows.map(({ kiosk, bucket }) => (
              <tr
                key={`${kiosk.kiosk_id}-${bucket.start}`}
                className="border-b border-zinc-100 dark:border-zinc-900"
              >
                <td className="py-1">{formatTime(bucket.start)}</td>
                <td className="py-1">
                  {stationLabel(kiosk)} · {kiosk.kiosk_name}
                </td>
                <td className="py-1 text-right">{bucket.tap_ons}</td>
                <td className="py-1 text-right">{bucket.tap_offs}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </details>
  );
}
