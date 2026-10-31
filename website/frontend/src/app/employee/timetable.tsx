import {
  DIRECTION_LABELS,
  type Direction,
  type Station,
  type Train,
} from "./types";
import { mutedClass } from "./ui";

function formatTime(time: string) {
  return time.slice(0, 5);
}

/** A timetable for one direction: stations down the side, trains across. */
export function Timetable({
  direction,
  stations,
  trains,
}: {
  direction: Direction;
  stations: Station[];
  trains: Train[];
}) {
  // Stations come ordered south to north; list them in the order trains
  // travelling this way reach them.
  const stops = direction === "northbound" ? stations : stations.toReversed();
  // Arrivals come ordered by time, so a train's first one is its departure.
  // Times are "HH:MM:SS", which sort correctly as strings.
  const columns = trains
    .filter((train) => train.direction === direction)
    .toSorted((a, b) =>
      (a.arrivals[0]?.time ?? "").localeCompare(b.arrivals[0]?.time ?? ""),
    )
    .map((train) => ({
      train,
      times: new Map(train.arrivals.map((a) => [a.station_id, a.time])),
    }));
  const servedStops = stops.filter((station) =>
    columns.some(({ times }) => times.has(station.id)),
  );

  return (
    <section className="flex flex-col gap-4">
      <h2 className="text-xl font-semibold">{DIRECTION_LABELS[direction]}</h2>
      {columns.length === 0 ? (
        <p className={mutedClass}>No {direction} trains scheduled.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead className="text-sm text-zinc-500">
              <tr className="border-b border-zinc-200 dark:border-zinc-800">
                <th className="py-2 pr-4 font-medium">Station</th>
                <th className="py-2 pr-4 font-medium">Zone</th>
                {columns.map(({ train }) => (
                  <th
                    key={train.id}
                    className="px-2 py-2 text-right font-medium whitespace-nowrap"
                  >
                    {train.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {servedStops.map((station) => (
                <tr
                  key={station.id}
                  className="border-b border-zinc-100 dark:border-zinc-900"
                >
                  <td className="py-2 pr-4 whitespace-nowrap">
                    {station.name}
                  </td>
                  <td className="py-2 pr-4 tabular-nums">{station.zone}</td>
                  {columns.map(({ train, times }) => {
                    const time = times.get(station.id);
                    return (
                      <td
                        key={train.id}
                        className="px-2 py-2 text-right tabular-nums"
                      >
                        {time ? (
                          formatTime(time)
                        ) : (
                          <span className="text-zinc-400">—</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
