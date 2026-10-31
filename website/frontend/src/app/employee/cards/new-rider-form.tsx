"use client";

import { startTransition, useActionState, useRef, useState } from "react";
import type { ActionState } from "../action-state";
import {
  ActionMessage,
  inputClass,
  mutedClass,
  primaryButtonClass,
  secondaryButtonClass,
} from "../ui";
import { createRider } from "./actions";
import { useCardReader } from "./use-card-reader";

function ReaderStatusLine({
  reader,
}: {
  reader: ReturnType<typeof useCardReader>;
}) {
  const { supported, status, connect } = reader;
  if (!supported) {
    return (
      <p className={`text-sm font-normal ${mutedClass}`}>
        To use the card reader, open this page in Chrome or Edge on a computer.
      </p>
    );
  }
  if (status.kind === "ready") {
    return (
      <p className="text-sm font-normal text-emerald-600">
        Reader connected. Tap a card on it.
      </p>
    );
  }
  if (status.kind === "connecting") {
    return (
      <p className={`text-sm font-normal ${mutedClass}`}>
        Connecting to the reader…
      </p>
    );
  }
  return (
    <div className="flex items-center gap-3 text-sm font-normal">
      <button
        type="button"
        onClick={connect}
        className={`${secondaryButtonClass} py-1`}
      >
        Connect card reader
      </button>
      <span className={status.kind === "error" ? "text-red-600" : mutedClass}>
        {status.kind === "error"
          ? status.message
          : "Plug the reader in, then connect it."}
      </span>
    </div>
  );
}

export function NewRiderForm() {
  const formRef = useRef<HTMLFormElement>(null);
  const [uid, setUid] = useState("");
  const reader = useCardReader(setUid);
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
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-sm font-medium">
          First name
          <input
            name="name"
            required
            placeholder="e.g. Ada"
            className={inputClass}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm font-medium">
          Costume
          <input
            name="costume"
            required
            placeholder="e.g. Witch"
            className={inputClass}
          />
        </label>
      </div>

      <div className="flex flex-col gap-1 text-sm font-medium">
        <label htmlFor="card_uid">Card UID</label>
        <input
          id="card_uid"
          name="card_uid"
          required
          value={uid}
          onChange={(e) => setUid(e.target.value)}
          placeholder="Tap a card on the reader"
          className={`${inputClass} font-mono`}
        />
        <ReaderStatusLine reader={reader} />
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
