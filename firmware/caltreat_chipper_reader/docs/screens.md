# Reader screens: references

Every screen the firmware draws, the real next-generation Clipper (Cubic) validator
image it is modelled on, and anything not yet backed by a reference.

Sources referred to below:

| Key | Source |
|---|---|
| SFCTA | SFCTA photo of a Muni LRV validator, full resolution: https://www.sfcta.org/sites/default/files/2024-07/229027_LRV4_Interior_7.jpg |
| GPAY | Clipper, "How to Pay for the Bus with Google Pay": https://www.youtube.com/watch?v=cz17U6Wfy1k |
| LSJQ | Clipper, "How to Pay for the Bus with Clipper on iPhone": https://www.youtube.com/watch?v=lsjqT0owwTM |
| B0YL | Clipper, "How to Pay for Train or Ferry with Clipper on iPhone": https://www.youtube.com/watch?v=B0YlMfe52Sk |
| MTC | MTC, "How to Tap ... with a Contactless Bank Card": https://www.youtube.com/watch?v=BtsGp7agwAQ |
| NBD | "We Attempted to Ride Every Bay Area Transit Agency" vlog: https://www.youtube.com/watch?v=NBD3wD0VGDo |
| U2, U4 | Project owner's photos: TRAVEL OK (blue) and TRAVEL NOT OK / EXPIRED CARD (orange) |

## Implemented

| Screen | Layout source | Notes |
|---|---|---|
| Idle "TAG HERE" | SFCTA; GPAY 0:19; NBD 363 s | Logo + wordmark (Chipper art substituted), hand icon traced from SFCTA (`tools/hand_icon.py`), bold "TAG HERE", date/time bottom left |
| TRAVEL OK | U2; GPAY 0:35; LSJQ 0:20; MTC 0:50; NBD 878 s | No logo; "TRAVEL" / "OK" bold, outlined check circle, "FARE $x.xx" under it and "BAL $x.xx" near the bottom in regular-weight capitals |
| TRAVEL OK, tag off | NBD 1119 s ("FARE $0.00" / "BAL $82.75") | Same layout with "FARE $0.00" (fixed-fare model: the fare is taken at tag on) |
| TRAVEL NOT OK + reason | U4 ("EXPIRED CARD"); GPAY 0:25 | No logo; "TRAVEL" / "NOT OK" bold, prohibition circle, reason in regular-weight capitals |

## Colours

Sampled from footage where the same camera shows several screens of the same device,
so exposure and white balance cancel out when comparing them:

| Screen | Same-camera samples | Firmware value |
|---|---|---|
| Idle | Bus validators: LSJQ #2A252A, GPAY #1A161B / #1F1C25, MTC #2E2D2D, neutral, i.e. black. Caltrain: navy per the project owner's first-hand observation; no clean Caltrain idle image found (NBD 363 s night shot #121B2E) | `#121A30` (Caltrain theme) |
| TRAVEL OK | LSJQ #171E32 (hue 224), GPAY #1C2343 (~230), MTC #243448 (213), U2 #2F80B0 (201, Caltrain pedestal) | `#121A30`, same navy as idle, per the project owner (Caltrain). Note U2 (a Caltrain pedestal) measures a saturated medium blue, ~`#2A78C6` |
| TRAVEL NOT OK / ERROR | LSJQ #5A4231 (22), GPAY #963917, U4 #CB7F2E (30) | `#E57200`, Clipper brand orange PMS 152C (brand guide: https://docs.clippercard.com/brochures/en/ClipperGuidelines_10-01-19.pdf), hue 30 like U4 |

## Not yet backed by a reference

These results exist in the firmware but no image of the real screen was found; they
reuse the nearest verified template and their wording is a placeholder.

| Result | Currently shows | Status |
|---|---|---|
| Low balance | TRAVEL OK + FARE + BAL (two beeps) | Matches LSJQ 0:20 ("BAL $2.75" on a normal TRAVEL OK); official text also mentions a yellow light, which this device does not have |
| Insufficient funds | TRAVEL NOT OK + "INSUFFICIENT FUNDS" | Template verified (U4), wording unverified (Cubic Validator 3 manual lists "Insufficient balance") |
| Card read error | TRAVEL NOT OK + "PLEASE TAP AGAIN" | A reference exists and should replace this: "ERROR" / prohibition / "TAG CARD AGAIN" (LSJQ 0:35, B0YL 0:34) |
