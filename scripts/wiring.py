#!/usr/bin/env python3
"""Breadboard wiring guide for UNO-family boards (5 V logic).

Reads a JSON project spec, checks it, places the parts, routes every jumper
on a grid and writes one SVG per step, an overview SVG, a Markdown guide and a
plan.json. With --png it also renders PNGs through headless Chrome or
Chromium, when one is installed.

    python3 scripts/wiring.py parts
    python3 scripts/wiring.py check SPEC.json
    python3 scripts/wiring.py build SPEC.json --out DIR [--png]

Standard library only, Python 3.9 or newer.
"""

import argparse
import heapq
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unicodedata
from pathlib import Path

GRID = 9
PITCH = 18
MAX_SPEC_BYTES = 256 * 1024
MAX_PARTS = 12
MAX_STEPS = 20

# ---------------------------------------------------------------- boards

BOARDS = {
    "uno-r3": {"name": "UNO R3", "pin_ma": 20},
    "uno-r4-minima": {"name": "UNO R4 Minima", "pin_ma": 8},
    "uno-r4-wifi": {"name": "UNO R4 WiFi", "pin_ma": 8},
}
TOP_HEADER = ["SCL", "SDA", "AREF", "GND", "D13", "D12", "D11", "D10", "D9",
              "D8", None, "D7", "D6", "D5", "D4", "D3", "D2", "D1", "D0"]
POWER_HEADER = ["NC", "IOREF", "RST", "3V3", "5V", "GND", "GND", "VIN"]
ANALOG_HEADER = ["A0", "A1", "A2", "A3", "A4", "A5"]
DIGITAL_PINS = ["D%d" % i for i in range(14)]
SIGNAL_PINS = set(DIGITAL_PINS) | set(ANALOG_HEADER) | {"SDA", "SCL"}
POWER_PINS = {"5V", "3V3", "VIN", "GND"}
AUTO_DIGITAL = ["D%d" % i for i in range(2, 14)]

# ---------------------------------------------------------------- parts

# kind: "bb" sits on the breadboard, "module" hangs off jumpers,
# "mixed" has a resistor on the breadboard and a body off it.
PARTS = {
    "button": {"kind": "bb", "width": 3, "signals": {"SIG": "digital"}},
    "led": {"kind": "bb", "width": 5, "signals": {"SIG": "digital"}},
    "buzzer": {"kind": "bb", "width": 5, "signals": {"SIG": "digital"}},
    "potentiometer": {"kind": "bb", "width": 3, "signals": {"WIPER": "analog"}},
    "speaker": {"kind": "mixed", "width": 4, "signals": {"SIG": "digital"},
                "module": {"w": 90, "h": 108, "pins": ["+", "-"]}},
    "lcd1602-i2c": {"kind": "module", "signals": {"SDA": "sda", "SCL": "scl"},
                    "module": {"w": 288, "h": 144, "pins": ["GND", "VCC", "SDA", "SCL"]},
                    "power": {"VCC": "+", "GND": "-"}},
    "joystick": {"kind": "module",
                 "signals": {"VRX": "analog", "VRY": "analog", "SW": "digital"},
                 "module": {"w": 144, "h": 162, "pins": ["GND", "+5V", "VRX", "VRY", "SW"]},
                 "power": {"+5V": "+", "GND": "-"}},
    "hc-sr04": {"kind": "module", "signals": {"TRIG": "digital", "ECHO": "digital"},
                "module": {"w": 162, "h": 90, "pins": ["VCC", "TRIG", "ECHO", "GND"]},
                "power": {"VCC": "+", "GND": "-"}},
    "pir": {"kind": "module", "signals": {"OUT": "analog-preferred"},
            "module": {"w": 126, "h": 126, "pins": ["VCC", "OUT", "GND"]},
            "power": {"VCC": "+", "GND": "-"}},
}
LED_COLORS = {  # forward voltage, kept low on purpose (more current = safer pick)
    "red": (1.8, "#E53935"), "yellow": (1.9, "#FDD835"), "green": (1.9, "#43A047"),
    "blue": (2.8, "#1E88E5"), "white": (2.8, "#F5F5F5"),
}
COMMON_RESISTORS = [220, 330, 470, 680, 1000, 2200]
RESISTOR_BANDS = {
    220: ("red", "red", "brown"), 330: ("orange", "orange", "brown"),
    470: ("yellow", "violet", "brown"), 680: ("blue", "grey", "brown"),
    1000: ("brown", "black", "red"), 2200: ("red", "red", "red"),
}
BAND_HEX = {"black": "#111111", "brown": "#7B4A2A", "red": "#D32F2F", "orange": "#F57C00",
            "yellow": "#FBC02D", "green": "#388E3C", "blue": "#1976D2", "violet": "#7B1FA2",
            "grey": "#9E9E9E", "white": "#FFFFFF", "gold": "#C9A227"}

# ---------------------------------------------------------------- colours

POWER_RED, GND_BLACK, SDA_GREEN, SCL_YELLOW = "#D3382B", "#222222", "#2E9E4F", "#E0B02C"
SIGNAL_COLORS = [("blue", "#2C63D6"), ("orange", "#F07F1C"), ("purple", "#8A4BD6"),
                 ("teal", "#1FA6A6"), ("pink", "#D6458E"), ("brown", "#8D5A3B"),
                 ("olive", "#7C8A1E"), ("cyan", "#19A7CE"), ("grey", "#7A7F87")]
INK, INK2, PAPER = "#1A212C", "#5B6470", "#FAFAF7"

# ---------------------------------------------------------------- text

