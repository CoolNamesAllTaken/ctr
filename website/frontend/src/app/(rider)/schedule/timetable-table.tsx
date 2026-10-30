import Link from "next/link";
import type { Direction, Station, TrainRun } from "@/app/employee/types";
import { formatTime, SERVICE_TYPES, serviceType } from "./format";

const HEADINGS: Record<Direction, string> = {
  northbound: "Northbound",
  southbound: "Southbound",
};

/** One direction's timetable: stations down the side in travel order, trains
 * across in departure order. */
export function TimetableTable({
  direction,
  stations,
  trains,
}: {
  direction: Direction;
  // In travel order.
  stations: Station[];
  // In departure order.
  trains: TrainRun[];
}) {
  const columns = trains.map((run) => ({
    run,
    service: SERVICE_TYPES[serviceType(run.train_name)],
    stops: new Map(run.stops.map((stop) => [stop.station_id, stop])),
  }));

  return (
    <section className="flex flex-col gap-5">
      <h2 className="text-4xl font-black text-[#dd1f29]">
        {HEADINGS[direction]}
      </h2>
      {columns.length === 0 ? (
        <p className="text-lg">No {direction} trains are scheduled today.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="border-separate border-spacing-0 text-xs">
            <thead>
              <tr className="font-bold">
                <th
                  scope="col"
                  className="sticky left-0 w-[60px] min-w-[60px] border-b border-[#bcbdbe] bg-[#f6f6f6] px-2 py-[6px] text-right"
                >
                  Zone
                </th>
                <th
                  scope="col"
                  className="sticky left-[60px] min-w-[150px] border-r-2 border-b border-r-[#212529] border-b-[#bcbdbe] bg-white px-2 py-[6px] text-left"
                >
                  Station Name
                </th>
                {columns.map(({ run, service }) => (
                  <th
                    key={run.train_id}
                    scope="col"
                    className={`min-w-[56px] border-b border-white/40 px-[6px] py-[6px] text-left ${service.className}`}
                  >
                    {run.train_name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {stations.map((station, i) => {
                // A red rule where the train crosses into a new fare zone.
                const zoneEnds =
                  i < stations.length - 1 &&
                  stations[i + 1].zone !== station.zone;
                const rule = (color: string) =>
                  zoneEnds ? "border-b-[#dd1f29]" : color;
                return (
                  <tr key={station.id}>
                    <td
                      className={`sticky left-0 border-b bg-[#f6f6f6] px-2 py-[5px] text-right ${rule("border-b-[#bcbdbe]")}`}
                    >
                      {station.zone}
                    </td>
                    <th
                      scope="row"
                      className={`sticky left-[60px] border-r-2 border-b border-r-[#212529] bg-white px-2 py-[5px] text-left font-normal whitespace-nowrap ${rule("border-b-[#bcbdbe]")}`}
                    >
                      <Link
                        href={`/schedule?from=${station.id}`}
                        className="underline underline-offset-2 hover:text-[#dd1f29]"
                      >
                        {station.name}
                      </Link>
                    </th>
                    {columns.map(({ run, service, stops }) => {
                      const stop = stops.get(station.id);
                      return (
                        <td
                          key={run.train_id}
                          className={`border-b px-[6px] py-[5px] font-(family-name:--font-roboto-mono) whitespace-nowrap ${service.className} ${rule("border-b-white/40")}`}
                        >
                          {stop ? formatTime(stop.scheduled_at) : "--"}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
