# Chipper card storage format (v2)

A Chipper card carries its own rides. **The card is the source of truth**; the backend is only told about taps, best effort.

Rides are stored in **halves**. Tapping on takes the first half of a ride and tapping off takes the second half, so the card itself records whether its rider is mid-trip. The readers are `firmware/caltreat_chipper_reader`.

This document is the contract between the readers and anything that **issues** cards, such as the employee portal's Web Serial flow through the caltreat_usb_nfc bridge (`website/frontend/src/app/employee/cards/pn532.ts`).

## Card

- **MIFARE Classic 1K** (ATQA `00 04`, SAK `08`) with a **4-byte UID**.
- 7-byte-UID cards are out of scope. pn532.js authenticates with the first 4 UID bytes, but those cards need the last 4 (see `firmware/caltreat_usb_nfc/README.md`).
- **Keys and access bits stay at the factory defaults.** Key A = key B = `FF FF FF FF FF FF`, and the sector trailer is `000000000000 FF078069 FFFFFFFFFFFF`.
- **Never write a sector trailer** (blocks 3, 7, 11, …).
- There are no secrets. Anyone with an NFC app can rewrite the block; that's accepted.

## Layout: sector 1, block 4 (one 16-byte block, nothing else is used)

| Offset | Size | Value |
|---|---|---|
| 0 | 4 | Magic `"CHPR"` = `43 48 50 52` |
| 4 | 1 | Format version = **`02`** |
| 5 | 1 | **Half rides** remaining, `uint8` (0–255) |
| 6 | 8 | Reserved, must be `00` |
| 14 | 2 | CRC-16 over bytes 0–13, **little-endian** (low byte first) |

### Half rides

| Byte 5 | Meaning |
|---|---|
| even, `2N` | N whole rides, **not** on a trip. The next tap is a **tap on** (if N ≥ 1). |
| odd, `2N + 1` | **Tapped on**: this trip's first half is paid, its second half is still on the card, and there are N whole rides after it. The next tap is a **tap off**. |
| `0` | No rides left. |

- **Issuing N rides writes `2N`.** A card with 1 ride is `02`, and a card with 5 rides is `0A`. Issuing or reloading always writes an even value, so it also closes any open trip.
- **To display a card:** `whole = byte5 / 2` (integer division), and `tapped_on = byte5 is odd`. For example `03` → "1 ride, tapped on".
- The maximum is 127 whole rides (`FE`).

### CRC

The CRC is **CRC-16/ISO-IEC-14443-3-A**, the "CRC_A" ISO 14443 uses on the air:
- polynomial `0x1021`, processed reflected (`0x8408`);
- initial value `0x6363` in reflected form (`0xC6C6` unreflected);
- input and output reflected, no final XOR.

**Check value:** ASCII `"123456789"` → `0xBF05`.

TypeScript (for pn532.ts):

```ts
export function crcA(data: Uint8Array): number {
  let crc = 0x6363;
  for (const byte of data) {
    let b = (byte ^ crc) & 0xff;
    b = (b ^ (b << 4)) & 0xff;
    crc = ((crc >> 8) ^ (b << 8) ^ (b << 3) ^ (b >> 4)) & 0xffff;
  }
  return crc;
}
```

Python:

```python
def crc_a(data: bytes) -> int:
    crc = 0x6363
    for byte in data:
        b = (byte ^ crc) & 0xFF
        b = (b ^ (b << 4)) & 0xFF
        crc = ((crc >> 8) ^ (b << 8) ^ (b << 3) ^ (b >> 4)) & 0xFFFF
    return crc
```

C, as used by the reader (`src/card.c`, `card_crc16()`):

```c
uint16_t crc = 0x6363;
for (size_t i = 0; i < len; i++) {
    uint8_t b = data[i] ^ (crc & 0xFF);
    b ^= b << 4;
    crc = (crc >> 8) ^ ((uint16_t)b << 8) ^ ((uint16_t)b << 3) ^ (b >> 4);
}
```

### Test vectors (block 4, all 16 bytes)

| Card | Block |
|---|---|
| 1 ride, not on a trip (a newly issued card) | `43 48 50 52 02 02 00 00 00 00 00 00 00 00 EE A5` |
| tapped on, 0 rides after this trip | `43 48 50 52 02 01 00 00 00 00 00 00 00 00 E9 73` |
| no rides left | `43 48 50 52 02 00 00 00 00 00 00 00 00 00 14 3E` |
| 2 rides, not on a trip | `43 48 50 52 02 04 00 00 00 00 00 00 00 00 F1 01` |