STR = {
    "en": {
        "unplug": "Unplug USB first.",
        "stay": "USB can stay plugged in.",
        "step": "Step {n} of {total}",
        "overview": "Overview: everything wired",
        "rails_title": "Power the breadboard rails",
        "add": "Add {name}",
        "connect": "Connect {what}",
        "ep": {"top_rail": ("the {pol} rail at the top (column {col})",) * 2,
               "bottom_rail": ("the {pol} rail at the bottom (column {col})",) * 2,
               "hole": ("hole {hole}",) * 2,
               "pin": ("pin {pin}",) * 2,
               "mod_pin": ("pin {pin} of the {name}",) * 2},
        "wire": "{color} wire: {a} to {b}",
        "jumper": "Short {color} jumper: {a} to {b}",
        "bands": {"black": "black", "brown": "brown", "red": "red", "orange": "orange",
                  "yellow": "yellow", "green": "green", "blue": "blue", "violet": "violet",
                  "grey": "grey", "white": "white", "gold": "gold"},
        "colors": {"red": "Red", "black": "Black", "green": "Green", "yellow": "Yellow",
                   "blue": "Blue", "orange": "Orange", "purple": "Purple", "teal": "Teal",
                   "pink": "Pink", "brown": "Brown", "olive": "Olive", "cyan": "Cyan",
                   "grey": "Grey"},
        "names": {"button": "push button", "led": "LED", "buzzer": "passive buzzer",
                  "potentiometer": "potentiometer", "speaker": "speaker",
                  "lcd1602-i2c": "LCD 1602 with I2C adapter", "joystick": "joystick module",
                  "hc-sr04": "HC-SR04 distance sensor", "pir": "PIR motion sensor"},
        "place": {
            "button": "Push the button across the centre gap: legs in e{c}, e{c2}, f{c} and f{c2}.",
            "led": "Resistor {r}: legs in e{c} and e{c3}. LED: long leg (+) in d{c3}, short leg (-) in d{c4}.",
            "buzzer": "Resistor {r}: legs in e{c} and e{c3}. Buzzer: + leg in d{c3}, other leg in d{c4}.",
            "potentiometer": "Potentiometer: its three legs in e{c}, e{c1} and e{c2}.",
            "speaker": "Resistor {r}: legs in e{c} and e{c3}. The speaker itself stays off the breadboard.",
            "module": "Take the {name}. Read the pin names printed on it: the order drawn here is common, yours may differ.",
        },
        "check": {
            "button": "The two wired legs are diagonal (e{c} and f{c2}). Two legs on the same side can be joined inside the button and read as always pressed.",
            "led": "Long leg toward the resistor. If the LED is backwards it stays dark but takes no harm.",
            "buzzer": "The + leg (often the longer one) goes toward the resistor.",
            "potentiometer": "Outer legs to + and -, the middle leg is the signal.",
            "speaker": "Never wire a speaker straight to a pin: the resistor keeps the current safe. The sound is quiet; real volume needs an amplifier module (not covered here).",
            "lcd1602-i2c": "Backlight on but no letters: turn the small contrast screw on the back. The I2C address is usually 0x27 or 0x3F.",
            "joystick": "Read SW with INPUT_PULLUP in the sketch: pressed reads LOW.",
            "hc-sr04": "No reading at all usually means Trig and Echo are swapped.",
            "pir": "Wait about a minute after power-up before trusting it. Pin order varies between boards: read the labels (sometimes under the white dome).",
        },
        "rails_check": "5V goes to the + rail, GND to the - rail. Never join the + rail and the - rail.",
        "bridge": "This part uses the bottom - rail: the black bridge joins the two - rails.",
        "panel_title": "Wires in this step",
        "panel_overview": "Pin map",
        "faded": "Grey wires are already in place.",
        "existing": "Already wired: {what}.",
        "board_sub": "top view, USB on the left",
        "breadboard": "breadboard (half size, 30 columns)",
        "guide_title": "Wiring guide: {title}",
        "board_line": "Board: {board}. Breadboard: half size, 30 columns. Pins on this board: at most {ma} mA each.",
        "parts_head": "Parts",
        "pins_head": "Pin map",
        "safety_head": "Before you start",
        "safety": [
            "Every step says first whether USB must be unplugged. Power wires (5V, GND, rails, module VCC and GND) and whole modules: unplug. Signal wires only: USB can stay plugged in.",
            "Wire colours in the drawings are only a guide. Any colour works, but red for 5V and black for GND avoids mistakes.",
            "Hole names: letter = row (a to j), number = column (1 to 30). Holes in the same column, a to e (or f to j), are joined inside the breadboard.",
            "No mains voltage, ever. Everything here runs on the 5 V from the USB cable.",
        ],
        "final_head": "Final check, then plug in USB",
        "final": [
            "Nothing joins the + rail to the - rail.",
            "Every module: VCC on +, GND on -.",
            "Plug in USB. The board's power LED lights up. If it does not, or anything gets warm, unplug at once and recheck.",
        ],
        "notes_head": "Notes",
        "check_head": "Check",
        "resistor": "resistor {r}",
        "pin_table": "| Part | Pin | Board pin |",
        "note_ma": "This board allows at most {ma} mA per pin: resistor values are picked for that.",
        "note_i2c": "SDA and SCL are the same signals as A4 and A5: keep A4 and A5 free.",
        "note_serial": "{pin} is also the USB serial line: uploads can fail while something is wired to it.",
        "note_pir_digital": "On UNO R4 boards a PIR with a 3.3 V output may not read as HIGH on a digital pin. Use an analog pin and analogRead (above about 600 = motion).",
        "note_pir_analog": "Read the PIR with analogRead: above about 600 means motion. This works on both UNO R3 and R4.",
        "rail_pol": {"+": "+", "-": "-"},
    },
    "it": {
        "unplug": "USB staccato.",
        "stay": "USB può restare attaccato.",
        "step": "Passo {n} di {total}",
        "overview": "Schema completo",
        "rails_title": "Alimenta le righe della breadboard",
        "add": "Aggiungi {name}",
        "connect": "Collega {what}",
        "ep": {"top_rail": ("dalla riga {pol} in alto (colonna {col})", "alla riga {pol} in alto (colonna {col})"),
               "bottom_rail": ("dalla riga {pol} in basso (colonna {col})", "alla riga {pol} in basso (colonna {col})"),
               "hole": ("dal foro {hole}", "al foro {hole}"),
               "pin": ("dal pin {pin}", "al pin {pin}"),
               "mod_pin": ("dal pin {pin} ({name})", "al pin {pin} ({name})")},
        "wire": "Filo {color}: {a} {b}",
        "jumper": "Ponticello {color}: {a} {b}",
        "bands": {"black": "nero", "brown": "marrone", "red": "rosso", "orange": "arancione",
                  "yellow": "giallo", "green": "verde", "blue": "blu", "violet": "viola",
                  "grey": "grigio", "white": "bianco", "gold": "oro"},
        "colors": {"red": "rosso", "black": "nero", "green": "verde", "yellow": "giallo",
                   "blue": "blu", "orange": "arancione", "purple": "viola", "teal": "verde acqua",
                   "pink": "rosa", "brown": "marrone", "olive": "verde oliva", "cyan": "azzurro",
                   "grey": "grigio"},
        "names": {"button": "pulsante", "led": "LED", "buzzer": "buzzer passivo",
                  "potentiometer": "potenziometro", "speaker": "speaker",
                  "lcd1602-i2c": "LCD 1602 con adattatore I2C", "joystick": "modulo joystick",
                  "hc-sr04": "sensore di distanza HC-SR04", "pir": "sensore di movimento PIR"},
        "place": {
            "button": "Metti il pulsante a cavallo del canale centrale: gambe in e{c}, e{c2}, f{c} e f{c2}.",
            "led": "Resistenza {r}: gambe in e{c} ed e{c3}. LED: gamba lunga (+) in d{c3}, gamba corta (-) in d{c4}.",
            "buzzer": "Resistenza {r}: gambe in e{c} ed e{c3}. Buzzer: gamba + in d{c3}, l'altra in d{c4}.",
            "potentiometer": "Potenziometro: le tre gambe in e{c}, e{c1} ed e{c2}.",
            "speaker": "Resistenza {r}: gambe in e{c} ed e{c3}. Lo speaker resta fuori dalla breadboard.",
            "module": "Prendi questo modulo: {name}. Leggi i nomi dei pin stampati sul modulo: l'ordine del disegno è il più comune, il tuo può cambiare.",
        },
        "check": {
            "button": "Le due gambe collegate sono in diagonale (e{c} e f{c2}). Due gambe dallo stesso lato possono essere unite dentro il pulsante e risultare sempre premute.",
            "led": "Gamba lunga verso la resistenza. Se il LED è al contrario resta spento ma non si rompe.",
            "buzzer": "La gamba + (spesso la più lunga) va verso la resistenza.",
            "potentiometer": "Le gambe esterne vanno a + e -, quella centrale è il segnale.",
            "speaker": "Mai lo speaker dritto su un pin: la resistenza tiene bassa la corrente. Il suono è debole; per il volume vero serve un modulo amplificatore (non incluso qui).",
            "lcd1602-i2c": "Luce accesa ma niente lettere: gira la vitina del contrasto sul retro. L'indirizzo I2C di solito è 0x27 o 0x3F.",
            "joystick": "Leggi SW con INPUT_PULLUP nello sketch: premuto vale LOW.",
            "hc-sr04": "Nessuna lettura di solito vuol dire Trig ed Echo invertiti.",
            "pir": "Aspetta circa un minuto dall'accensione prima di fidarti. L'ordine dei pin cambia tra i moduli: leggi le scritte (a volte sotto la cupola bianca).",
        },
        "rails_check": "5V va sulla riga +, GND sulla riga -. Mai unire la riga + e la riga -.",
        "bridge": "Questo pezzo usa la riga - in basso: il ponte nero unisce le due righe -.",
        "panel_title": "Fili di questo passo",
        "panel_overview": "Mappa dei pin",
        "faded": "I fili grigi sono già al loro posto.",
        "existing": "Già collegato: {what}.",
        "board_sub": "vista dall'alto, USB a sinistra",
        "breadboard": "breadboard (mezza, 30 colonne)",
        "guide_title": "Guida ai collegamenti: {title}",
        "board_line": "Scheda: {board}. Breadboard: mezza, 30 colonne. Pin di questa scheda: al massimo {ma} mA ciascuno.",
        "parts_head": "Componenti",
        "pins_head": "Mappa dei pin",
        "safety_head": "Prima di iniziare",
        "safety": [
            "Ogni passo dice per prima cosa se staccare l'USB. Fili di alimentazione (5V, GND, righe, VCC e GND dei moduli) e moduli interi: USB staccato. Solo fili di segnale: USB può restare attaccato.",
            "I colori dei fili nei disegni sono solo una guida. Va bene qualsiasi colore, ma rosso per 5V e nero per GND evita errori.",
            "Nomi dei fori: lettera = riga (da a a j), numero = colonna (da 1 a 30). I fori della stessa colonna da a a e (o da f a j) sono uniti dentro la breadboard.",
            "Mai tensione di rete. Qui tutto va con i 5 V del cavo USB.",
        ],
        "final_head": "Controllo finale, poi attacca l'USB",
        "final": [
            "Niente unisce la riga + alla riga -.",
            "Ogni modulo: VCC sul +, GND sul -.",
            "Attacca l'USB. Il LED di alimentazione della scheda si accende. Se non si accende, o qualcosa scalda, stacca subito e ricontrolla.",
        ],
        "notes_head": "Note",
        "check_head": "Controllo",
        "resistor": "resistenza {r}",
        "pin_table": "| Componente | Pin | Pin della scheda |",
        "note_ma": "Questa scheda regge al massimo {ma} mA per pin: le resistenze sono scelte per questo.",
        "note_i2c": "SDA e SCL sono gli stessi segnali di A4 e A5: lascia liberi A4 e A5.",
        "note_serial": "{pin} è anche la linea seriale dell'USB: il caricamento può fallire se ci colleghi qualcosa.",
        "note_pir_digital": "Sulle UNO R4 un PIR con uscita a 3,3 V può non essere letto come HIGH su un pin digitale. Usa un pin analogico e analogRead (sopra circa 600 = movimento).",
        "note_pir_analog": "Leggi il PIR con analogRead: sopra circa 600 vuol dire movimento. Funziona sia su UNO R3 sia su R4.",
        "rail_pol": {"+": "+", "-": "-"},
    },
}


class SpecError(Exception):
    """The spec cannot be built; the message says why."""


# ---------------------------------------------------------------- helpers

_BAD_CHARS = re.compile("[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]")


def clean_text(value, field, max_len):
    if not isinstance(value, str):
        raise SpecError("%s must be text" % field)
    if _BAD_CHARS.search(value) or any(unicodedata.category(c) in ("Cf", "Co", "Cs") for c in value):
        raise SpecError("%s contains control or invisible characters" % field)
    value = value.strip()
    if not value:
        raise SpecError("%s is empty" % field)
    if len(value) > max_len:
        raise SpecError("%s is longer than %d characters" % (field, max_len))
    return value


