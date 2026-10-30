const timeFormat = new Intl.DateTimeFormat("en-US", {
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZone: "America/Los_Angeles",
});

/** "HH:MM" on the line's clock, matching the schedule. */
export function formatTime(datetime: string) {
  return timeFormat.format(new Date(datetime));
}
