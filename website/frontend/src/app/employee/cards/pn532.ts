// Reads card UIDs from the USB NFC reader (firmware/caltreat_usb_nfc) over Web
// Serial. The reader speaks the PN532 serial protocol; this is just enough of
// it to ask "which card is on the reader?".

// The bits of Web Serial used here; TypeScript's DOM types don't include it.
export type SerialPort = EventTarget & {
  open(options: { baudRate: number }): Promise<void>;
  close(): Promise<void>;
  readonly readable: ReadableStream<Uint8Array> | null;
  readonly writable: WritableStream<Uint8Array> | null;
  getInfo(): { usbVendorId?: number; usbProductId?: number };
};

type Serial = EventTarget & {
  getPorts(): Promise<SerialPort[]>;
  requestPort(options?: {
    filters: { usbVendorId: number; usbProductId?: number }[];
  }): Promise<SerialPort>;
};

/** Web Serial, if the browser has it (Chrome and Edge on desktop). */
export function getSerial(): Serial | undefined {
  return (navigator as Navigator & { serial?: Serial }).serial;
}

/** The reader's USB IDs (the Pico SDK's defaults). */
export const READER_FILTER = { usbVendorId: 0x2e8a, usbProductId: 0x000b };

export function isReaderPort(port: SerialPort) {
  const { usbVendorId, usbProductId } = port.getInfo();
  return (
    usbVendorId === READER_FILTER.usbVendorId &&
    usbProductId === READER_FILTER.usbProductId
  );
}

const CMD_GET_FIRMWARE_VERSION = 0x02;
const CMD_SAM_CONFIGURATION = 0x14;
const CMD_RF_CONFIGURATION = 0x32;
const CMD_IN_LIST_PASSIVE_TARGET = 0x4a;

const TIMEOUT_MS = 1000;

/** A host-to-reader frame: 00 00 FF LEN LCS D4 CMD DATA… DCS 00. */
function frame(cmd: number, data: number[]) {
  const body = [0xd4, cmd, ...data];
  const sum = body.reduce((a, b) => a + b, 0);
  return new Uint8Array([
    0x00,
    0x00,
    0xff,
    body.length,
    -body.length & 0xff,
    ...body,
    -sum & 0xff,
    0x00,
  ]);
}

export class CardReader {
  private buffer = new Uint8Array(0);
  private pendingRead: Promise<ReadableStreamReadResult<Uint8Array>> | null =
    null;

  private constructor(
    private port: SerialPort,
    private reader: ReadableStreamDefaultReader<Uint8Array>,
    private writer: WritableStreamDefaultWriter<Uint8Array>,
  ) {}

  /** Opens the port and sets the reader up for quick card polls. */
  static async open(port: SerialPort) {
    // USB serial ignores the baud rate, but never use 1200: opening the port
    // at 1200 baud reboots the reader into its bootloader.
    await port.open({ baudRate: 115200 });
    const reader = new CardReader(
      port,
      port.readable!.getReader(),
      port.writable!.getWriter(),
    );
    try {
      await reader.command(CMD_GET_FIRMWARE_VERSION);
      // Normal mode, no SAM.
      await reader.command(CMD_SAM_CONFIGURATION, [0x01, 0x14, 0x01]);
      // MaxRetries: look for a card twice and answer, rather than wait for one.
      await reader.command(CMD_RF_CONFIGURATION, [0x05, 0xff, 0x01, 0x01]);
    } catch (error) {
      await reader.close();
      throw error;
    }
    return reader;
  }

  /** The UID of the card on the reader as lowercase hex, or null if none. */
  async readUid(): Promise<string | null> {
    // One target, 106 kbps type A.
    const data = await this.command(CMD_IN_LIST_PASSIVE_TARGET, [0x01, 0x00]);
    // NbTg, Tg, SENS_RES (2), SEL_RES, NFCIDLength, NFCID…
    if (data[0] === 0) return null;
    const uid = data.subarray(6, 6 + data[5]);
    return [...uid].map((b) => b.toString(16).padStart(2, "0")).join("");
  }

  async close() {
    await this.reader.cancel().catch(() => {});
    this.reader.releaseLock();
    this.writer.releaseLock();
    await this.port.close().catch(() => {});
  }

  /** Sends a command and returns its response data. */
  private async command(cmd: number, data: number[] = []) {
    await this.writer.write(frame(cmd, data));
    const deadline = Date.now() + TIMEOUT_MS;
    // The reader ACKs the command, then answers it.
    let body = await this.readFrame(deadline);
    if (body.length === 0) body = await this.readFrame(deadline);
    if (body[0] !== 0xd5 || body[1] !== cmd + 1) {
      throw new Error(`Unexpected reply to reader command ${cmd}`);
    }
    return body.subarray(2);
  }

  /** The next frame's TFI + data, or an empty array for an ACK. */
  private async readFrame(deadline: number): Promise<Uint8Array> {
    for (;;) {
      const buf = this.buffer;
      // Skip the preamble (and any stray bytes) up to the 00 FF start code.
      let start = 0;
      while (
        start + 1 < buf.length &&
        !(buf[start] === 0 && buf[start + 1] === 0xff)
      ) {
        start++;
      }
      if (start + 4 <= buf.length) {
        const len = buf[start + 2];
        const lcs = buf[start + 3];
        if (len === 0x00 && lcs === 0xff) {
          this.buffer = buf.subarray(start + 4);
          return new Uint8Array(0);
        }
        if (((len + lcs) & 0xff) !== 0)
          throw new Error("Bad frame from reader");
        const end = start + 4 + len;
        if (end + 1 <= buf.length) {
          const body = buf.slice(start + 4, end);
          const sum = body.reduce((a, b) => a + b, buf[end]);
          this.buffer = buf.subarray(end + 1);
          if ((sum & 0xff) !== 0) throw new Error("Bad frame from reader");
          if (body[0] === 0x7f) throw new Error("Reader rejected the command");
          return body;
        }
      }
      await this.fill(deadline);
    }
  }

  /** Waits for more bytes from the port. */
  private async fill(deadline: number) {
    this.pendingRead ??= this.reader.read();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(
        () => reject(new Error("The reader didn't answer")),
        deadline - Date.now(),
      );
    });
    try {
      const { value, done } = await Promise.race([this.pendingRead, timeout]);
      this.pendingRead = null;
      if (done) throw new Error("The reader was disconnected");
      const merged = new Uint8Array(this.buffer.length + value.length);
      merged.set(this.buffer);
      merged.set(value, this.buffer.length);
      this.buffer = merged;
    } finally {
      clearTimeout(timer);
    }
  }
}