def xml(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def md(text):
    """Escape user text for Markdown: no HTML, no links, no table breaks."""
    out = str(text)
    for a, b in (("\\", "\\\\"), ("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"),
                 ("[", "\\["), ("]", "\\]"), ("|", "\\|"), ("`", "\\`")):
        out = out.replace(a, b)
    return out


def norm_pin(value, field):
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        raise SpecError("%s must be a pin name like D2 or A0" % field)
    v = value.strip().upper()
    if re.fullmatch(r"\d{1,2}", v):
        v = "D" + str(int(v))
    m = re.fullmatch(r"D(\d{1,2})", v)
    if m:
        v = "D" + str(int(m.group(1)))
    if v in POWER_PINS or v in ("AREF", "IOREF", "RST", "NC"):
        raise SpecError("%s: %s is a power or special pin, not a signal pin" % (field, v))
    if v not in SIGNAL_PINS:
        raise SpecError("%s: unknown pin %r (use D0-D13, A0-A5, SDA or SCL)" % (field, value))
    return v


def pick_resistor(volt_drop, pin_ma):
    """Smallest common value that keeps the pin at or under 80% of its limit."""
    limit_ma = pin_ma * 0.8
    for r in COMMON_RESISTORS:
        if (5.0 - volt_drop) / r * 1000.0 <= limit_ma:
            return r
    return COMMON_RESISTORS[-1]


def fmt_ohm(r):
    if r >= 1000:
        return "%g kΩ" % (r / 1000.0)
    return "%d Ω" % r


# ---------------------------------------------------------------- spec

SPEC_KEYS = {"title", "board", "language", "parts", "steps", "existing", "lcd_text"}
PART_KEYS = {"id", "type", "label", "pin", "pins", "color", "column"}
STEP_KEYS = {"title", "parts", "wires", "note", "usb"}


def load_spec(path):
    p = Path(path)
    try:
        if p.stat().st_size > MAX_SPEC_BYTES:
            raise SpecError("spec file is larger than %d bytes" % MAX_SPEC_BYTES)
        raw = p.read_bytes()
    except OSError as exc:
        raise SpecError("cannot read %s: %s" % (path, exc.strerror or exc))
    if len(raw) > MAX_SPEC_BYTES:
        raise SpecError("spec file is larger than %d bytes" % MAX_SPEC_BYTES)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SpecError("spec is not valid UTF-8 JSON: %s" % exc)
    return validate(data)


def validate(data):
    if not isinstance(data, dict):
        raise SpecError("spec must be a JSON object")
    unknown = set(data) - SPEC_KEYS
    if unknown:
        raise SpecError("unknown spec keys: %s" % ", ".join(sorted(unknown)))
    title = clean_text(data.get("title", ""), "title", 80)
    board = data.get("board", "uno-r4-wifi")
    if board not in BOARDS:
        raise SpecError("board must be one of: %s" % ", ".join(BOARDS))
    lang = data.get("language", "en")
    if lang not in STR:
        raise SpecError("language must be one of: %s" % ", ".join(STR))
    lcd_text = data.get("lcd_text", ["Hello,", "breadboard!"])
    if (not isinstance(lcd_text, list) or len(lcd_text) != 2):
        raise SpecError("lcd_text must be a list of two lines")
    lcd_text = [clean_text(t, "lcd_text", 16) if t else " " for t in lcd_text]

    parts_in = data.get("parts")
    if not isinstance(parts_in, list) or not parts_in:
        raise SpecError("parts must be a non-empty list")
    if len(parts_in) > MAX_PARTS:
        raise SpecError("at most %d parts per guide" % MAX_PARTS)

    parts, ids, used_pins = [], set(), {}
    for i, raw in enumerate(parts_in):
        where = "parts[%d]" % i
        if not isinstance(raw, dict):
            raise SpecError("%s must be an object" % where)
        unknown = set(raw) - PART_KEYS
        if unknown:
            raise SpecError("%s: unknown keys %s" % (where, ", ".join(sorted(unknown))))
        pid = raw.get("id")
        if not isinstance(pid, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,15}", pid) or pid == "rails":
            raise SpecError("%s: id must be 1-16 lowercase letters, digits or _, starting with a letter (not 'rails')" % where)
        if pid in ids:
            raise SpecError("duplicate part id %r" % pid)
        ids.add(pid)
        ptype = raw.get("type")
        if ptype not in PARTS:
            raise SpecError("%s: type must be one of: %s" % (where, ", ".join(PARTS)))
        info = PARTS[ptype]
        label = clean_text(raw["label"], where + ".label", 24) if "label" in raw else None
        color = raw.get("color", "red")
        if ptype == "led":
            if color not in LED_COLORS:
                raise SpecError("%s: LED color must be one of: %s" % (where, ", ".join(LED_COLORS)))
        elif "color" in raw:
            raise SpecError("%s: color only applies to LEDs" % where)
        column = raw.get("column")
        if column is not None:
            if info["kind"] == "module":
                raise SpecError("%s: column only applies to breadboard parts" % where)
            if not isinstance(column, int) or isinstance(column, bool):
                raise SpecError("%s: column must be a whole number" % where)
        given = {}
        if "pin" in raw and "pins" in raw:
            raise SpecError("%s: use pin or pins, not both" % where)
        if "pin" in raw:
            if len(info["signals"]) != 1:
                raise SpecError("%s: this part has several signals, use pins: {...}" % where)
            given[next(iter(info["signals"]))] = raw["pin"]
        elif "pins" in raw:
            if not isinstance(raw["pins"], dict):
                raise SpecError("%s: pins must be an object" % where)
            for k, v in raw["pins"].items():
                key = str(k).upper()
                if key not in info["signals"]:
                    raise SpecError("%s: %s has no signal %r (it has %s)" % (where, ptype, k, ", ".join(info["signals"])))
                given[key] = v
        pins = {}
        for sig, kind in info["signals"].items():
            if kind in ("sda", "scl"):
                if sig in given and norm_pin(given[sig], where) != kind.upper():
                    raise SpecError("%s: %s must go to the %s pin" % (where, sig, kind.upper()))
                pins[sig] = kind.upper()
                continue
            if sig in given:
                pin = norm_pin(given[sig], "%s.%s" % (where, sig))
                if pin in ("SDA", "SCL"):
                    raise SpecError("%s: SDA and SCL are for I2C modules only" % where)
                if kind == "analog" and not pin.startswith("A"):
                    raise SpecError("%s: %s needs an analog pin (A0-A5)" % (where, sig))
                pins[sig] = pin
        parts.append({"id": pid, "type": ptype, "label": label, "color": color,
                      "column": column, "pins": pins, "index": i})

    # pin bookkeeping: fixed pins first, then auto ones
    i2c = any(p["type"] == "lcd1602-i2c" for p in parts)
    if sum(1 for p in parts if p["type"] == "lcd1602-i2c") > 1:
        raise SpecError("only one I2C module per guide in this version")
    for p in parts:
        for sig, pin in p["pins"].items():
            if pin in used_pins:
                raise SpecError("pin %s is used twice (%s and %s.%s)" % (pin, used_pins[pin], p["id"], sig))
            used_pins[pin] = "%s.%s" % (p["id"], sig)
    if i2c:
        for bad in ("A4", "A5"):
            if bad in used_pins:
                raise SpecError("%s is the same signal as SDA/SCL: with an I2C module, pick another pin for %s" % (bad, used_pins[bad]))
    free_analog = [a for a in ANALOG_HEADER if a not in used_pins and not (i2c and a in ("A4", "A5"))]
    free_digital = [d for d in AUTO_DIGITAL if d not in used_pins]
    for p in parts:
        for sig, kind in PARTS[p["type"]]["signals"].items():
            if sig in p["pins"]:
                continue
            pool = free_analog if kind in ("analog", "analog-preferred") else free_digital
            if not pool and kind == "analog-preferred":
                pool = free_digital
            if not pool:
                raise SpecError("no free %s pin left for %s.%s" % ("analog" if kind == "analog" else "digital", p["id"], sig))
            pin = pool.pop(0)
            p["pins"][sig] = pin
            used_pins[pin] = "%s.%s" % (p["id"], sig)

    existing = data.get("existing", [])
    if not isinstance(existing, list):
        raise SpecError("existing must be a list of part ids")
    for e in existing:
        if e != "rails" and e not in ids:
            raise SpecError("existing: unknown part %r" % e)
    existing = set(existing)

    steps = data.get("steps")
    if steps is not None:
        if not isinstance(steps, list) or not steps:
            raise SpecError("steps must be a non-empty list, or left out")
        if len(steps) > MAX_STEPS:
            raise SpecError("at most %d steps" % MAX_STEPS)
        clean_steps, seen_parts, seen_wires = [], set(), set()
        for i, s in enumerate(steps):
            where = "steps[%d]" % i
            if not isinstance(s, dict):
                raise SpecError("%s must be an object" % where)
            unknown = set(s) - STEP_KEYS
            if unknown:
                raise SpecError("%s: unknown keys %s" % (where, ", ".join(sorted(unknown))))
            st = {"title": clean_text(s["title"], where + ".title", 60) if "title" in s else None,
                  "note": clean_text(s["note"], where + ".note", 200) if "note" in s else None,
                  "parts": [], "wires": [], "force_unplug": False}
            usb = s.get("usb")
            if usb not in (None, "unplug"):
                raise SpecError("%s.usb can only be \"unplug\": the script decides when USB may stay plugged in" % where)
            st["force_unplug"] = usb == "unplug"
            for pid in s.get("parts", []):
                if pid != "rails" and pid not in ids:
                    raise SpecError("%s: unknown part %r" % (where, pid))
                if pid in existing:
                    raise SpecError("%s: %r is listed as existing" % (where, pid))
                if pid in seen_parts:
                    raise SpecError("%s: part %r is placed in two steps" % (where, pid))
                seen_parts.add(pid)
                st["parts"].append(pid)
            for w in s.get("wires", []):
                if not isinstance(w, str) or "." not in w:
                    raise SpecError("%s: wires take names like joy.SW" % where)
                if w in seen_wires:
                    raise SpecError("%s: wire %r is claimed twice" % (where, w))
                seen_wires.add(w)
                st["wires"].append(w)
            if not st["parts"] and not st["wires"]:
                raise SpecError("%s has no parts and no wires" % where)
            clean_steps.append(st)
        missing = [p["id"] for p in parts if p["id"] not in seen_parts and p["id"] not in existing]
        if missing:
            raise SpecError("these parts are in no step: %s" % ", ".join(missing))
        steps = clean_steps
    return {"title": title, "board": board, "lang": lang, "parts": parts,
            "existing": existing, "steps": steps, "lcd_text": lcd_text}


# ---------------------------------------------------------------- layout

class Layout:
    """Fixed geometry. Everything sits on the 9 px grid."""
    title_y = 40
    route_top = 117
    bx0, by0, bw, bh = 54, 243, 414, 252
    bb_x0, bb_y0 = 576, 594
    mod_bottom = 459
    panel = (54, 603, 540, 918)

    def __init__(self):
        self.bx1 = self.bx0 + self.bw
        self.by1 = self.by0 + self.bh
        self.top_hdr_y = self.by0 + 18
        self.bot_hdr_y = self.by1 - 18
        self.pins = {}
        x = self.bx0 + 72
        for name in TOP_HEADER:
            if name:
                self.pins["GND.top" if name == "GND" else name] = (x, self.top_hdr_y, 0)
            x += PITCH
        x = self.bx0 + 54
        for name in POWER_HEADER:
            self.pins.setdefault(name, (x, self.bot_hdr_y, 2))
            x += PITCH
        x += 36
        for name in ANALOG_HEADER:
            self.pins[name] = (x, self.bot_hdr_y, 2)
            x += PITCH
        # breadboard rows
        b = self.bb_y0
        self.rail_y = {("top", "+"): b + 18, ("top", "-"): b + 36,
                       ("bottom", "-"): b + 288, ("bottom", "+"): b + 306}
        self.row_y = {r: b + 72 + i * PITCH for i, r in enumerate("abcde")}
        self.row_y.update({r: b + 180 + i * PITCH for i, r in enumerate("fghij")})
        self.gutter_y = b + 162
        self.col_x0 = self.bb_x0 + 45
        self.bb_x1 = self.col_x0 + 29 * PITCH + 45
        self.bb_y1 = b + 324
        self.width = max(1242, self.bb_x1 + 54)
        self.modules = {}

    def col_x(self, col):
        return self.col_x0 + (col - 1) * PITCH

    def hole(self, row, col):
        return (self.col_x(col), self.row_y[row])

    def rail(self, side, pol, col):
        return (self.col_x(col), self.rail_y[(side, pol)])


def place(project):
    """Breadboard columns, module positions, resistor values."""
    lay = Layout()
    pin_ma = BOARDS[project["board"]]["pin_ma"]
    col = 3
    taken = set()
    for p in project["parts"]:
        info = PARTS[p["type"]]
        if p["type"] == "led":
            p["resistor"] = pick_resistor(LED_COLORS[p["color"]][0], pin_ma)
        elif p["type"] in ("buzzer", "speaker"):
            p["resistor"] = pick_resistor(0.0, pin_ma)
        else:
            p["resistor"] = None
        if info["kind"] in ("bb", "mixed"):
            w = info["width"]
            if p["column"] is not None:
                c = p["column"]
            else:
                c = col
                while any(x in taken for x in range(c - 1, c + w + 1)):
                    c += 1
            if c < 3 or c + w - 1 > 28:
                raise SpecError("%s does not fit on the breadboard (columns 3 to 28 are free for parts)" % p["id"])
            if any(x in taken for x in range(c, c + w)):
                raise SpecError("%s overlaps another part on the breadboard" % p["id"])
            taken.update(range(c, c + w))
            p["col"] = c
            col = c + w + 1
    # modules in a row above the breadboard
    x = lay.col_x0 - 2 * PITCH  # pins line up with breadboard columns
    for p in project["parts"]:
        info = PARTS[p["type"]]
        if "module" in info:
            m = info["module"]
            lay.modules[p["id"]] = (x, lay.mod_bottom - m["h"], m["w"], m["h"])
            x += m["w"] + 36
    lay.width = max(lay.width, x + 18)
    return lay


# ---------------------------------------------------------------- wires

def build_wires(project, lay):
    """Every wire with its endpoints and the step it belongs to."""
    parts = project["parts"]
    wires = []
    plus_used, minus_used = set([1, 2, 30]), set([1, 2, 30])

    def add(wid, part, a, b, role, direct=False, sig=None):
        wires.append({"id": wid, "part": part, "a": a, "b": b, "role": role,
                      "direct": direct, "sig": sig})

    def use_col(c):
        plus_used.add(c)
        minus_used.add(c)

    add("rails.5V", "rails", ("pin", "5V"), ("rail", "top", "+", 1), "power")
    add("rails.GND", "rails", ("pin", "GND"), ("rail", "top", "-", 2), "ground")
    needs_bridge = None
    for p in parts:
        t, pid = p["type"], p["id"]
        if t == "button":
            c = p["col"]
            add(pid + ".SIG", pid, ("pin", p["pins"]["SIG"]), ("hole", "a", c), "signal", sig="SIG")
            add(pid + ".GND", pid, ("hole", "j", c + 2), ("rail", "bottom", "-", c + 2), "ground", direct=True)
            use_col(c)
            needs_bridge = needs_bridge or pid
        elif t in ("led", "buzzer"):
            c = p["col"]
            add(pid + ".SIG", pid, ("pin", p["pins"]["SIG"]), ("hole", "a", c), "signal", sig="SIG")
            add(pid + ".GND", pid, ("hole", "a", c + 4), ("rail", "top", "-", c + 4), "ground", direct=True)
            use_col(c)
            minus_used.add(c + 4)
        elif t == "potentiometer":
            c = p["col"]
            add(pid + ".VCC", pid, ("hole", "a", c), ("rail", "top", "+", c), "power", direct=True)
            add(pid + ".WIPER", pid, ("pin", p["pins"]["WIPER"]), ("hole", "a", c + 1), "signal", sig="WIPER")
            add(pid + ".GND", pid, ("hole", "a", c + 2), ("rail", "top", "-", c + 2), "ground", direct=True)
            for x in (c, c + 1):
                use_col(x)
            minus_used.add(c + 2)
        elif t == "speaker":
            c = p["col"]
            add(pid + ".SIG", pid, ("pin", p["pins"]["SIG"]), ("hole", "a", c), "signal", sig="SIG")
            use_col(c)
            use_col(c + 3)
    # module wires, after every breadboard column is known
    for p in parts:
        t, pid = p["type"], p["id"]
        info = PARTS[t]
        if t == "speaker":
            c = p["col"]
            add(pid + ".+", pid, ("mod", pid, "+"), ("hole", "a", c + 3), "speaker+")
            add(pid + ".-", pid, ("mod", pid, "-"), ("rail", "top", "-", None), "ground")
        elif info["kind"] == "module":
            for pin in info["module"]["pins"]:
                if pin in info["power"]:
                    pol = info["power"][pin]
                    add("%s.%s" % (pid, pin), pid, ("mod", pid, pin), ("rail", "top", pol, None),
                        "power" if pol == "+" else "ground")
                else:
                    board_pin = p["pins"][pin]
                    role = "sda" if board_pin == "SDA" else "scl" if board_pin == "SCL" else "signal"
                    add("%s.%s" % (pid, pin), pid, ("mod", pid, pin), ("pin", board_pin), role, sig=pin)
    # rail columns for module power wires: nearest free column under the pin
    for w in wires:
        b = w["b"]
        if b[0] == "rail" and b[3] is None:
            px = module_pin_xy(lay, project, w["a"])[0]
            pol = b[2]
            best = None
            for c in range(1, 31):
                if c in plus_used or (pol == "-" and c in minus_used):
                    continue
                d = abs(lay.col_x(c) - px)
                if best is None or d < best[0]:
                    best = (d, c)
            if best is None:
                raise SpecError("no free rail column left for %s" % w["id"])
            c = best[1]
            w["b"] = ("rail", "top", pol, c)
            plus_used.add(c)
            if pol == "-":
                minus_used.add(c)
    if needs_bridge:
        add("rails.bridge", needs_bridge, ("rail", "top", "-", 30), ("rail", "bottom", "-", 30),
            "ground", direct=True)
    return wires


def module_pin_xy(lay, project, ep):
    _, pid, pin = ep
    x, y, w, h = lay.modules[pid]
    ptype = next(p["type"] for p in project["parts"] if p["id"] == pid)
    i = PARTS[ptype]["module"]["pins"].index(pin)
    return (x + 18 * (i + 1), y + h)


def endpoint_xy(lay, project, ep):
    kind = ep[0]
    if kind == "pin":
        x, y, _ = lay.pins[ep[1]]
        return (x, y)
    if kind == "hole":
        return lay.hole(ep[1], ep[2])
    if kind == "rail":
        return lay.rail(ep[1], ep[2], ep[3])
    return module_pin_xy(lay, project, ep)


def endpoint_exit(lay, ep):
    """Direction the wire leaves the endpoint: 0 up, 1 right, 2 down, 3 left."""
    if ep[0] == "pin":
        return lay.pins[ep[1]][2]
    if ep[0] == "mod":
        return 2
    return 0  # breadboard holes and top rails are reached from above


def stub_tip(lay, xy, d, ep):
    x, y = xy
    if d == 0:
        top = lay.bb_y0 if ep[0] in ("hole", "rail") else lay.by0
        return (x, top - GRID)
    if d == 2:
        bottom = lay.by1 if ep[0] == "pin" else y
        return (x, bottom + (GRID if ep[0] == "pin" else 2 * GRID))
    raise ValueError(d)


# ---------------------------------------------------------------- router

DIRS = [(0, -1), (1, 0), (0, 1), (-1, 0)]
STEP_COST, BEND_COST, CROSS_COST, NEAR_COST = 1.0, 3.0, 5.0, 0.6


class Router:
    def __init__(self, width, height, top):
        self.w = width // GRID
        self.h = height // GRID
        self.top = top // GRID
        self.blocked = set()
        self.occ = {}

    def block_rect(self, x0, y0, x1, y1):
        for cx in range(x0 // GRID, -(-x1 // GRID) + 1):
            for cy in range(y0 // GRID, -(-y1 // GRID) + 1):
                self.blocked.add((cx, cy))

    def block_line(self, p, q):
        (x0, y0), (x1, y1) = p, q
        cx0, cy0, cx1, cy1 = x0 // GRID, y0 // GRID, x1 // GRID, y1 // GRID
        for cx in range(min(cx0, cx1), max(cx0, cx1) + 1):
            for cy in range(min(cy0, cy1), max(cy0, cy1) + 1):
                self.blocked.add((cx, cy))

    def inside(self, c):
        return 1 <= c[0] < self.w - 1 and self.top <= c[1] < self.h - 1

    def route(self, start, sdir, goal, gdir_in, limit=400000):
        s, g = (start[0] // GRID, start[1] // GRID), (goal[0] // GRID, goal[1] // GRID)
        heap = [(abs(s[0] - g[0]) + abs(s[1] - g[1]), 0.0, 0, s, sdir)]
        best = {(s, sdir): 0.0}
        came = {}
        n = 0
        while heap:
            _, cost, _, cell, d = heapq.heappop(heap)
            if cell == g:
                return self._finish(came, (cell, d), s, sdir)
            if cost > best.get((cell, d), 1e18):
                continue
            n += 1
            if n > limit:
                break
            here_busy = cell in self.occ
            for nd in range(4):
                if nd == (d + 2) % 4:
                    continue
                if here_busy and nd != d:
                    continue  # no bends on top of another wire
                nxt = (cell[0] + DIRS[nd][0], cell[1] + DIRS[nd][1])
                if not self.inside(nxt):
                    continue
                axis = "v" if nd in (0, 2) else "h"
                step = STEP_COST + (BEND_COST if nd != d else 0.0)
                if nxt == g:
                    step += BEND_COST if nd != gdir_in else 0.0
                else:
                    if nxt in self.blocked:
                        continue
                    o = self.occ.get(nxt)
                    if o:
                        if axis in o:
                            continue
                        step += CROSS_COST
                    for side in ((1, 0), (-1, 0)) if axis == "v" else ((0, 1), (0, -1)):
                        o2 = self.occ.get((nxt[0] + side[0], nxt[1] + side[1]))
                        if o2 and axis in o2:
                            step += NEAR_COST
                nc = cost + step
                key = (nxt, nd)
                if nc < best.get(key, 1e18):
                    best[key] = nc
                    came[key] = (cell, d)
                    h = abs(nxt[0] - g[0]) + abs(nxt[1] - g[1])
                    heapq.heappush(heap, (nc + h, nc, len(best), nxt, nd))
        return None

    def _finish(self, came, state, s, sdir):
        cells = []
        dirs = []
        while state in came:
            cells.append(state[0])
            dirs.append(state[1])
            state = came[state]
        cells.append(s)
        dirs.append(sdir)
        cells.reverse()
        dirs.reverse()
        # occupancy: a bend takes the whole cell
        for i, c in enumerate(cells):
            d_in = dirs[i]
            d_out = dirs[i + 1] if i + 1 < len(dirs) else d_in
            axes = {"v" if d_in in (0, 2) else "h", "v" if d_out in (0, 2) else "h"}
            self.occ.setdefault(c, set()).update(axes)
        return [(c[0] * GRID, c[1] * GRID) for c in cells]


def simplify(points):
    out = []
    for p in points:
        if out and out[-1] == p:
            continue
        if len(out) >= 2:
            a, b = out[-2], out[-1]
            if (a[0] == b[0] == p[0]) or (a[1] == b[1] == p[1]):
                out[-1] = p
                continue
        out.append(p)
    return out


def route_all(project, lay, wires):
    height = lay.bb_y1 + GRID * 2
    r = Router(lay.width, height, lay.route_top)
    r.block_rect(lay.bx0 - GRID * 2, lay.by0, lay.bx1, lay.by1)
    r.block_rect(lay.bb_x0, lay.bb_y0, lay.bb_x1, lay.bb_y1)
    r.block_rect(*lay.panel)
    for (x, y, w, h) in lay.modules.values():
        r.block_rect(x, y, x + w, y + h)
    plans = []
    for w in wires:
        if w["direct"]:
            w["path"] = [endpoint_xy(lay, project, w["a"]), endpoint_xy(lay, project, w["b"])]
            continue
        ends = []
        for ep in (w["a"], w["b"]):
            xy = endpoint_xy(lay, project, ep)
            d = endpoint_exit(lay, ep)
            tip = stub_tip(lay, xy, d, ep)
            ends.append((xy, d, tip))
            r.block_line(xy, tip)
        plans.append((w, ends))
    for w, ends in plans:
        (axy, ad, atip), (bxy, bd, btip) = ends
        path = r.route(atip, ad, btip, (bd + 2) % 4)
        if path is None:
            raise SpecError("could not draw wire %s without overlaps: try fewer parts or other pins" % w["id"])
        w["path"] = simplify([axy] + path + [bxy])
    return height


# ---------------------------------------------------------------- steps

def wire_power_like(w):
    return w["role"] in ("power", "ground") or w["a"][0] == "rail" or w["b"][0] == "rail" \
        or (w["a"][0] == "pin" and w["a"][1] in POWER_PINS) or (w["b"][0] == "pin" and w["b"][1] in POWER_PINS)


def build_steps(project, wires):
    T = STR[project["lang"]]
    parts = {p["id"]: p for p in project["parts"]}
    existing = project["existing"]
    by_part = {}
    for w in wires:
        by_part.setdefault(w["part"], []).append(w)
    claimed = {}
    if project["steps"] is None:
        spec_steps = []
        if "rails" not in existing:
            spec_steps.append({"title": None, "parts": ["rails"], "wires": [], "note": None, "force_unplug": False})
        for p in project["parts"]:
            if p["id"] not in existing:
                spec_steps.append({"title": None, "parts": [p["id"]], "wires": [], "note": None, "force_unplug": False})
    else:
        spec_steps = [dict(s) for s in project["steps"]]
        placed = {pid for s in spec_steps for pid in s["parts"]}
        if "rails" not in placed and "rails" not in existing:
            spec_steps.insert(0, {"title": None, "parts": ["rails"], "wires": [], "note": None, "force_unplug": False})
    wire_ids = {w["id"]: w for w in wires}
    for i, s in enumerate(spec_steps):
        for wid in s["wires"]:
            if wid not in wire_ids:
                raise SpecError("step %d: no wire named %r (see the check output for wire names)" % (i + 1, wid))
            claimed[wid] = i
    # which step each wire lands in
    part_step = {}
    for i, s in enumerate(spec_steps):
        for pid in s["parts"]:
            part_step[pid] = i
    for w in wires:
        if w["id"] in claimed:
            si = claimed[w["id"]]
            ps = part_step.get(w["part"], -1)
            if w["part"] not in existing and ps > si:
                raise SpecError("wire %s is claimed before its part is placed" % w["id"])
            w["step"] = si
        elif w["part"] in existing:
            w["step"] = -1
        elif w["id"] == "rails.bridge" and "rails" in part_step and part_step["rails"] < part_step.get(w["part"], 0):
            w["step"] = part_step[w["part"]]
        else:
            w["step"] = part_step.get(w["part"], -1)
    steps = []
    for i, s in enumerate(spec_steps):
        new_parts = s["parts"]
        new_wires = [w for w in wires if w["step"] == i]
        unplug = s["force_unplug"] or bool(new_parts) or any(wire_power_like(w) for w in new_wires)
        if s["title"]:
            title = s["title"]
        elif new_parts == ["rails"]:
            title = T["rails_title"]
        elif new_parts:
            names = [part_name(project, parts[p]) if p != "rails" else T["rails_title"] for p in new_parts]
            title = T["add"].format(name=", ".join(names))
        else:
            title = T["connect"].format(what=", ".join(w["id"] for w in new_wires))
        steps.append({"title": title, "parts": new_parts, "wires": new_wires,
                      "unplug": unplug, "note": s["note"]})
    if not steps:
        raise SpecError("nothing to wire: every part is marked existing")
    return steps


def part_name(project, p):
    T = STR[project["lang"]]
    base = T["names"][p["type"]]
    if p["label"]:
        return '%s "%s"' % (base, p["label"])
    return base


# ---------------------------------------------------------------- colours

def assign_colors(wires):
    i = 0
    for w in wires:
        role = w["role"]
        if role in ("power", "speaker+"):
            w["color"], w["cname"] = POWER_RED, "red"
        elif role == "ground":
            w["color"], w["cname"] = GND_BLACK, "black"
        elif role == "sda":
            w["color"], w["cname"] = SDA_GREEN, "green"
        elif role == "scl":
            w["color"], w["cname"] = SCL_YELLOW, "yellow"
        else:
            name, hexv = SIGNAL_COLORS[i % len(SIGNAL_COLORS)]
            w["color"], w["cname"] = hexv, name
            i += 1


def describe_ep(project, lay, ep, which=0):
    """which: 0 = where the wire starts, 1 = where it ends."""
    E = STR[project["lang"]]["ep"]
    if ep[0] == "pin":
        return E["pin"][which].format(pin=ep[1])
    if ep[0] == "hole":
        return E["hole"][which].format(hole="%s%d" % (ep[1], ep[2]))
    if ep[0] == "rail":
        key = "top_rail" if ep[1] == "top" else "bottom_rail"
        return E[key][which].format(pol=ep[2], col=ep[3])
    p = next(p for p in project["parts"] if p["id"] == ep[1])
    return E["mod_pin"][which].format(pin=ep[2], name=part_name(project, p))


def describe_wire(project, lay, w):
    T = STR[project["lang"]]
    key = "jumper" if w["direct"] else "wire"
    color = T["colors"][w["cname"]]
    if project["lang"] == "en" and key == "jumper":
        color = color.lower()
    return T[key].format(color=color, a=describe_ep(project, lay, w["a"], 0),
                         b=describe_ep(project, lay, w["b"], 1))


# ---------------------------------------------------------------- svg

FONT = "Menlo, Consolas, 'DejaVu Sans Mono', monospace"
FONT_HEAD = "'Helvetica Neue', Arial, sans-serif"


class Svg:
    def __init__(self):
        self.out = []

    def add(self, s):
        self.out.append(s)

    def text(self, x, y, s, size=12, fill=INK, weight="normal", anchor="start", font=FONT, rotate=None, opacity=None):
        tr = ' transform="rotate(%d %s %s)"' % (rotate, x, y) if rotate else ""
        op = ' opacity="%s"' % opacity if opacity is not None else ""
        self.add('<text x="%s" y="%s" font-family="%s" font-size="%s" font-weight="%s" fill="%s" text-anchor="%s"%s%s>%s</text>'
                 % (x, y, xml(font), size, weight, fill, anchor, tr, op, xml(s)))

    def rect(self, x, y, w, h, fill, stroke="none", rx=0, sw=1, extra=""):
        self.add('<rect x="%s" y="%s" width="%s" height="%s" rx="%s" fill="%s" stroke="%s" stroke-width="%s"%s/>'
                 % (x, y, w, h, rx, fill, stroke, sw, extra))

    def circle(self, x, y, r, fill, stroke="none", sw=1):
        self.add('<circle cx="%s" cy="%s" r="%s" fill="%s" stroke="%s" stroke-width="%s"/>' % (x, y, r, fill, stroke, sw))

    def line(self, x1, y1, x2, y2, stroke, sw=2, extra=""):
        self.add('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="%s"%s/>' % (x1, y1, x2, y2, stroke, sw, extra))


def draw_board(svg, project, lay):
    b = BOARDS[project["board"]]
    svg.rect(lay.bx0 - 18, lay.by0 + 54, 36, 45, "#B8BEC4", "#7D848A", rx=4)
    svg.rect(lay.bx0, lay.by0, lay.bw, lay.bh, "#22708C", "#164E63", rx=12, sw=2)
    svg.text(lay.bx0 + 207, lay.by0 + 120, b["name"], size=24, fill="#EAF4F7", weight="bold", anchor="middle", font=FONT_HEAD)
    T = STR[project["lang"]]
    svg.text(lay.bx0 + 207, lay.by0 + 142, T["board_sub"], size=11, fill="#EAF4F7", anchor="middle")
    svg.text(lay.bx0 - 18, lay.by0 + 46, "USB", size=10, fill=INK2, anchor="middle")
    # headers
    svg.rect(lay.bx0 + 72 - 9, lay.top_hdr_y - 9, len(TOP_HEADER) * PITCH, 18, "#111111", rx=2)
    for name in TOP_HEADER:
        if not name:
            continue
        x, y, _ = lay.pins["GND.top" if name == "GND" else name]
        svg.rect(x - 4, y - 4, 8, 8, "#D9D9D9")
        svg.text(x + 4, y + 16, name[1:] if re.fullmatch(r"D\d+", name) else name, size=10,
                 fill="#EAF4F7", rotate=90)
    svg.rect(lay.bx0 + 54 - 9, lay.bot_hdr_y - 9, len(POWER_HEADER) * PITCH, 18, "#111111", rx=2)
    x = lay.bx0 + 54
    for name in POWER_HEADER:
        svg.rect(x - 4, lay.bot_hdr_y - 4, 8, 8, "#D9D9D9")
        svg.text(x + 4, lay.bot_hdr_y - 16, name, size=10, fill="#EAF4F7", rotate=-90)
        x += PITCH
    ax = lay.pins["A0"][0]
    svg.rect(ax - 9, lay.bot_hdr_y - 9, len(ANALOG_HEADER) * PITCH, 18, "#111111", rx=2)
    for name in ANALOG_HEADER:
        x = lay.pins[name][0]
        svg.rect(x - 4, lay.bot_hdr_y - 4, 8, 8, "#D9D9D9")
        svg.text(x + 4, lay.bot_hdr_y - 16, name, size=10, fill="#EAF4F7", rotate=-90)
    svg.text(lay.bx1 - 12, lay.top_hdr_y + 62, "DIGITAL", size=10, fill="#CFE3EA", anchor="end")
    svg.text(lay.bx0 + 54, lay.bot_hdr_y - 66, "POWER", size=10, fill="#CFE3EA")
    svg.text(ax, lay.bot_hdr_y - 66, "ANALOG", size=10, fill="#CFE3EA")


def draw_breadboard(svg, project, lay):
    T = STR[project["lang"]]
    svg.rect(lay.bb_x0, lay.bb_y0, lay.bb_x1 - lay.bb_x0, lay.bb_y1 - lay.bb_y0, "#F3EFE6", "#D8D2C4", rx=8, sw=2)
    x0, x1 = lay.col_x(1) - 14, lay.col_x(30) + 14
    for (side, pol), y in lay.rail_y.items():
        color = POWER_RED if pol == "+" else "#2C63D6"
        off = -9 if (side, pol) in (("top", "+"),) else 9 if (side, pol) == ("bottom", "+") else (9 if side == "top" else -9)
        svg.line(x0, y + off, x1, y + off, color, 2)
        svg.text(x0 - 14, y + 4, "+" if pol == "+" else "−", size=14, fill=color, weight="bold", anchor="middle")
        for c in range(1, 31):
            svg.rect(lay.col_x(c) - 3, y - 3, 6, 6, "#8C877A", rx=1)
    svg.rect(lay.bb_x0 + 10, lay.gutter_y - 7, lay.bb_x1 - lay.bb_x0 - 20, 14, "#E7E2D6")
    for c in range(1, 31):
        x = lay.col_x(c)
        for r in "abcdefghij":
            svg.rect(x - 3, lay.row_y[r] - 3, 6, 6, "#8C877A", rx=1)
        svg.text(x, lay.row_y["a"] - 13, str(c), size=9, fill=INK2, anchor="middle")
        svg.text(x, lay.row_y["j"] + 19, str(c), size=9, fill=INK2, anchor="middle")
    for r in "abcdefghij":
        svg.text(lay.col_x(1) - 22, lay.row_y[r] + 4, r, size=10, fill=INK2, anchor="middle")
        svg.text(lay.col_x(30) + 22, lay.row_y[r] + 4, r, size=10, fill=INK2, anchor="middle")
    svg.text(lay.bb_x1 - 8, lay.bb_y1 + 16, T["breadboard"], size=10, fill=INK2, anchor="end")


def draw_resistor(svg, x1, x2, y, value):
    svg.line(x1, y, x2, y, "#8A8A8A", 3)
    mx = (x1 + x2) / 2
    svg.rect(mx - 17, y - 7, 34, 14, "#D9C7A0", "#8A7A55", rx=5)
    bands = list(RESISTOR_BANDS.get(value, ("black", "black", "black"))) + ["gold"]
    for i, b in enumerate(bands):
        bx = mx - 11 + i * 6 + (3 if i == 3 else 0)
        svg.rect(bx, y - 7, 3, 14, BAND_HEX[b])
    for x in (x1, x2):
        svg.circle(x, y, 3.5, "#B0B0B0", "#555555")


def draw_bb_part(svg, project, lay, p, highlight):
    t, c = p["type"], p.get("col")
    hx = lay.col_x
    if t == "button":
        cx, cy = (hx(c) + hx(c + 2)) / 2, lay.gutter_y
        if highlight:
            svg.rect(cx - 30, cy - 30, 60, 60, "none", "#F2A900", rx=8, sw=2.5, extra=' stroke-dasharray="6 4"')
        svg.rect(cx - 22, cy - 22, 44, 44, "#2A2A2A", rx=5)
        svg.rect(cx - 15, cy - 15, 30, 30, "#C9C9C9", rx=3)
        svg.circle(cx, cy, 8, "#1A1A1A")
        for (col, row) in ((c, "e"), (c + 2, "e"), (c, "f"), (c + 2, "f")):
            svg.circle(hx(col), lay.row_y[row], 3.5, "#B0B0B0", "#555555")
        bb_label(svg, lay, p, c, 3)
    elif t in ("led", "buzzer", "speaker"):
        if highlight:
            svg.rect(hx(c) - 12, lay.row_y["a"] - 28, hx(c + (4 if t != "speaker" else 3)) - hx(c) + 24, 96, "none", "#F2A900", rx=8, sw=2.5, extra=' stroke-dasharray="6 4"')
        draw_resistor(svg, hx(c), hx(c + 3), lay.row_y["e"], p["resistor"])
        if t in ("led", "buzzer"):
            xa, xk, y = hx(c + 3), hx(c + 4), lay.row_y["d"]
            mx = (xa + xk) / 2
            if t == "led":
                svg.line(xa, y, mx - 3, lay.row_y["b"] + 4, "#9A9A9A", 2)
                svg.line(xk, y, mx + 3, lay.row_y["b"] + 4, "#9A9A9A", 2)
                fill = LED_COLORS[p["color"]][1]
                svg.add('<path d="M %s %s a 8 8 0 0 1 16 0 v 6 h -16 z" fill="%s" stroke="#555555" stroke-width="1"/>'
                        % (mx - 8, lay.row_y["b"] - 2, fill))
                svg.text(xa - 7, lay.row_y["c"] + 4, "+", size=10, fill=INK, weight="bold", anchor="middle")
            else:
                svg.circle(mx, lay.row_y["b"] + 4, 12, "#1E1E1E", "#000000")
                svg.circle(mx, lay.row_y["b"] + 4, 3, "#555555")
                svg.text(xa - 9, lay.row_y["c"] + 8, "+", size=10, fill=INK, weight="bold", anchor="middle")
            for x in (xa, xk):
                svg.circle(x, y, 3.5, "#B0B0B0", "#555555")
        bb_label(svg, lay, p, c, PARTS[t]["width"])
    elif t == "potentiometer":
        x0, y = hx(c), lay.row_y["e"]
        if highlight:
            svg.rect(x0 - 12, lay.row_y["a"] - 28, 2 * PITCH + 24, 96, "none", "#F2A900", rx=8, sw=2.5, extra=' stroke-dasharray="6 4"')
        svg.rect(x0 - 8, lay.row_y["b"] - 4, 2 * PITCH + 16, 44, "#2F5DA8", "#1D3E73", rx=4)
        svg.circle(x0 + PITCH, lay.row_y["c"] + 4, 13, "#E0E0E0", "#888888")
        svg.line(x0 + PITCH, lay.row_y["c"] - 6, x0 + PITCH, lay.row_y["c"] + 14, "#666666", 3)
        for i in range(3):
            svg.circle(x0 + i * PITCH, y, 3.5, "#B0B0B0", "#555555")
        bb_label(svg, lay, p, c, 3)


def bb_label(svg, lay, p, c, width):
    if not p["label"]:
        return
    room = max(3, int((width * PITCH + 6) / 6.6))
    text = p["label"] if len(p["label"]) <= room else p["label"][:room - 1] + "."
    cx = lay.col_x(c) + (width - 1) * PITCH / 2
    w = len(text) * 6.6 + 8
    y = lay.row_y["h"]
    svg.rect(cx - w / 2, y - 10, w, 16, "#FFFFFF", "#C9C2B2", rx=3)
    svg.text(cx, y + 2, text, size=11, fill=INK, weight="bold", anchor="middle")


def draw_module(svg, project, lay, p, highlight):
    x, y, w, h = lay.modules[p["id"]]
    t = p["type"]
    pins = PARTS[t]["module"]["pins"]
    if highlight:
        svg.rect(x - 10, y - 30, w + 20, h + 48, "none", "#F2A900", rx=10, sw=2.5, extra=' stroke-dasharray="6 4"')
    if t == "lcd1602-i2c":
        svg.rect(x, y, w, h, "#1E6B3A", "#144D2A", rx=6, sw=2)
        svg.rect(x + 22, y + 22, w - 44, 70, "#1B3FBF", "#0E2470", rx=3, sw=3)
        for i, line in enumerate(project["lcd_text"]):
            svg.text(x + 36, y + 50 + i * 26, line, size=16, fill="#E9F0FF")
    elif t == "joystick":
        svg.rect(x, y, w, h, "#B5262B", "#7D1A1E", rx=6, sw=2)
        svg.circle(x + w / 2, y + 66, 38, "#222222")
        svg.circle(x + w / 2, y + 66, 22, "#3A3A3A")
    elif t == "hc-sr04":
        svg.rect(x, y, w, h, "#1E5FA8", "#123D6E", rx=5, sw=2)
        svg.circle(x + 42, y + 38, 26, "#C9CBCE", "#666666")
        svg.circle(x + w - 42, y + 38, 26, "#C9CBCE", "#666666")
    elif t == "pir":
        svg.rect(x, y, w, h, "#1E6B3A", "#144D2A", rx=5, sw=2)
        svg.circle(x + w / 2, y + 52, 38, "#F3F3F0", "#BBBBBB")
        svg.circle(x + w / 2, y + 52, 22, "none", "#DDDDDD")
    elif t == "speaker":
        svg.rect(x, y, w, h, "#222222", "#000000", rx=10)
        svg.add('<ellipse cx="%s" cy="%s" rx="26" ry="34" fill="#333333" stroke="#555555"/>' % (x + w / 2, y + 48))
        svg.add('<ellipse cx="%s" cy="%s" rx="13" ry="18" fill="#111111"/>' % (x + w / 2, y + 48))
    # header strip and pins on the bottom edge
    svg.rect(x + 9, y + h - 12, 18 * len(pins) + 18, 16, "#111111", rx=2)
    for i, name in enumerate(pins):
        px = x + 18 * (i + 1)
        svg.rect(px - 4, y + h - 4, 8, 8, "#D9D9D9")
        label = {"-": "−"}.get(name, name)
        svg.text(px + 4, y + h - 16, label, size=10, fill="#FFFFFF", weight="bold", rotate=-90)
    name = part_name(project, p)
    svg.text(x, y - 10, name, size=11, fill=INK, weight="bold")


def wire_svg(svg, w, faded=False, bold=False):
    pts = w["path"]
    d = "M " + " L ".join("%s %s" % (round(x, 1), round(y, 1)) for x, y in pts)
    width = 4.5 if bold else 3.5
    if faded:
        svg.add('<path d="%s" fill="none" stroke="#9AA0A6" stroke-opacity=".55" stroke-width="%s" stroke-linecap="round" stroke-linejoin="round"/>' % (d, width - 1))
        return
    svg.add('<path d="%s" fill="none" stroke="#000000" stroke-opacity=".18" stroke-width="%s" stroke-linecap="round" stroke-linejoin="round"/>' % (d, width + 2.5))
    svg.add('<path d="%s" fill="none" stroke="%s" stroke-width="%s" stroke-linecap="round" stroke-linejoin="round"/>' % (d, w["color"], width))
    for (x, y) in (pts[0], pts[-1]):
        svg.circle(x, y, 4, w["color"], "#FFFFFF", 1.2)


def wrap(text, width):
    return textwrap.wrap(text, width) or [""]


def render_svg(project, lay, height, wires, steps, k=None):
    """k = step index, or None for the overview."""
    T = STR[project["lang"]]
    W = lay.width
    H = lay.bb_y1 + 36
    svg = Svg()
    svg.add('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d">' % (W, H, W, H))
    svg.rect(0, 0, W, H, PAPER)
    title = project["title"]
    if k is None:
        heading = T["overview"]
        unplug = None
    else:
        heading = "%s: %s" % (T["step"].format(n=k + 1, total=len(steps)), steps[k]["title"])
        unplug = steps[k]["unplug"]
    svg.text(36, 40, title, size=26, weight="bold", font=FONT_HEAD)
    svg.text(36, 66, heading[:110], size=15, fill=INK2, font=FONT_HEAD)
    if unplug is not None:
        bg, fg = ("#FDECEA", "#B3261E") if unplug else ("#E7F4EA", "#1E6B3A")
        msg = T["unplug"] if unplug else T["stay"]
        svg.rect(36, 78, 18 + 9 * len(msg) + 36, 30, bg, fg, rx=6, sw=1.5)
        if unplug:
            svg.add('<path d="M 52 101 L 62 84 L 72 101 Z" fill="%s"/>' % fg)
            svg.text(62, 99, "!", size=12, fill="#FFFFFF", weight="bold", anchor="middle")
        else:
            svg.circle(62, 93, 8, fg)
        svg.text(80, 98, msg, size=15, fill=fg, weight="bold", font=FONT_HEAD)
    draw_board(svg, project, lay)
    draw_breadboard(svg, project, lay)
    current_parts = set(steps[k]["parts"]) if k is not None else set()
    visible_steps = range(-1, (k if k is not None else len(steps) - 1) + 1)
    shown_parts = set(project["existing"])
    for i in visible_steps:
        if i >= 0:
            shown_parts.update(steps[i]["parts"])
    for p in project["parts"]:
        if p["id"] not in shown_parts:
            continue
        hl = p["id"] in current_parts
        if PARTS[p["type"]]["kind"] in ("bb", "mixed"):
            draw_bb_part(svg, project, lay, p, hl)
        if "module" in PARTS[p["type"]]:
            draw_module(svg, project, lay, p, hl)
    # wires: earlier ones faded, this step's in colour
    for w in wires:
        if w["step"] in visible_steps and (k is not None and w["step"] < k):
            wire_svg(svg, w, faded=True)
    for w in wires:
        if (k is None and w["step"] in visible_steps) or (k is not None and w["step"] == k):
            wire_svg(svg, w, bold=k is not None)
    # side panel
    px0, py0, px1, py1 = lay.panel
    svg.rect(px0, py0, px1 - px0, py1 - py0, "#FFFFFF", "#D5D9DE", rx=8, sw=1.5)
    y = py0 + 28
    if k is None:
        svg.text(px0 + 16, y, T["panel_overview"], size=15, weight="bold", font=FONT_HEAD)
        y += 24
        for p in project["parts"]:
            for sig, pin in p["pins"].items():
                line = "%s: %s → %s" % (part_name(project, p), sig, pin)
                for ln in wrap(line, 60):
                    if y < py1 - 12:
                        svg.text(px0 + 16, y, ln, size=12)
                    y += 17
    else:
        svg.text(px0 + 16, y, T["panel_title"], size=15, weight="bold", font=FONT_HEAD)
        y += 24
        for w in steps[k]["wires"]:
            lines = wrap(describe_wire(project, lay, w), 58)
            if y < py1 - 12:
                svg.rect(px0 + 16, y - 8, 18, 6, w["color"], rx=2)
            for ln in lines:
                if y < py1 - 12:
                    svg.text(px0 + 42, y, ln, size=12)
                y += 16
            y += 4
        if k > 0 or project["existing"]:
            if y < py1 - 12:
                svg.text(px0 + 16, y + 6, T["faded"], size=11, fill=INK2)
    svg.add("</svg>")
    return "\n".join(svg.out), W, H


# ---------------------------------------------------------------- guide

def notes_for(project):
    T = STR[project["lang"]]
    pin_ma = BOARDS[project["board"]]["pin_ma"]
    notes = [T["note_ma"].format(ma=pin_ma)]
    if any(p["type"] == "lcd1602-i2c" for p in project["parts"]):
        notes.append(T["note_i2c"])
    for p in project["parts"]:
        for pin in p["pins"].values():
            if pin in ("D0", "D1"):
                notes.append(T["note_serial"].format(pin=pin))
        if p["type"] == "pir":
            out = p["pins"]["OUT"]
            if out.startswith("A"):
                notes.append(T["note_pir_analog"])
            elif pin_ma == 8:
                notes.append(T["note_pir_digital"])
    return notes


def step_lines(project, lay, step):
    T = STR[project["lang"]]
    parts = {p["id"]: p for p in project["parts"]}
    lines, checks = [], []
    for pid in step["parts"]:
        if pid == "rails":
            checks.append(T["rails_check"])
            continue
        p = parts[pid]
        t = p["type"]
        c = p.get("col")
        fmt = {}
        if c is not None:
            fmt = {"c": c, "c1": c + 1, "c2": c + 2, "c3": c + 3, "c4": c + 4}
        if p.get("resistor"):
            fmt["r"] = "%s (%s)" % (fmt_ohm(p["resistor"]), ", ".join(T["bands"][b] for b in RESISTOR_BANDS[p["resistor"]]))
        if t in T["place"]:
            lines.append(T["place"][t].format(**fmt))
        if "module" in PARTS[t] and t != "speaker":
            lines.append(T["place"]["module"].format(name=part_name(project, p)))
        checks.append(T["check"][t].format(**fmt))
    for w in step["wires"]:
        lines.append(describe_wire(project, lay, w) + ".")
        if w["id"] == "rails.bridge":
            checks.append(T["bridge"])
    return lines, checks


def render_guide(project, lay, steps, image_ext):
    T = STR[project["lang"]]
    b = BOARDS[project["board"]]
    usb_mark = {True: "**⚠\ufe0f %s**" % T["unplug"], False: "**%s**" % T["stay"]}
    out = ["# %s" % md(T["guide_title"].format(title=project["title"])), "",
           T["board_line"].format(board=b["name"], ma=b["pin_ma"]), "",
           "![%s](overview.%s)" % (md(T["overview"]), image_ext), "",
           "## %s" % T["safety_head"], ""]
    out += ["- %s" % s for s in T["safety"]]
    out += ["", "## %s" % T["parts_head"], ""]
    for p in project["parts"]:
        extra = ""
        if p.get("resistor"):
            extra = ", " + T["resistor"].format(r=fmt_ohm(p["resistor"]))
        if p["type"] == "led":
            extra = " (%s)%s" % (p["color"], extra)
        out.append("- %s%s" % (md(part_name(project, p)), md(extra)))
    out += ["", "## %s" % T["pins_head"], "", T["pin_table"], "| --- | --- | --- |"]
    for p in project["parts"]:
        for sig, pin in p["pins"].items():
            out.append("| %s | %s | %s |" % (md(part_name(project, p)), md(sig), pin))
    if project["existing"]:
        names = [T["rails_title"] if e == "rails" else part_name(project, next(p for p in project["parts"] if p["id"] == e))
                 for e in sorted(project["existing"])]
        out += ["", T["existing"].format(what=md(", ".join(names)))]
    notes = notes_for(project)
    out += ["", "## %s" % T["notes_head"], ""] + ["- %s" % n for n in notes]
    for i, s in enumerate(steps):
        out += ["", "## %s: %s" % (T["step"].format(n=i + 1, total=len(steps)), md(s["title"])), "",
                usb_mark[s["unplug"]], ""]
        lines, checks = step_lines(project, lay, s)
        out += ["%d. %s" % (j + 1, md(l)) for j, l in enumerate(lines)]
        if s["note"]:
            out += ["", md(s["note"])]
        out += ["", "![%s](step-%02d.%s)" % (md(T["step"].format(n=i + 1, total=len(steps))), i + 1, image_ext)]
        if checks:
            out += ["", "%s: %s" % (T["check_head"], " ".join(md(c) for c in checks))]
    out += ["", "## %s" % T["final_head"], "", usb_mark[True], ""]
    out += ["%d. %s" % (j + 1, s) for j, s in enumerate(T["final"])]
    return "\n".join(out) + "\n"


def plan_json(project, steps, wires):
    return {
        "title": project["title"], "board": project["board"], "language": project["lang"],
        "parts": [{"id": p["id"], "type": p["type"], "label": p["label"], "pins": p["pins"],
                   "column": p.get("col"), "resistor_ohm": p.get("resistor")} for p in project["parts"]],
        "existing": sorted(project["existing"]),
        "steps": [{"n": i + 1, "title": s["title"], "usb": "unplug" if s["unplug"] else "may-stay-plugged",
                   "parts": s["parts"], "wires": [w["id"] for w in s["wires"]]} for i, s in enumerate(steps)],
        "wires": [{"id": w["id"], "from": list(w["a"]), "to": list(w["b"]), "color": w["cname"],
                   "step": w["step"] + 1 if w["step"] >= 0 else None} for w in wires],
        "notes": notes_for(project),
    }


# ---------------------------------------------------------------- build

def compute(project):
    lay = place(project)
    wires = build_wires(project, lay)
    assign_colors(wires)
    steps = build_steps(project, wires)
    height = route_all(project, lay, wires)
    return lay, wires, steps, height


STEP_FILE = re.compile(r"^step-\d{2}\.(svg|png)$")
OUR_FILES = {"overview.svg", "overview.png", "guide.md", "plan.json"}


def prepare_out(out):
    out = Path(out)
    if out.is_symlink():
        raise SpecError("output folder %s is a symlink: pick a real folder" % out)
    if out.exists() and not out.is_dir():
        raise SpecError("%s exists and is not a folder" % out)
    out.mkdir(parents=True, exist_ok=True)
    entries = list(out.iterdir())
    if entries and not (out / "plan.json").is_file():
        raise SpecError("output folder %s is not empty and holds no earlier build: pick an empty folder" % out)
    for e in entries:
        if e.is_symlink():
            raise SpecError("refusing to write: %s is a symlink" % e)
        if STEP_FILE.match(e.name) and e.is_file():
            e.unlink()  # stale step from an earlier build with more steps
    return out


def safe_write(path, data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    if os.path.islink(path):
        raise SpecError("refusing to write through symlink %s" % path)
    fd = os.open(str(path), flags, 0o644)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def find_chrome():
    env = os.environ.get("CHROME_PATH")
    if env and Path(env).is_file():
        return env
    for c in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium"):
        if Path(c).is_file():
            return c
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    return None


def chrome_cmd(chrome, svg_path, png_path, w, h, profile):
    return [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
            "--no-default-browser-check", "--disable-extensions", "--user-data-dir=%s" % profile,
            "--window-size=%d,%d" % (w, h), "--force-device-scale-factor=2",
            "--screenshot=%s" % png_path, Path(svg_path).resolve().as_uri()]


def render_png(chrome, svg_path, png_path, w, h):
    with tempfile.TemporaryDirectory(prefix="wiring-chrome-") as profile:
        cmd = chrome_cmd(chrome, svg_path, png_path, w, h, profile)
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90, check=False)
    return Path(png_path).is_file()


def build(spec_path, out, png=False, lang=None):
    project = load_spec(spec_path)
    if lang:
        if lang not in STR:
            raise SpecError("language must be one of: %s" % ", ".join(STR))
        project["lang"] = lang
    lay, wires, steps, height = compute(project)
    out = prepare_out(out)
    chrome = find_chrome() if png else None
    ext = "png" if chrome else "svg"
    files = []
    jobs = [("overview", None)] + [("step-%02d" % (i + 1), i) for i in range(len(steps))]
    sizes = {}
    for name, k in jobs:
        text, W, H = render_svg(project, lay, height, wires, steps, k)
        path = out / (name + ".svg")
        safe_write(path, text)
        sizes[name] = (W, H)
        files.append(path)
    if chrome:
        for name, _ in jobs:
            png_path = out / (name + ".png")
            if png_path.is_symlink():
                raise SpecError("refusing to write through symlink %s" % png_path)
            W, H = sizes[name]
            if not render_png(chrome, out / (name + ".svg"), png_path, W, H):
                ext = "svg"
                break
            files.append(png_path)
    safe_write(out / "guide.md", render_guide(project, lay, steps, ext))
    safe_write(out / "plan.json", json.dumps(plan_json(project, steps, wires), indent=2, ensure_ascii=False) + "\n")
    files += [out / "guide.md", out / "plan.json"]
    return project, steps, files, (png and not chrome)


def summary(project, steps, wires):
    T = STR[project["lang"]]
    b = BOARDS[project["board"]]
    lines = ["Board: %s (max %d mA per pin)" % (b["name"], b["pin_ma"]), "Parts:"]
    for p in project["parts"]:
        bits = ["%s=%s" % kv for kv in p["pins"].items()]
        if p.get("col"):
            bits.append("column %d" % p["col"])
        if p.get("resistor"):
            bits.append("resistor %s" % fmt_ohm(p["resistor"]))
        if p["id"] in project["existing"]:
            bits.append("existing")
        lines.append("  %s (%s): %s" % (p["id"], p["type"], ", ".join(bits)))
    lines.append("Steps:")
    for i, s in enumerate(steps):
        lines.append("  %d. [%s] %s" % (i + 1, T["unplug"] if s["unplug"] else T["stay"], s["title"]))
        for w in s["wires"]:
            lines.append("       %s" % w["id"])
    notes = notes_for(project)
    if notes:
        lines.append("Notes:")
        lines += ["  - %s" % n for n in notes]
    return "\n".join(lines)


def cmd_parts():
    for t, info in PARTS.items():
        sig = ", ".join("%s (%s)" % kv for kv in info["signals"].items())
        where = {"bb": "on the breadboard", "module": "off the breadboard, on jumpers",
                 "mixed": "resistor on the breadboard, body off it"}[info["kind"]]
        pins = ", ".join(info["module"]["pins"]) if "module" in info else ""
        print("%-14s %s. Signals: %s%s" % (t, where, sig, (". Module pins: " + pins) if pins else ""))
    print("\nBoards: %s" % ", ".join("%s (%s, %d mA per pin)" % (k, v["name"], v["pin_ma"]) for k, v in BOARDS.items()))
    print("LED colours: %s" % ", ".join(LED_COLORS))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Breadboard wiring guide for UNO-family boards")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("parts", help="list the supported parts and boards")
    c = sub.add_parser("check", help="check a spec and print the plan")
    c.add_argument("spec")
    b = sub.add_parser("build", help="write diagrams and the guide")
    b.add_argument("spec")
    b.add_argument("--out", required=True)
    b.add_argument("--png", action="store_true", help="also render PNGs with headless Chrome or Chromium")
    b.add_argument("--lang", choices=sorted(STR), help="override the spec language")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "parts":
            cmd_parts()
        elif args.cmd == "check":
            project = load_spec(args.spec)
            lay, wires, steps, _ = compute(project)
            print(summary(project, steps, wires))
        else:
            project, steps, files, no_chrome = build(args.spec, args.out, args.png, args.lang)
            for f in files:
                print("wrote %s" % f)
            if no_chrome:
                print("note: no Chrome or Chromium found, so SVG only (set CHROME_PATH to use another install)")
            print("%d steps; USB: %s" % (len(steps), ", ".join("unplug" if s["unplug"] else "may stay" for s in steps)))
    except SpecError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
