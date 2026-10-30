"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { LiveMap, LiveTrain } from "@/app/employee/types";
import { formatTime, type ServiceType, serviceType } from "../schedule/format";
import {
  type House,
  layOutStreet,
  ROWS,
  STREET_DEPTH,
  STREET_NAME,
  trainX,
} from "./street";

const POLL_MS = 15_000;
const MAP_HEIGHT = 420;
// Pixels per foot never drops below this, so labels don't pile up on phones;
// the map scrolls sideways instead.
const MIN_SCALE = 0.4;
const ZOOM_STEP = 1.5;
const MAX_ZOOM = ZOOM_STEP ** 4;
// Half the popup's w-60, plus a margin, to keep it inside the map.
const POPUP_HALF_WIDTH = 128;
// How far station labels sit above their dot, in pixels.
const LEADER_LENGTH = 18;

// A plain basemap, like caltrain.com's.
const MAP_COLOURS = {
  land: "#ebebeb",
  building: "#dedede",
  buildingEdge: "#d3d3d3",
  roadEdge: "#d6d6e0",
  line: "#d8312c",
};

// Like caltrain.com's live map: grays, yellows and red by service type.
const TRAIN_COLOURS: Record<ServiceType, { label: string; className: string }> =
  {
    local: { label: "Local", className: "bg-[#8a8d8f] text-white" },
    limited: { label: "Limited", className: "bg-[#f5c400] text-[#212529]" },
    express: { label: "Express", className: "bg-[#dd1f29] text-white" },
    weekendLocal: {
      label: "Weekend Local",
      className: "bg-[#8a8d8f] text-white",
    },
    southCounty: {
      label: "South County Connector",
      className: "bg-[#fae4a7] text-[#212529]",
    },
  };

