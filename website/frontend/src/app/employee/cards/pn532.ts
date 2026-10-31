// Reads card UIDs from the USB NFC reader (firmware/caltreat_usb_nfc) over Web
// Serial. The reader speaks the PN532 serial protocol; this is just enough of
// it to find the card on the reader and read or write its Chipper ride block.

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
const CMD_IN_DATA_EXCHANGE = 0x40;

const TIMEOUT_MS = 1000;

// The Chipper ride block (spec: firmware/docs/chipper_card.md): sector 1,
// block 4, factory keys. "CHPR", version 2, half rides left, 8 zero bytes,
// CRC_A of bytes 0-13 (low byte first). Tapping on and off each take half a
// ride, so an odd count means the rider is mid-trip.
const CHIPPER_BLOCK = 4;
const FACTORY_KEY = [0xff, 0xff, 0xff, 0xff, 0xff, 0xff];
const MAGIC = [0x43, 0x48, 0x50, 0x52];
const VERSION = 2;
// v1 stored whole rides. The portal re-issues those cards like blank ones.
const V1 = 1;

/** The most whole rides a card can hold (twice this has to fit in a byte). */
export const MAX_RIDES = 127;
const MIFARE_AUTH_A = 0x60;
const MIFARE_READ = 0x30;
const MIFARE_WRITE = 0xa0;
const SAK_CLASSIC_1K = 0x08;

/** What's on a card, as far as Chipper is concerned. */
export type CardInfo =
  | { kind: "blank"; v1?: true }
  | { kind: "chipper"; rides: number; tappedOn: boolean }
  | { kind: "damaged" }
  | { kind: "unusable"; message: string };

/** A problem with the card (not the reader), worded for the employee. */
export class CardError extends Error {}

/** CRC-16/ISO-IEC-14443-3-A; "123456789" -> 0xBF05. */
export function crcA(data: Uint8Array): number {
  let crc = 0x6363;
  for (const byte of data) {
    let b = (byte ^ crc) & 0xff;
    b = (b ^ (b << 4)) & 0xff;
    crc = ((crc >> 8) ^ (b << 8) ^ (b << 3) ^ (b >> 4)) & 0xffff;
  }
  return crc;
}

/** A valid ride block holding the given whole rides (0 to MAX_RIDES). */
export function makeChipperBlock(rides: number): Uint8Array {
  const block = new Uint8Array(16);
  block.set(MAGIC);
  block[4] = VERSION;
  block[5] = 2 * rides;
  const crc = crcA(block.subarray(0, 14));
  block[14] = crc & 0xff;
  block[15] = crc >> 8;
  return block;
}

export function parseChipperBlock(block: Uint8Array): CardInfo {
  if (!MAGIC.every((b, i) => block[i] === b)) return { kind: "blank" };
  if (block[4] === V1) return { kind: "blank", v1: true };
  if (block[4] !== VERSION) return { kind: "blank" };
  const crc = crcA(block.subarray(0, 14));
  if (block[14] !== (crc & 0xff) || block[15] !== crc >> 8) {
    return { kind: "damaged" };
  }
  return {
    kind: "chipper",
    rides: block[5] >> 1,
    tappedOn: (block[5] & 1) === 1,
  };
}

const toHex = (bytes: Uint8Array) =>
  [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");

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
    const target = await this.select();
    return target && toHex(target.uid);
  }

  /** Reads the card's ride block. Problems with the card come back as info. */
  async readChipper(uid: string): Promise<CardInfo> {
    try {
      await this.openBlock(uid);
      const data = await this.exchange([MIFARE_READ, CHIPPER_BLOCK]);
      return parseChipperBlock(data.subarray(0, 16));
    } catch (error) {
      if (error instanceof CardError) {
        return { kind: "unusable", message: error.message };
      }
      throw error;
    }
  }

  /** Writes a ride block and reads it back. Throws CardError if it didn't take. */
  async writeChipper(uid: string, rides: number) {
    const block = makeChipperBlock(rides);
    await this.openBlock(uid);
    await this.exchange([MIFARE_WRITE, CHIPPER_BLOCK, ...block]);
    // The card drops out of the authenticated state after the write, so
    // select it again to check what it kept.
    await this.openBlock(uid);
    const data = await this.exchange([MIFARE_READ, CHIPPER_BLOCK]);
    if (!block.every((b, i) => data[i] === b)) {
      throw new CardError("The card didn't keep the write. Tap it again.");
    }
  }

  async close() {
    await this.reader.cancel().catch(() => {});
    this.reader.releaseLock();
    this.writer.releaseLock();
    await this.port.close().catch(() => {});
  }

  /** The card on the reader, or null if none. */
  private async select() {
    // One target, 106 kbps type A.
    const data = await this.command(CMD_IN_LIST_PASSIVE_TARGET, [0x01, 0x00]);
    // NbTg, Tg, SENS_RES (2), SEL_RES, NFCIDLength, NFCID…
    if (data[0] === 0) return null;
    return { sak: data[4], uid: data.slice(6, 6 + data[5]) };
  }

  /** Selects the card with this UID and authenticates the ride block. */
  private async openBlock(uid: string) {
    const target = await this.select();
    if (!target || toHex(target.uid) !== uid) {
      throw new CardError("The card left the reader. Tap it again.");
    }
    if (target.uid.length !== 4) {
      throw new CardError(
        "This card has a 7-byte UID, which Chipper doesn't support.",
      );
    }
    if (target.sak !== SAK_CLASSIC_1K) {
      throw new CardError("This isn't a MIFARE Classic 1K card.");
    }
    try {
      await this.exchange([
        MIFARE_AUTH_A,
        CHIPPER_BLOCK,
        ...FACTORY_KEY,
        ...target.uid,
      ]);
    } catch (error) {
      if (!(error instanceof CardError)) throw error;
      throw new CardError(
        "This card isn't factory-keyed, so it can't be used.",
      );
    }
  }

  /** InDataExchange with the selected card; returns the card's reply. */
  private async exchange(bytes: number[]) {
    const data = await this.command(CMD_IN_DATA_EXCHANGE, [0x01, ...bytes]);
    if ((data[0] & 0x3f) !== 0) {
      throw new CardError("The card didn't answer. Tap it again.");
    }
    return data.subarray(1);
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
