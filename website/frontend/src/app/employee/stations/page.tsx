import type { Metadata } from "next";
import { Suspense } from "react";
import { apiFetch } from "@/lib/api";
import type { Kiosk, Station } from "../types";
import { BackendUnreachable, mutedClass } from "../ui";
import { KioskRow } from "./kiosk-row";
import { NewStationForm } from "./new-station-form";
import { StationRow } from "./station-row";

export const metadata: Metadata = {
  title: "Stations & kiosks · Employee portal · ctr",
};

async function StationsTab() {
  let data: [Station[], Kiosk[]];
  try {
    data = await Promise.all([
      apiFetch<Station[]>("/api/stations/"),
      apiFetch<Kiosk[]>("/api/kiosks/"),
    ]);
  } catch {
    return <BackendUnreachable />;
  }
  const [stations, kiosks] = data;
  const unassigned = kiosks.filter((kiosk) => kiosk.station_id === null);
  const assigned = kiosks.filter((kiosk) => kiosk.station_id !== null);

  return (
    <>
      <section className="flex flex-col gap-4">
        <h2 className="text-xl font-semibold">Kiosks</h2>
        <p className={mutedClass}>
          A kiosk shows up here the first time a card is tapped on it. Give it a
          station before it can charge fares; until then, taps on it are
          refused.
        </p>
        {kiosks.length === 0 ? (
          <p className={mutedClass}>
            No kiosks yet. Tap a card on one to register it.
          </p>
        ) : (
          <>
            {unassigned.length > 0 && (
              <p className="font-medium text-amber-700 dark:text-amber-400">
                {unassigned.length === 1
                  ? "1 kiosk is"
                  : `${unassigned.length} kiosks are`}{" "}
                waiting for a station.
              </p>
            )}
            {/* One list, waiting kiosks first, so a row keeps its message
                when it's given a station. */}
            <ul className="flex flex-col gap-2">
              {[...unassigned, ...assigned].map((kiosk) => (
                <KioskRow key={kiosk.id} kiosk={kiosk} stations={stations} />
              ))}
            </ul>
          </>
        )}
      </section>

      <section className="flex flex-col gap-4">
        <h2 className="text-xl font-semibold">Stations</h2>
        {stations.length === 0 ? (
          <p className={mutedClass}>No stations yet.</p>
        ) : (
          <>
            <p className={mutedClass}>
              In order along the line, from the south end. A station can only be
              deleted once no train stops there and it has no kiosks.
            </p>
            <ul className="flex flex-col gap-2">
              {stations.map((station, index) => (
                <StationRow
                  key={station.id}
                  station={station}
                  index={index}
                  count={stations.length}
                  kiosks={assigned.filter(
                    (kiosk) => kiosk.station_id === station.id,
                  )}
                />
              ))}
            </ul>
          </>
        )}
      </section>

      <section className="flex flex-col gap-4">
        <h2 className="text-xl font-semibold">Add a station</h2>
        <NewStationForm stations={stations} />
      </section>
    </>
  );
}

export default function StationsPage() {
  return (
    <Suspense fallback={<p className="text-zinc-500">Loading…</p>}>
      <StationsTab />
    </Suspense>
  );
}
