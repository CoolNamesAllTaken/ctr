import type { Metadata } from "next";
import { Suspense } from "react";
import { apiFetch } from "@/lib/api";
import type { Kiosk, Rider, Tap } from "../types";
import { BackendUnreachable, formatMoney, mutedClass } from "../ui";
import { TapForm } from "./tap-form";

export const metadata: Metadata = {
  title: "Tap simulator · Employee portal · ctr",
};

const timeFormat = new Intl.DateTimeFormat("en-US", {
  timeStyle: "medium",
  timeZone: "America/Los_Angeles",
});

async function TapSimulator() {
  let data: [Rider[], Kiosk[], Tap[]];
  try {
    data = await Promise.all([
      apiFetch<Rider[]>("/api/riders/"),
      apiFetch<Kiosk[]>("/api/kiosks/"),
      apiFetch<Tap[]>("/api/taps/"),
    ]);
  } catch {
    return <BackendUnreachable />;
  }
  const [riders, kiosks, taps] = data;
  const ridersWithCards = riders.filter((rider) => rider.card_uid);

  return (
    <>
      <section className="flex flex-col gap-4">
        <h2 className="text-xl font-semibold">Simulate a card tap</h2>
        <p className={mutedClass}>
          Like Clipper: tapping on charges the maximum fare, and tapping off
          refunds the difference for a shorter trip. Tapping off at the same
          station cancels the trip with a full refund. A rider who forgets to
          tap off pays the maximum fare.
        </p>
        {ridersWithCards.length === 0 || kiosks.length === 0 ? (
          <p className={mutedClass}>
            You need a rider with a Chipper card and at least one kiosk. Set up
            a card on the Chipper cards tab, and load the starter data on the
            Trains tab for kiosks.
          </p>
        ) : (
          <TapForm riders={ridersWithCards} kiosks={kiosks} />
        )}
      </section>

      <section className="flex flex-col gap-4">
        <h2 className="text-xl font-semibold">Recent taps</h2>
        {taps.length === 0 ? (
          <p className={mutedClass}>No taps yet.</p>
        ) : (
          <table className="w-full text-left">
            <thead className="text-sm text-zinc-500">
              <tr className="border-b border-zinc-200 dark:border-zinc-800">
                <th className="py-2 font-medium">Time</th>
                <th className="py-2 font-medium">Rider</th>
                <th className="py-2 font-medium">Tap</th>
                <th className="py-2 text-right font-medium">Charge</th>
                <th className="py-2 text-right font-medium">Balance</th>
              </tr>
            </thead>
            <tbody>
              {taps.map((tap) => (
                <tr
                  key={tap.id}
                  className="border-b border-zinc-100 dark:border-zinc-900"
                >
                  <td className="py-2 text-sm tabular-nums">
                    {timeFormat.format(new Date(tap.tap_time))}
                  </td>
                  <td className="py-2">
                    {tap.rider_name ?? (
                      <span className="font-mono text-sm">{tap.card_uid}</span>
                    )}
                  </td>
                  <td className="py-2">
                    {tap.kind === "tap_on" ? "On" : "Off"} at {tap.station_name}
                  </td>
                  <td className="py-2 text-right tabular-nums">
                    {formatMoney(tap.card_charge)}
                  </td>
                  <td className="py-2 text-right tabular-nums">
                    {formatMoney(tap.balance_after)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}

export default function TapsPage() {
  return (
    <Suspense fallback={<p className="text-zinc-500">Loading…</p>}>
      <TapSimulator />
    </Suspense>
  );
}
