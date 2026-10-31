"use client";

import { startTransition, useActionState } from "react";
import type { ActionState } from "../action-state";
import { ActionMessage, secondaryButtonClass } from "../ui";
import { cancelAlert } from "./actions";

/** Takes an alert down, so riders no longer see it. */
export function CancelAlertButton({ alertId }: { alertId: number }) {
  const [state, formAction, pending] = useActionState<ActionState, FormData>(
    cancelAlert,
    { status: "idle" },
  );

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (!window.confirm("Cancel this alert? Riders will stop seeing it."))
          return;
        const formData = new FormData(e.currentTarget);
        startTransition(() => formAction(formData));
      }}
      className="flex flex-col items-end gap-1"
    >
      <input type="hidden" name="id" value={alertId} />
      <button
        type="submit"
        disabled={pending}
        className={`${secondaryButtonClass} py-1 text-sm`}
      >
        {pending ? "Canceling…" : "Cancel"}
      </button>
      {state.status === "error" && <ActionMessage state={state} />}
    </form>
  );
}
