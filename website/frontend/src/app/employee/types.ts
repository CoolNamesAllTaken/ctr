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
