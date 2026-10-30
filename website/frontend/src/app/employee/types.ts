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

// Times are ISO datetimes for today's run of the train.
export type StopDelay = {
  station_id: number;
  station_name: string;
  scheduled_at: string;
  expected_at: string;
  delay_minutes: number;
  alert_id: number | null;
  alert_message: string | null;
};

export type TrainRun = {
  train_id: number;
  train_name: string;
  direction: Direction;
  stops: StopDelay[];
};

export type Timetable = {
  date: string;
  stations: Station[];
  trains: TrainRun[];
  // Active ones only.
  alerts: Alert[];
};

// A train on the live map: between two stations, or at one (when both are
// that station and at_station_id is set).
export type LiveTrain = {
  train_id: number;
  train_name: string;
  direction: Direction;
  from_station_id: number;
  to_station_id: number;
  // 0 at from_station, 1 at to_station.
  progress: number;
  at_station_id: number | null;
  delay_minutes: number;
  // Including the one it's at.
  upcoming_stops: StopDelay[];
};

export type LiveMap = {
  as_of: string;
  stations: Station[];
  trains: LiveTrain[];
};

export type TrainState = "stopped" | "moving" | "out_of_service";

// Today's run of a train with what the conductor last marked it. The status
// fields are all null while it's following the timetable.
export type TrainStatus = TrainRun & {
  state: TrainState | null;
  // Where it's stopped, or the stop it last left.
  station_id: number | null;
  station_name: string | null;
  updated_at: string | null;
};
