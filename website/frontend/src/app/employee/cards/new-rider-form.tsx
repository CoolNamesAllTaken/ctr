"use client";

import { startTransition, useActionState, useRef, useState } from "react";
import type { ActionState } from "../action-state";
import {
  ActionMessage,
  inputClass,
  primaryButtonClass,
  secondaryButtonClass,
} from "../ui";
import { createRider } from "./actions";

/** A random 7-byte UID, like an NXP NFC tag's (they start with 04). */
function randomUid() {
  const bytes = crypto.getRandomValues(new Uint8Array(6));
  return "04" + [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function NewRiderForm() {
  const formRef = useRef<HTMLFormElement>(null);
  const [uid, setUid] = useState("");
  const [state, formAction, pending] = useActionState<ActionState, FormData>(
    async (prev, formData) => {
      const result = await createRider(prev, formData);
      if (result.status === "success") {
        formRef.current?.reset();
        setUid("");
      }
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
      <div className="grid gap-4 sm:grid-cols-[1fr_1.4fr_auto]">
        <label className="flex flex-col gap-1 text-sm font-medium">
          Rider name
          <input
            name="name"
            required
            placeholder="e.g. Ada"
            className={inputClass}
          />
        </label>
        <div className="flex flex-col gap-1 text-sm font-medium">
          <label htmlFor="card_uid">Card UID</label>
          <div className="flex gap-2">
            <input
              id="card_uid"
              name="card_uid"
              required
              value={uid}
              onChange={(e) => setUid(e.target.value)}
              placeholder="e.g. 04a1b2c3d4e5f6"
              className={`${inputClass} min-w-0 flex-1 font-mono`}
            />
            <button
              type="button"
              onClick={() => setUid(randomUid())}
              className={secondaryButtonClass}
            >
              Generate
            </button>
          </div>
        </div>
        <label className="flex flex-col gap-1 text-sm font-medium">
          Starting balance ($)
          <input
            name="balance"
            type="number"
            required
            min="0"
            step="0.01"
            defaultValue="20.00"
            className={`${inputClass} sm:w-32`}
          />
        </label>
      </div>

      <div className="flex items-center gap-4">
        <button type="submit" disabled={pending} className={primaryButtonClass}>
          {pending ? "Setting up…" : "Set up card"}
        </button>
        <ActionMessage state={state} />
      </div>
    </form>
  );
}