function DirectionArrow({ pointing }: { pointing: "left" | "right" }) {
  return (
    <svg
      viewBox="0 0 10 12"
      aria-hidden="true"
      className={`h-3 w-[10px] shrink-0 ${pointing === "left" ? "-scale-x-100" : ""}`}
    >
      <path
        d="M1 1l8 5-8 5z"
        fill="#dd1f29"
        stroke="white"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function Legend() {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-2">
      {Object.values(TRAIN_COLOURS).map(({ label, className }) => (
        <li key={label} className="flex items-center gap-1.5">
          <span
            className={`size-3 rounded-full shadow-[0_0_0_1px_rgba(0,0,0,0.3)] ${className}`}
          />
          {label}
        </li>
      ))}
      <li className="flex items-center gap-1.5">
        <DirectionArrow pointing="right" />
        Direction of travel
      </li>
      <li className="flex items-center gap-1.5">
        <span className="size-3.5 rounded-full border-2 border-white bg-[#d8312c] shadow-[0_0_0_1.5px_#1a1a1a]" />
        Station
      </li>
    </ul>
  );
}

function describe(train: LiveTrain, stationName: (id: number) => string) {
  const where = train.at_station_id
    ? `at ${stationName(train.at_station_id)}`
    : `approaching ${stationName(train.to_station_id)}`;
  return `Train ${train.train_name} ${train.direction}, ${where}`;
}

function TrainPopup({
  train,
  onClose,
}: {
  train: LiveTrain;
  onClose: () => void;
}) {
  const service = TRAIN_COLOURS[serviceType(train.train_name)];
  return (
    <div
      role="dialog"
      aria-label={`Train ${train.train_name}`}
      className="w-60 overflow-hidden rounded bg-white text-left text-sm shadow-[0_3px_14px_rgba(0,0,0,0.4)]"
    >
      <div
        className={`flex items-center justify-between px-3 py-2 ${service.className}`}
      >
        <p>
          <b>{service.label}</b> · Train {train.train_name}
        </p>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="-mr-1 px-1 text-lg leading-none"
        >
          ×
        </button>
      </div>
      <div className="flex flex-col gap-2 px-3 py-2">
        <p className="capitalize">{train.direction}</p>
        {train.delay_minutes > 0 && (
          <p className="font-medium text-[#dd1f29]">
            Running {train.delay_minutes} min late
          </p>
        )}
        <table className="w-full">
          <caption className="text-left font-bold">Upcoming stops</caption>
          <tbody>
            {train.upcoming_stops.map((stop) => (
              <tr key={stop.station_id} className="border-t border-[#dddddd]">
                <td className="py-1">{stop.station_name}</td>
                <td className="py-1 text-right font-mono">
                  {formatTime(stop.expected_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ZoomButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="flex size-[30px] items-center justify-center bg-white text-lg font-bold hover:bg-[#f4f4f4] disabled:text-[#bbbbbb] disabled:hover:bg-white"
    >
      {children}
    </button>
  );
}

/** The live map: the street, its stations, and the trains on it, refreshed
 * by polling `path`. */
export function TrainMap({
  initial,
  path,
}: {
  initial: LiveMap;
  path: string;
}) {
  const [data, setData] = useState(initial);
  const [stale, setStale] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [zoom, setZoom] = useState(1);
  const [viewWidth, setViewWidth] = useState(1000);
  const viewport = useRef<HTMLDivElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  // The point at the middle of the view before a zoom, as a share of the map's
  // size, so zooming keeps it in the middle.
  const zoomCentre = useRef<{ x: number; y: number } | null>(null);

  useEffect(() => {
    const id = setInterval(async () => {
      try {
        const response = await fetch(path, { cache: "no-store" });
        if (!response.ok) throw new Error(`${response.status}`);
        setData(await response.json());
        setStale(false);
      } catch {
        setStale(true);
      }
    }, POLL_MS);
    return () => clearInterval(id);
  }, [path]);

  useEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) =>
      setViewWidth(entry.contentRect.width),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const street = layOutStreet(data.stations);
  const scale = Math.max(viewWidth / street.width, MIN_SCALE) * zoom;
  const width = Math.max(viewWidth, street.width * scale);
  const height = Math.max(MAP_HEIGHT, (STREET_DEPTH + 80) * scale);
  // Feet on the street to pixels on the map, with the street in the middle.
  const x = (feet: number) => (width - street.width * scale) / 2 + feet * scale;
  const y = (feet: number) =>
    (height - STREET_DEPTH * scale) / 2 + feet * scale;

  useLayoutEffect(() => {
    const element = viewport.current;
    const centre = zoomCentre.current;
    if (!element || centre === null) return;
    element.scrollLeft =
      centre.x * element.scrollWidth - element.clientWidth / 2;
    element.scrollTop =
      centre.y * element.scrollHeight - element.clientHeight / 2;
    zoomCentre.current = null;
  }, [zoom]);

  // On a narrow map, the train's popup can open out of view.
  useEffect(() => {
    popup.current?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [selectedId]);

  function zoomBy(factor: number) {
    const element = viewport.current;
    if (element) {
      zoomCentre.current = {
        x: (element.scrollLeft + element.clientWidth / 2) / element.scrollWidth,
        y:
          (element.scrollTop + element.clientHeight / 2) / element.scrollHeight,
      };
    }
    setZoom((z) => Math.min(Math.max(z * factor, 1), MAX_ZOOM));
  }

  const building = (house: House, top: number) => (
    <rect
      key={`${house.x}-${top}`}
      x={x(house.x - house.width / 2)}
      y={y(top)}
      width={house.width * scale}
      height={house.depth * scale}
      fill={MAP_COLOURS.building}
      stroke={MAP_COLOURS.buildingEdge}
    />
  );
  const trackY = y(ROWS.track);

  const stationName = (id: number) =>
    data.stations.find((s) => s.id === id)?.name ?? "the next station";
  const trains = data.trains.flatMap((train) => {
    const feet = trainX(street, train);
    return feet === null ? [] : [{ train, left: x(feet) }];
  });
  const selected = trains.find(({ train }) => train.train_id === selectedId);

  return (
    <div className="flex flex-col gap-2">
      <div className="relative overflow-hidden border border-[#bcbdbe]">
        <div
          ref={viewport}
          className="overflow-auto overscroll-x-contain"
          style={{ height: MAP_HEIGHT }}
        >
          <div
            role="group"
            aria-label="Live map of the line"
            className="relative"
            style={{ width, height, background: MAP_COLOURS.land }}
            onClick={(event) => {
              // Clicking the map itself closes the popup.
              if (event.target === event.currentTarget) setSelectedId(null);
            }}
          >
            <svg
              width={width}
              height={height}
              aria-hidden="true"
              className="pointer-events-none absolute inset-0"
            >
              {street.houses.map((house) =>
                building(house, ROWS.houseBottom - house.depth),
              )}
              {street.housesAcross.map((house) =>
                building(house, ROWS.housesAcrossTop),
              )}
              <rect
                x={-2}
                y={y(ROWS.roadTop)}
                width={width + 4}
                height={(ROWS.roadBottom - ROWS.roadTop) * scale}
                fill="white"
                stroke={MAP_COLOURS.roadEdge}
                strokeWidth={1.5}
              />
              {STREET_NAME &&
                [0.25, 0.75].map((along) => (
                  <text
                    key={along}
                    x={width * along}
                    y={y((ROWS.roadTop + ROWS.roadBottom) / 2)}
                    textAnchor="middle"
                    dominantBaseline="central"
                    fill="#4a4a4a"
                    fontSize={15}
                  >
                    {STREET_NAME}
                  </text>
                ))}
              {/* The line: red, cased in white and outlined in black. */}
              {[
                { stroke: "#1a1a1a", width: 13 },
                { stroke: "white", width: 10 },
                { stroke: MAP_COLOURS.line, width: 5 },
              ].map((layer) => (
                <line
                  key={layer.stroke}
                  x1={x(street.trackStart)}
                  x2={x(street.trackEnd)}
                  y1={trackY}
                  y2={trackY}
                  stroke={layer.stroke}
                  strokeWidth={layer.width}
                  strokeLinecap="round"
                />
              ))}
              {[...street.stationX.values()].map((feet) => (
                <g key={feet}>
                  <line
                    x1={x(feet)}
                    x2={x(feet)}
                    y1={trackY}
                    y2={trackY - 8 - LEADER_LENGTH}
                    stroke="#1a1a1a"
                    strokeWidth={1.5}
                  />
                  <circle
                    cx={x(feet)}
                    cy={trackY}
                    r={8}
                    fill={MAP_COLOURS.line}
                    stroke="white"
                    strokeWidth={2}
                  />
                  <circle
                    cx={x(feet)}
                    cy={trackY}
                    r={9.5}
                    fill="none"
                    stroke="#1a1a1a"
                    strokeWidth={1.5}
                  />
                </g>
              ))}
            </svg>

            {data.stations.map((station) => {
              const feet = street.stationX.get(station.id);
              if (feet === undefined) return null;
              return (
                <p
                  key={station.id}
                  className="absolute -translate-x-1/2 -translate-y-full rounded-[4px] border-2 border-[#1a1a1a] bg-white px-2.5 py-1 text-sm whitespace-nowrap text-[#1a1a1a] shadow-[0_0_0_3px_white] sm:text-lg"
                  style={{ left: x(feet), top: trackY - 8 - LEADER_LENGTH }}
                >
                  {station.name}
                </p>
              );
            })}

            {trains.map(({ train, left }) => {
              const service = TRAIN_COLOURS[serviceType(train.train_name)];
              const northbound = train.direction === "northbound";
              return (
                <button
                  key={train.train_id}
                  type="button"
                  aria-label={describe(train, stationName)}
                  aria-expanded={selectedId === train.train_id}
                  onClick={() =>
                    setSelectedId((id) =>
                      id === train.train_id ? null : train.train_id,
                    )
                  }
                  className={`absolute flex -translate-x-1/2 -translate-y-1/2 items-center gap-0.5 ${northbound ? "" : "flex-row-reverse"}`}
                  style={{ left, top: trackY }}
                >
                  <span
                    className={`rounded-full border-2 border-white px-2 py-0.5 text-xs font-bold shadow-[0_0_0_1.5px_#1a1a1a] ${service.className}`}
                  >
                    {train.train_name}
                  </span>
                  <DirectionArrow pointing={northbound ? "right" : "left"} />
                </button>
              );
            })}

            {selected && (
              <div
                ref={popup}
                className="absolute z-10 -translate-x-1/2 pt-5"
                style={{
                  left: Math.min(
                    Math.max(selected.left, POPUP_HALF_WIDTH),
                    width - POPUP_HALF_WIDTH,
                  ),
                  top: trackY,
                }}
              >
                <TrainPopup
                  train={selected.train}
                  onClose={() => setSelectedId(null)}
                />
              </div>
            )}
          </div>
        </div>

        <div className="absolute top-3 left-3 flex flex-col divide-y divide-[#cccccc] overflow-hidden rounded border-2 border-black/20 bg-clip-padding">
          <ZoomButton
            label="Zoom in"
            disabled={zoom >= MAX_ZOOM}
            onClick={() => zoomBy(ZOOM_STEP)}
          >
            +
          </ZoomButton>
          <ZoomButton
            label="Zoom out"
            disabled={zoom <= 1}
            onClick={() => zoomBy(1 / ZOOM_STEP)}
          >
            −
          </ZoomButton>
        </div>

        <div className="pointer-events-none absolute top-3 right-3 flex items-center gap-1 rounded bg-white/80 px-2 py-1 text-xs font-bold">
          N <DirectionArrow pointing="right" />
        </div>

        {trains.length === 0 && (
          <p className="pointer-events-none absolute inset-x-0 top-3 mx-auto w-fit rounded bg-white px-3 py-1.5 text-sm shadow">
            No trains are running right now.
          </p>
        )}

        <p className="pointer-events-none absolute right-0 bottom-0 bg-white/70 px-1.5 text-[11px] text-[#515151]">
          Map data © Caltreat contributors
        </p>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 text-sm">
        <Legend />
        <p role="status" className="text-[#515151]">
          {stale
            ? `Couldn't refresh. Showing trains as of ${formatTime(data.as_of)}.`
            : `Updated ${formatTime(data.as_of)}`}
        </p>
      </div>
    </div>
  );
}