## Issuing a card (portal / bridge)

Over the bridge's PN532 protocol (`InListPassiveTarget` = `0x4A`, `InDataExchange` = `0x40`):

1. Select the card: `InListPassiveTarget` (`4A 01 00`). Check `SAK == 0x08` and UID length 4. Refuse anything else, with a clear message.
2. Authenticate block 4 with key A: `InDataExchange` `40 01 60 04 FF FF FF FF FF FF <uid 4 bytes>`. The status byte must be `00`. If it fails, the card isn't factory-keyed: refuse it, and don't try other keys.
3. Optional: read block 4 (`40 01 30 04`). If it already holds a valid Chipper block (v1 or v2, magic and CRC good), show its rides and ask before overwriting.
4. Write block 4: `40 01 A0 04 <16 bytes>`, version `02` and byte 5 = `2 × rides`.
5. **Verify:** re-select and authenticate again, then read block 4 back and compare all 16 bytes. Report success only if they match.
6. Register the card's UID with the backend (`POST /api/riders/`) as today. The UID is lowercase hex without separators, e.g. `0ee3431e`.

## What the reader does

On each tap, while the card is held:

| Card | Reader shows | Card written? | Reported to the backend? |
|---|---|---|---|
| Key A auth fails (not factory keys) | NOT A CHIPPER CARD | no | no |
| Magic ≠ `CHPR`, or version not 1 or 2 (blank and other cards) | NOT A CHIPPER CARD | no | no |
| CRC wrong (torn or edited block) | TAP AGAIN (logged "card damaged": re-issue it) | no | no |
| Even, ≥ 2 | TRAVEL OK, TAGGED ON | yes: byte 5 − 1 (now odd) | yes, `result: "tap_on"` |
| Odd | TRAVEL OK, TAGGED OFF, RIDES LEFT n | yes: byte 5 − 1 (now even) | yes, `result: "tap_off"` |
| Odd, and it tapped on **at this same reader** within the last 30 minutes (since that reader's last boot) | TRAVEL OK, ALREADY TAPPED ON | no | no |
| `00` | NO RIDES LEFT | no | yes, `result: "no_rides"` |

- Every write by the reader is version 2. Reserved bytes 6–13 are copied unchanged, and the write is read back and compared.
- The same-reader rule stops a rider's second tap at the gate they just entered from closing their trip. After 30 minutes, a tap there counts as a normal tap-off, so a rider who forgot to tap off isn't stuck.

### Tap report sent by the reader (`POST /api/taps/`, best effort, retried)

```json
{"card_uid": "0ee3431e", "kiosk_id": "chipper-reader-e8f60ae251f9",
 "result": "tap_on", "rides_left": 0, "half_rides_left": 1,
 "tapped_at": "2026-10-31T23:05:12.345Z", "seq": 17}
```

- `result` is `tap_on`, `tap_off` or `no_rides`.
- `rides_left` is whole rides after this tap (`half_rides_left / 2`).
- `kiosk_id` is the reader's own ID (`chipper-reader-` + its factory MAC), also shown on the reader's idle screen.
- `tapped_at` is `null` until the reader's clock has synced (SNTP).
- `seq` counts up per reader since boot, so the backend can de-duplicate retries.

## Changes from v1

v1 (version byte `01`) stored **whole rides** in byte 5, and every tap took one ride.

- **Readers still accept v1 cards.** They read byte 5 as `2 × rides`, i.e. not on a trip, and rewrite the card as v2 on its first tap. Cards already issued as v1 keep working.
- **Portal changes needed** (`website/frontend/src/app/employee/cards/pn532.ts`):
  - `makeChipperBlock(rides)`: write version `02` and byte 5 = `2 × rides` (check against the test vectors above).
  - `parseChipperBlock()`: accept versions `01` and `02`. For v2, report `rides = byte5 / 2` and `tappedOn = byte5 is odd`. For v1, report `rides = byte5`, not tapped on. Today it treats any version other than 1 as "blank", so a card a reader has tapped would be overwritten without the confirmation prompt.
  - Show "tapped on" where the portal shows a card's rides, e.g. "1 ride (tapped on)".
  - The rides input stays in whole rides (1–127, since `2 × rides` must fit in a byte).

## Changing the format

Bump the version byte (offset 4) for any layout change. Readers treat an unknown version as "not a Chipper card" and never write to it.
