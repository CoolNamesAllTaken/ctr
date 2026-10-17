"use client";

import { startTransition, useActionState, useRef } from "react";
import type { ActionState } from "../action-state";
import type { Train } from "../types";
import { ActionMessage, inputClass, primaryButtonClass } from "../ui";
import { issueAlert } from "./actions";

export function AlertForm({ trains }: { trains: Train[] }) {
  const formRef = useRef<HTMLFormElement>(null);
  const [state, formAction, pending] = useActionState<ActionState, FormData>(
    async (prev, formData) => {
      const result = await issueAlert(prev, formData);
      if (result.status === "success") formRef.current?.reset();
      return result;
    },
    { status: "idle" },
  );

  return (
    <form
      ref={formRef}
      // Submit via onSubmit rather than action={formAction}, so React doesn't
      // reset the form after an error and wipe what the employee typed.
      onSubmit={(e) => {
        e.preventDefault();
        const formData = new FormData(e.currentTarget);
        startTransition(() => formAction(formData));
      }}
      className="flex flex-col gap-5"
    >
      <label className="flex flex-col gap-1 text-sm font-medium sm:w-1/2">
        Train
        <select name="train_id" className={inputClass}>
          <option value="">Whole line</option>
          {trains.map((train) => (
            <option key={train.id} value={train.id}>
              {train.name}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-sm font-medium">
        Message
        <textarea
          name="message"
          required
          rows={3}
          placeholder="e.g. Running about 10 minutes late due to a signal problem."
          className={inputClass}
        />
      </label>

      <div className="flex items-center gap-4">
        <button type="submit" disabled={pending} className={primaryButtonClass}>
          {pending ? "Issuing…" : "Issue alert"}
        </button>
        <ActionMessage state={state} />
      </div>
    </form>
  );
}
