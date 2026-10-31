import type { Metadata } from "next";
import { Suspense } from "react";
import type { LiveMap } from "@/app/employee/types";
import { apiFetch } from "@/lib/api";
import { TrainMap } from "./train-map";

export const metadata: Metadata = { title: "Live Map · Caltreat" };

// Styled after caltrain.com's Live Map tab. Add `?at=<ISO datetime>` to see the
// trains at another time today.

async function LiveMapSection({
  searchParams,
}: {
  searchParams: PageProps<"/map">["searchParams"];
}) {
  const { at } = await searchParams;
  const path =
    typeof at === "string"
      ? `/api/live-map/?${new URLSearchParams({ at })}`
      : "/api/live-map/";

  let initial: LiveMap;
  try {
    initial = await apiFetch<LiveMap>(path);
  } catch {
    return (
      <p className="text-lg text-[#dd1f29]">
        The live map isn&apos;t available right now. Please try again shortly.
      </p>
    );
  }
  return <TrainMap initial={initial} path={path} />;
}

export default function MapPage({ searchParams }: PageProps<"/map">) {
  return (
    <div className="mx-auto flex w-full max-w-[1196px] flex-1 flex-col gap-5 bg-[#f6f6f6] px-4 py-6 sm:px-[34px]">
      <h1 className="sr-only">Live Map</h1>
      <Suspense fallback={<p className="text-lg">Loading the live map…</p>}>
        <LiveMapSection searchParams={searchParams} />
      </Suspense>
      <p className="text-lg">
        Trains are shown in gray, blue, yellow and red by service type, with a
        red arrow showing their direction of travel. Select a train to see its
        upcoming stops. Use the +/− buttons to zoom.
      </p>
    </div>
  );
}
