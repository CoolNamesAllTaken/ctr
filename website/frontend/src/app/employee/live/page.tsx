import type { Metadata } from "next";
import { Suspense } from "react";
import { apiFetch } from "@/lib/api";
import type { Dashboard } from "../types";
import { BackendUnreachable } from "../ui";
import { LiveDashboard } from "./live-dashboard";
import { dashboardPath } from "./path";

export const metadata: Metadata = { title: "Live · Employee portal · ctr" };

// Add `?at=<ISO datetime>` to see the dashboard at another time today.

async function LiveSection({
  searchParams,
}: {
  searchParams: PageProps<"/employee/live">["searchParams"];
}) {
  const { at } = await searchParams;
  const atParam = typeof at === "string" ? at : null;
  let initial: Dashboard;
  try {
    initial = await apiFetch<Dashboard>(dashboardPath(atParam));
  } catch {
    return <BackendUnreachable />;
  }
  const pollPath = atParam
    ? `/employee/live/data?${new URLSearchParams({ at: atParam })}`
    : "/employee/live/data";
  return <LiveDashboard initial={initial} pollPath={pollPath} />;
}

export default function LivePage({
  searchParams,
}: PageProps<"/employee/live">) {
  return (
    <Suspense fallback={<p className="text-zinc-500">Loading…</p>}>
      <LiveSection searchParams={searchParams} />
    </Suspense>
  );
}
