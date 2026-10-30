import type { LiveTrain, Station } from "@/app/employee/types";

// The street the line runs along, in feet, laid out from the stations: each
// station sits in front of a house, with a couple of houses between stations
// and a few more past each end. The track runs between the houses and the
// road, with more houses across the road. North (towards San Francisco) is to
// the right. Add a station and the street grows to fit.

/** One house per lot, so stations come out about 300ft apart. */
export const LOT_WIDTH = 100;
export const HOUSES_BETWEEN_STATIONS = 2;
const HOUSES_PAST_ENDS = 2;
const LOTS_PER_STATION = HOUSES_BETWEEN_STATIONS + 1;

/** Shown along the road; leave null to leave the road unlabelled. */
export const STREET_NAME: string | null = null;

// Rows from the top of the map down.
export const ROWS = {
  houseBottom: 42,
  track: 62,
  roadTop: 80,
  roadBottom: 110,
  housesAcrossTop: 128,
};
export const STREET_DEPTH = 170;

export type House = { x: number; width: number; depth: number };

export type Street = {
  width: number;
  houses: House[];
  housesAcross: House[];
  // Station id to how far along the street it is.
  stationX: Map<number, number>;
  // Where the track starts and ends.
  trackStart: number;
  trackEnd: number;
};

const lotCentre = (lot: number) => (lot + 0.5) * LOT_WIDTH;

export function layOutStreet(stations: Station[]): Street {
  // In line order, south to north.
  const ordered = stations.toSorted((a, b) => a.position - b.position);
  const lots =
    2 * HOUSES_PAST_ENDS +
    1 +
    Math.max(ordered.length - 1, 0) * LOTS_PER_STATION;

  // Vary the houses a little so the street doesn't look stamped out.
  const house = (x: number, seed: number) => ({
    x,
    width: 50 + ((seed * 17) % 16),
    depth: 30 + ((seed * 11) % 12),
  });
  const houses = Array.from({ length: lots }, (_, lot) =>
    house(lotCentre(lot), lot),
  );
  // Lots across the road don't line up with these ones.
  const housesAcross = Array.from({ length: lots + 1 }, (_, lot) =>
    house(lotCentre(lot) - LOT_WIDTH * 0.4, lot + 5),
  );

  const stationX = new Map(
    ordered.map((station, i) => [
      station.id,
      lotCentre(HOUSES_PAST_ENDS + i * LOTS_PER_STATION),
    ]),
  );
  const ends = [...stationX.values()];
  const runOff = LOT_WIDTH * 0.75;

  return {
    width: lots * LOT_WIDTH,
    houses,
    housesAcross,
    stationX,
    trackStart: (ends.at(0) ?? lotCentre(HOUSES_PAST_ENDS)) - runOff,
    trackEnd: (ends.at(-1) ?? lotCentre(HOUSES_PAST_ENDS)) + runOff,
  };
}

/** How far along the street a train is, or null if it's between stations the
 * street doesn't know about. */
export function trainX(street: Street, train: LiveTrain) {
  const from = street.stationX.get(train.from_station_id);
  const to = street.stationX.get(train.to_station_id);
  if (from === undefined || to === undefined) return null;
  return from + (to - from) * train.progress;
}
