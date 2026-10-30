const timeFormat = new Intl.DateTimeFormat("en-US", {
  hour: "numeric",
  minute: "2-digit",
  timeZone: "America/Los_Angeles",
});

/** A timetable time on the line's clock, e.g. "4:37am". */
export function formatTime(datetime: string) {
  return timeFormat.format(new Date(datetime)).replace(" ", "").toLowerCase();
}

export type ServiceType =
  "local" | "limited" | "express" | "weekendLocal" | "southCounty";

// Caltrain-style colours, with classes written out in full so Tailwind sees
// them.
export const SERVICE_TYPES: Record<
  ServiceType,
  { label: string; className: string }
> = {
  local: {
    label: "1XX Local",
    className: "bg-[#dcddde]",
  },
  limited: {
    label: "4XX Limited",
    className: "bg-[#99d7dc]",
  },
  express: {
    label: "5XX Express",
    className: "bg-[#ce202f] text-white",
  },
  weekendLocal: {
    label: "6XX Weekend Local",
    className: "bg-[#dcddde]",
  },
  southCounty: {
    label: "8XX South County Connector",
    className: "bg-[#fae4a7]",
  },
};

/** Like Caltrain, the train number's hundreds digit gives its service type:
 * 4XX is Limited, 5XX Express, 6XX Weekend Local and 8XX South County
 * Connector. Anything else is shown as Local. */
export function serviceType(trainName: string): ServiceType {
  const match = trainName.trim().match(/^(\d)\d\d$/);
  if (match?.[1] === "4") return "limited";
  if (match?.[1] === "5") return "express";
  if (match?.[1] === "6") return "weekendLocal";
  if (match?.[1] === "8") return "southCounty";
  return "local";
}
