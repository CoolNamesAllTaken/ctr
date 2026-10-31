import type { Metadata } from "next";
import { Suspense } from "react";
import { apiFetch } from "@/lib/api";
import type { Rider } from "../types";
import { BackendUnreachable, formatMoney, mutedClass } from "../ui";
import { NewRiderForm } from "./new-rider-form";

export const metadata: Metadata = {
  title: "Chipper cards · Employee portal · ctr",
};

async function RiderList() {
  let riders: Rider[];
  try {
    riders = await apiFetch<Rider[]>("/api/riders/");
  } catch {
    return <BackendUnreachable />;
  }
  if (riders.length === 0) {
    return <p className={mutedClass}>No riders yet.</p>;
  }

  return (
    <table className="w-full text-left">
      <thead className="text-sm text-zinc-500">
        <tr className="border-b border-zinc-200 dark:border-zinc-800">
          <th className="py-2 font-medium">Rider</th>
          <th className="py-2 font-medium">Costume</th>
          <th className="py-2 font-medium">Card UID</th>
          <th className="py-2 text-right font-medium">Balance</th>
        </tr>
      </thead>
      <tbody>
        {riders.map((rider) => (
          <tr
            key={rider.id}
            className="border-b border-zinc-100 dark:border-zinc-900"
          >
            <td className="py-2">{rider.name}</td>
            <td className="py-2">{rider.costume}</td>
            <td className="py-2 font-mono text-sm">
              {rider.card_uid ?? <span className="text-zinc-500">No card</span>}
            </td>
            <td className="py-2 text-right tabular-nums">
              {rider.balance ? formatMoney(rider.balance) : "—"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function CardsPage() {
  return (
    <>
      <section className="flex flex-col gap-4">
        <h2 className="text-xl font-semibold">Set up a Chipper card</h2>
        <p className={mutedClass}>
          Creates a new rider and gives them a new card. Tap the card on the
          reader to fill in its UID, or generate one for a pretend card.
        </p>
        <NewRiderForm />
      </section>

      <section className="flex flex-col gap-4">
        <h2 className="text-xl font-semibold">Riders</h2>
        <Suspense fallback={<p className="text-zinc-500">Loading…</p>}>
          <RiderList />
        </Suspense>
      </section>
    </>
  );
}
