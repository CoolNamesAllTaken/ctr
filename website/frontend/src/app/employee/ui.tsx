// Shared bits for the employee portal's forms and pages.

import type { ActionState } from "./action-state";

export const inputClass =
  "rounded-md border border-zinc-300 bg-transparent px-3 py-2 dark:border-zinc-700";

export const primaryButtonClass =
  "rounded-md bg-zinc-900 px-4 py-2 font-medium text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900";

export const secondaryButtonClass =
  "rounded-md border border-zinc-300 px-4 py-2 font-medium disabled:opacity-50 dark:border-zinc-700";

export const mutedClass = "text-zinc-600 dark:text-zinc-400";

export function ActionMessage({ state }: { state: ActionState }) {
  return (
    <p
      aria-live="polite"
      className={state.status === "error" ? "text-red-600" : "text-emerald-600"}
    >
      {state.status !== "idle" && state.message}
    </p>
  );
}

export function BackendUnreachable() {
  return <p className="text-red-600">Couldn&apos;t reach the backend.</p>;
}

export function formatMoney(amount: string) {
  const value = Number(amount);
  return `${value < 0 ? "-" : ""}$${Math.abs(value).toFixed(2)}`;
}

export function formatRides(rides: number) {
  return `${rides} ride${rides === 1 ? "" : "s"}`;
}
