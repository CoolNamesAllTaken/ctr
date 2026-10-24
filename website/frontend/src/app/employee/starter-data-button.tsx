"use client";

import { useActionState } from "react";
import { type ActionState, loadStarterData } from "./actions";

export function StarterDataButton() {
  const [state, formAction, pending] = useActionState<ActionState>(
    loadStarterData,
    { status: "idle" },
  );

  return (
    <form action={formAction} className="flex items-center gap-4">
      <button
        type="submit"
        disabled={pending}
        className="rounded-md border border-zinc-300 px-4 py-2 font-medium disabled:opacity-50 dark:border-zinc-700"
      >
        {pending ? "Loading…" : "Load starter data"}
      </button>
      <p
        aria-live="polite"
        className={
          state.status === "error" ? "text-red-600" : "text-emerald-600"
        }
      >
        {state.status !== "idle" && state.message}
      </p>
    </form>
  );
}
