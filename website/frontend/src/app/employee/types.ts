// Mirrors the schemas in backend/core/api.py.

export type Direction = "northbound" | "southbound";

export type TrainCar = { id: number; number: string; capacity: number };

export type Station = {
  id: number;
  name: string;
  zone: number;
  position: number;
};

export type Train = {
  id: number;
  name: string;
  direction: Direction;
  traincar_id: number;
  arrivals: { station_id: number; station_name: string; time: string }[];
};

export const DIRECTION_LABELS: Record<Direction, string> = {
  northbound: "Northbound (towards San Francisco)",
  southbound: "Southbound (towards Mountain View)",
};

// Money comes from the API as decimal strings, e.g. "6.25".

export type Rider = {
  id: number;
  name: string;
  card_uid: string | null;
  balance: string | null;
};

export type Kiosk = {
  id: number;
  name: string;
  station_id: number;
  station_name: string;
};

export type Tap = {
  id: number;
  kind: "tap_on" | "tap_off";
  card_uid: string;
  rider_name: string | null;
  station_name: string;
  kiosk_name: string;
  tap_time: string;
  card_charge: string;
  balance_after: string;
};

export type TapResult = {
  tap: Tap;
  from_station: string | null;
  trip_fare: string | null;
};

export type Alert = {
  id: number;
  message: string;
  // Null for an alert about the whole line.
  train_id: number | null;
  train_name: string | null;
  is_active: boolean;
  created_at: string;
};
