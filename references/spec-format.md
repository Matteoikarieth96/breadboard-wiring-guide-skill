# Spec format

One JSON object. Unknown keys are an error, so typos show up at once.

```json
{
  "title": "Desk doorbell",
  "board": "uno-r4-wifi",
  "language": "en",
  "existing": ["rails"],
  "lcd_text": ["Hello,", "breadboard!"],
  "parts": [
    {"id": "bell", "type": "button", "label": "Bell", "pin": "D2"},
    {"id": "lamp", "type": "led", "color": "green"},
    {"id": "stick", "type": "joystick", "pins": {"VRX": "A0", "VRY": "A1", "SW": "D6"}}
  ],
  "steps": [
    {"parts": ["bell", "lamp"]},
    {"title": "Place the joystick and power it", "parts": ["stick"]},
    {"title": "Joystick signals", "wires": ["stick.VRX", "stick.VRY", "stick.SW"]}
  ]
}
```

## Top level

| Key | Required | Meaning |
| --- | --- | --- |
| `title` | yes | Shown on every drawing, up to 80 characters. |
| `board` | no | `uno-r3`, `uno-r4-minima` or `uno-r4-wifi` (default). Sets the pin current limit: 20 mA on R3, 8 mA on R4. |
| `language` | no | `en` (default) or `it`. Fixed text only; your titles, labels and notes are used as written. `--lang` on the command line overrides it. |
| `parts` | yes | 1 to 12 parts, see below. |
| `existing` | no | Part ids already wired and working, plus `"rails"` if 5V and GND already feed the rails. No steps for them; their wires are drawn grey. Each existing part must give its pins and, on the breadboard, its `column`, as wired on the bench. |
| `steps` | no | Leave it out for one step per part (after a first step for the rails). |
| `lcd_text` | no | Two lines of up to 16 characters shown on the drawn LCD. |

## Parts

| Key | Meaning |
| --- | --- |
| `id` | 1 to 16 characters: lowercase letters, digits, `_`, starting with a letter. Not `rails`. |
| `type` | See the table below. |
| `label` | Optional short name (up to 24 characters), shown in the text and on the drawing. |
| `pin` | For parts with one signal: `D2`, `2`, `A0`. |
| `pins` | For parts with several signals: `{"VRX": "A0", ...}`. Any signal left out gets a free pin. |
| `color` | LEDs only: `red`, `yellow`, `green`, `blue`, `white`. Sets the resistor. |
| `column` | Breadboard parts only: first breadboard column (3 to 28), to match a real bench. Parts need one free column between them. Default: next free column from the left. |

| Type | Where it goes | Signals |
| --- | --- | --- |
| `button` | across the centre gap, 3 columns | `SIG` (digital) |
| `led` | resistor + LED, 5 columns | `SIG` (digital) |
| `buzzer` | resistor + passive buzzer (legs 3 holes apart), 7 columns | `SIG` (digital) |
| `potentiometer` | 3 legs in a row, 3 columns | `WIPER` (analog only) |
| `speaker` | resistor on the breadboard (4 columns), speaker on its wires | `SIG` (digital) |
| `lcd1602-i2c` | off the breadboard | `SDA`, `SCL` (fixed) |
| `joystick` | off the breadboard | `VRX`, `VRY` (analog), `SW` (digital) |
| `hc-sr04` | off the breadboard | `TRIG`, `ECHO` (digital) |
| `pir` | off the breadboard | `OUT` (an analog pin when one is free) |

Pin rules the check enforces: no pin used twice; 5V, GND, 3V3, VIN, AREF,
IOREF, RST are never signal pins; analog signals need A0 to A5; with an LCD,
A4 and A5 stay free (they are SDA and SCL). D0 and D1 work but get a note on
the UNO R3 (they are its USB serial line); an input on D13 gets a note (the
on-board LED sits on it). Automatic pins never use D0, D1 or D13, start at
D2 and A0, and give LEDs a PWM pin (3, 5, 6, 9, 10, 11) when one is free.

Text rules: titles, labels, notes and `lcd_text` are plain text: Latin
letters (accents included), digits, basic punctuation, and the signs °, Ω,
µ, € and $; typographic quotes and dashes become plain ones. Links, domains,
e-mail addresses, other alphabets, control or invisible characters, and
anything that spells USB or plug (also with dots, spaces, accents or common
stand-ins such as $ for S) are refused: the USB call is the script's job.
Plain words that say the same thing ("keep the board connected") cannot be
caught; the script's own USB line always comes first. In `guide.md` the
USB line comes right under each step heading, before your step title.

## Steps

Each step: `title` (optional), `parts` (placed in this step, with all their
wires) and/or `wires` (wire names taken out of their part's step and done
here), `note` (optional, up to 200 characters), `usb` (optional, only
`"unplug"` to force the warning).

- Every part not in `existing` must be placed in exactly one step.
- A wire can be moved to a later step, never to a step before its part.
- The rails step comes first on its own unless you list `"rails"` in a step
  or in `existing`.
- The USB call is computed: a step that places a part or touches a power wire
  says unplug. Only a step with signal wires alone (board D, A, SDA or SCL pin
  to a part) may say USB can stay plugged in, and only when the rails and the
  power wires of every part it touches were done in an earlier step or are
  `existing`. The speaker's leads count as power. `usb` can make a step
  stricter, never looser.

Wire names are `<part id>.<pin>`: `stick.SW`, `stick.GND`, `bell.SIG`,
`lamp.GND`, plus `rails.5V`, `rails.GND` and `rails.bridge` (the black jumper
that joins the top and bottom - rails when a button needs GND at the bottom).
`check` prints every name.

## Output

`build --out DIR` writes into an empty folder, or into a folder holding an
earlier build of this skill (its `plan.json` says `"generator":
"breadboard-wiring-guide"`). It checks every file it would touch first, never
writes through symlinks or hard links (each file is written to a temp file
and renamed), and only removes its own old `step-NN` and `overview` images
and old `guide.md`. It writes `plan.json` with `"complete": false` first and
the full plan last, so a build cut short is easy to spot.

- `overview.svg`, `step-01.svg` ... (and `.png` with `--png`)
- `guide.md`: safety notes, what you need (jumper wires by type, resistors
  with 4-band and 5-band codes), pin map, notes, then every step starting
  with the USB line, then the final check
- `plan.json`: the resolved plan (pins, columns, resistors, steps with their
  `usb` and `usb_line`, wires with their endpoints), handy to compare with a
  photo
