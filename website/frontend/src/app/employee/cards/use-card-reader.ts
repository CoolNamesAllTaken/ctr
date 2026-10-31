import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import {
  CardReader,
  getSerial,
  isReaderPort,
  READER_FILTER,
  type SerialPort,
} from "./pn532";

export type ReaderStatus =
  | { kind: "disconnected" }
  | { kind: "connecting" }
  | { kind: "ready" }
  | { kind: "error"; message: string };

const POLL_MS = 250;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const noSubscribe = () => () => {};

/**
 * Watches the USB card reader and calls onCard with each newly tapped card's
 * UID. Reconnects by itself to a reader the browser has been allowed to use
 * before; connect() asks for one the first time.
 */
export function useCardReader(onCard: (uid: string) => void) {
  // Assumed on the server, so the page renders the same until it hydrates.
  const supported = useSyncExternalStore(
    noSubscribe,
    () => getSerial() !== undefined,
    () => true,
  );
  const [status, setStatus] = useState<ReaderStatus>({ kind: "disconnected" });
  const onCardRef = useRef(onCard);
  useEffect(() => {
    onCardRef.current = onCard;
  });

  // The latest poll loop. Each one waits for the one before to close its port,
  // since a port can only be open once at a time.
  const session = useRef<{
    port: SerialPort;
    stop: AbortController;
    done: Promise<void>;
  } | null>(null);

  const stop = useCallback(() => session.current?.stop.abort(), []);

  const start = useCallback((port: SerialPort) => {
    const previous = session.current;
    previous?.stop.abort();
    const abort = new AbortController();
    const report = (s: ReaderStatus) => {
      if (!abort.signal.aborted) setStatus(s);
    };
    const done = (async () => {
      await previous?.done;
      if (abort.signal.aborted) return;
      report({ kind: "connecting" });
      let reader: CardReader | null = null;
      try {
        reader = await CardReader.open(port);
        report({ kind: "ready" });
        let last: string | null = null;
        while (!abort.signal.aborted) {
          const uid = await reader.readUid();
          if (uid && uid !== last) onCardRef.current(uid);
          last = uid;
          await sleep(POLL_MS);
        }
      } catch (error) {
        if (abort.signal.aborted) return;
        console.error(error);
        report({
          kind: "error",
          message:
            error instanceof Error && error.name === "InvalidStateError"
              ? "The reader is in use, maybe by another tab."
              : "Lost the card reader.",
        });
      } finally {
        await reader?.close();
      }
    })();
    session.current = { port, stop: abort, done };
  }, []);

  useEffect(() => {
    const serial = getSerial();
    if (!serial) return;
    let unmounted = false;

    serial.getPorts().then((ports) => {
      const port = ports.find(isReaderPort);
      if (port && !unmounted) start(port);
    });

    const onConnect = (e: Event) => {
      const port = e.target as SerialPort;
      const running = session.current && !session.current.stop.signal.aborted;
      if (isReaderPort(port) && !running) start(port);
    };
    const onDisconnect = (e: Event) => {
      if (e.target !== session.current?.port) return;
      stop();
      setStatus({ kind: "disconnected" });
    };
    serial.addEventListener("connect", onConnect);
    serial.addEventListener("disconnect", onDisconnect);
    return () => {
      unmounted = true;
      serial.removeEventListener("connect", onConnect);
      serial.removeEventListener("disconnect", onDisconnect);
      stop();
    };
  }, [start, stop]);

  /** Asks the employee to pick the reader, then starts watching it. */
  const connect = useCallback(async () => {
    try {
      start(await getSerial()!.requestPort({ filters: [READER_FILTER] }));
    } catch (error) {
      // NotFoundError: the employee closed the picker without choosing.
      if (!(error instanceof Error && error.name === "NotFoundError")) {
        console.error(error);
      }
    }
  }, [start]);

  return { supported, status, connect };
}
