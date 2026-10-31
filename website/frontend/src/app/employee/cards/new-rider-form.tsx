"use client";

import { startTransition, useActionState, useRef, useState } from "react";
import type { ActionState } from "../action-state";
import {
  ActionMessage,
  formatRides,
  inputClass,
  mutedClass,
  primaryButtonClass,
  secondaryButtonClass,
} from "../ui";
import { cardRiderName, createRider } from "./actions";
import { type TappedCard, useCardReader } from "./use-card-reader";

type Reader = ReturnType<typeof useCardReader>;

function ReaderStatusLine({ reader }: { reader: Reader }) {
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

function CardStatusLine({ card }: { card: TappedCard | null }) {
  if (!card) return null;
  const { info } = card;
  if (info.kind === "unusable") {
    return <p className="text-sm font-normal text-red-600">{info.message}</p>;
  }
  return (
    <p className={`text-sm font-normal ${mutedClass}`}>
      {info.kind === "blank" && "Blank card."}
      {info.kind === "chipper" &&
        `Chipper card with ${formatRides(info.rides)} left.`}
      {info.kind === "damaged" &&
        "Chipper card with a damaged ride block. It will be rewritten."}
    </p>
  );
}

function RidesInput({ className = "" }: { className?: string }) {
  return (
    <label className="flex flex-col gap-1 text-sm font-medium">
      Rides
      <input
        name="rides"
        type="number"
        required
        min="1"
        max="255"
        step="1"
        defaultValue="1"
        className={`${inputClass} ${className}`}
      />
    </label>
  );
}

/** Asks before overwriting a Chipper card that still holds rides. */
function confirmOverwrite(card: TappedCard, rides: number) {
  return (
    card.info.kind !== "chipper" ||
    confirm(
      `This card holds ${formatRides(card.info.rides)}. Replace them with ${formatRides(rides)}?`,
    )
  );
}

const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : "Couldn't write the card.";

/**
 * Writes a card's rides (firmware/docs/chipper_card.md), then registers it to a
 * new rider.
 */
function NewRiderForm({
  reader,
  card,
}: {
  reader: Reader;
  card: TappedCard | null;
}) {
  const formRef = useRef<HTMLFormElement>(null);
  const [writeState, setWriteState] = useState<ActionState>({
    status: "idle",
  });
  const [writing, setWriting] = useState(false);
  const [state, formAction, pending] = useActionState<ActionState, FormData>(
    async (prev, formData) => {
      const result = await createRider(prev, formData);
      if (result.status === "success") formRef.current?.reset();
      return result;
    },
    { status: "idle" },
  );

  const fail = (message: string) => setWriteState({ status: "error", message });

  // Submitted via onSubmit rather than action={formAction}, so React doesn't
  // reset the form after an error and wipe what the employee typed.
  async function submit(formData: FormData) {
    setWriteState({ status: "idle" });
    if (!card) return fail("Tap a card on the reader first.");
    if (card.info.kind === "unusable") return fail(card.info.message);
    const rides = Number(formData.get("rides"));
    setWriting(true);
    try {
      const owner = await cardRiderName(card.uid);
      if (owner !== null) {
        return fail(
          `This card is already set up for ${owner}. Use Reload card instead.`,
        );
      }
      if (!confirmOverwrite(card, rides)) return;
      // The card must hold its rides before the rider is registered to it.
      await reader.writeRides(card.uid, rides);
    } catch (error) {
      return fail(errorMessage(error));
    } finally {
      setWriting(false);
    }
    startTransition(() => formAction(formData));
  }

  const busy = writing || pending;
  return (
    <form
      ref={formRef}
      onSubmit={(e) => {
        e.preventDefault();
        submit(new FormData(e.currentTarget));
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

      <div className="grid gap-4 sm:grid-cols-[1fr_auto]">
        <div className="flex flex-col gap-1 text-sm font-medium">
          <label htmlFor="card_uid">Card UID</label>
          <input
            id="card_uid"
            name="card_uid"
            readOnly
            value={card?.uid ?? ""}
            placeholder="Tap a card on the reader"
            className={`${inputClass} font-mono`}
          />
          <CardStatusLine card={card} />
          <ReaderStatusLine reader={reader} />
        </div>
        <RidesInput className="sm:w-32" />
      </div>

      <div className="flex items-center gap-4">
        <button type="submit" disabled={busy} className={primaryButtonClass}>
          {writing ? "Writing card…" : pending ? "Setting up…" : "Set up card"}
        </button>
        <ActionMessage
          state={writeState.status !== "idle" ? writeState : state}
        />
      </div>
    </form>
  );
}

/** Writes rides to the card on the reader without touching the backend. */
function ReloadCardForm({
  reader,
  card,
}: {
  reader: Reader;
  card: TappedCard | null;
}) {
  const [state, setState] = useState<ActionState>({ status: "idle" });
  const [writing, setWriting] = useState(false);

  async function reload(rides: number) {
    setState({ status: "idle" });
    if (!card) {
      return setState({
        status: "error",
        message: "Tap a card on the reader first.",
      });
    }
    if (card.info.kind === "unusable") {
      return setState({ status: "error", message: card.info.message });
    }
    if (!confirmOverwrite(card, rides)) return;
    setWriting(true);
    try {
      await reader.writeRides(card.uid, rides);
      setState({
        status: "success",
        message: `Card ${card.uid} now has ${formatRides(rides)}.`,
      });
    } catch (error) {
      setState({ status: "error", message: errorMessage(error) });
    } finally {
      setWriting(false);
    }
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        reload(Number(new FormData(e.currentTarget).get("rides")));
      }}
      className="flex flex-col gap-3"
    >
      <h3 className="font-semibold">Reload a card</h3>
      <p className={`text-sm ${mutedClass}`}>
        Gives the card on the reader a fresh set of rides, for a rider who
        already has one.
      </p>
      <div className="flex flex-wrap items-end gap-4">
        <RidesInput className="w-32" />
        <button
          type="submit"
          disabled={writing}
          className={secondaryButtonClass}
        >
          {writing ? "Writing card…" : "Reload card"}
        </button>
        <ActionMessage state={state} />
      </div>
    </form>
  );
}

/** Both card forms, sharing the one connection to the card reader. */
export function CardForms() {
  const [card, setCard] = useState<TappedCard | null>(null);
  const reader = useCardReader(setCard);
  return (
    <div className="flex flex-col gap-8">
      <NewRiderForm reader={reader} card={card} />
      <ReloadCardForm reader={reader} card={card} />
    </div>
  );
}
