"use client";

import { useActionState } from "react";
import type { ActionState } from "../action-state";
import { ActionMessage, secondaryButtonClass } from "../ui";
import { setDelay } from "./actions";

/** Puts the whole of today's run of a train back on time. */
export function ClearDelayButton({ trainId }: { trainId: number }) {
  const [state, formAction, pending] = useActionState<ActionState, FormData>(
    setDelay,
    { status: "idle" },
  );

  return (
    <form action={formAction} className="flex items-center gap-4">
      <input type="hidden" name="train_id" value={trainId} />
      <input type="hidden" name="delay_minutes" value="0" />
      <button type="submit" disabled={pending} className={secondaryButtonClass}>
        {pending ? "Clearing…" : "Clear delay"}
      </button>
      {state.status === "error" && <ActionMessage state={state} />}
    </form>
  );
}
