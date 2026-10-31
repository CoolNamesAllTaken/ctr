"use client";

import { startTransition, useActionState } from "react";
import type { ActionState } from "../action-state";
import { ActionMessage, inputClass, primaryButtonClass } from "../ui";
import { logIn } from "./actions";

export function LoginForm() {
  const [state, formAction, pending] = useActionState<ActionState, FormData>(
    logIn,
    { status: "idle" },
  );

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        const formData = new FormData(e.currentTarget);
        // The page src/proxy.ts sent them here from.
        const next = new URLSearchParams(window.location.search).get("next");
        formData.set("next", next ?? "");
        startTransition(() => formAction(formData));
      }}
      className="flex flex-col gap-5 sm:w-1/2"
    >
      <label className="flex flex-col gap-1 text-sm font-medium">
        Passphrase
        <input
          name="passphrase"
          type="password"
          required
          autoFocus
          autoComplete="current-password"
          className={inputClass}
        />
      </label>

      <div className="flex items-center gap-4">
        <button type="submit" disabled={pending} className={primaryButtonClass}>
          {pending ? "Logging in…" : "Log in"}
        </button>
        <ActionMessage state={state} />
      </div>
    </form>
  );
}
