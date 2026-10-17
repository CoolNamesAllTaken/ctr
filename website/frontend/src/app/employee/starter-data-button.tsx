"use client";

import { useActionState } from "react";
import type { ActionState } from "./action-state";
import { loadStarterData } from "./actions";
import { ActionMessage, secondaryButtonClass } from "./ui";

export function StarterDataButton() {
  const [state, formAction, pending] = useActionState<ActionState>(
    loadStarterData,
    { status: "idle" },
  );

  return (
    <form action={formAction} className="flex items-center gap-4">
      <button type="submit" disabled={pending} className={secondaryButtonClass}>
        {pending ? "Loading…" : "Load starter data"}
      </button>
      <ActionMessage state={state} />
    </form>
  );
}
