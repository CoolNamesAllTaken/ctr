"use client";

import { useEffect, useState } from "react";

/** Shows one delay at a time, moving on every few seconds, like the original. */
export function DelayTicker({ messages }: { messages: string[] }) {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    if (messages.length < 2) return;
    const id = setInterval(
      () => setIndex((i) => (i + 1) % messages.length),
      5000,
    );
    return () => clearInterval(id);
  }, [messages.length]);

  return (
    <p className="bg-[#f4f4f4] px-[10px] py-[9px] text-lg">
      {messages[index % messages.length] ??
        "There are no real-time alerts at this time."}
    </p>
  );
}
