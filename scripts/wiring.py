#!/usr/bin/env python3
"""Breadboard wiring guide for Arduino UNO boards (5 V logic).

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
import concurrent.futures
import heapq
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unicodedata
from pathlib import Path

GENERATOR = "breadboard-wiring-guide"
GRID = 9
PITCH = 18
MAX_SPEC_BYTES = 256 * 1024
MAX_PARTS = 12
MAX_STEPS = 20
PNG_TIMEOUT = 90
PNG_WORKERS = 4

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
PWM_PINS = ["D3", "D5", "D6", "D9", "D10", "D11"]
# automatic pins: never D0/D1 (serial) or D13 (on-board LED); PWM pins last so LEDs can have them
AUTO_DIGITAL = ["D2", "D4", "D7", "D8", "D12", "D3", "D5", "D6", "D9", "D10", "D11"]
INPUT_SIGNALS = {("button", "SIG"), ("joystick", "SW"), ("hc-sr04", "ECHO"), ("pir", "OUT")}

# ---------------------------------------------------------------- parts

# kind: "bb" sits on the breadboard, "module" hangs off jumpers,
# "mixed" has a resistor on the breadboard and a body off it.
PARTS = {
    "button": {"kind": "bb", "width": 3, "signals": {"SIG": "digital"}},
    "led": {"kind": "bb", "width": 5, "signals": {"SIG": "digital"}},
    "buzzer": {"kind": "bb", "width": 7, "signals": {"SIG": "digital"}},
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
                "module": {"w": 162, "h": 108, "pins": ["VCC", "TRIG", "ECHO", "GND"]},
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
BANDS4 = {
    220: ("red", "red", "brown", "gold"), 330: ("orange", "orange", "brown", "gold"),
    470: ("yellow", "violet", "brown", "gold"), 680: ("blue", "grey", "brown", "gold"),
    1000: ("brown", "black", "red", "gold"), 2200: ("red", "red", "red", "gold"),
}
BANDS5 = {
    220: ("red", "red", "black", "black", "brown"), 330: ("orange", "orange", "black", "black", "brown"),
    470: ("yellow", "violet", "black", "black", "brown"), 680: ("blue", "grey", "black", "black", "brown"),
    1000: ("brown", "black", "black", "brown", "brown"), 2200: ("red", "red", "black", "brown", "brown"),
}
BAND_HEX = {"black": "#111111", "brown": "#7B4A2A", "red": "#D32F2F", "orange": "#F57C00",
            "yellow": "#FBC02D", "green": "#388E3C", "blue": "#1976D2", "violet": "#7B1FA2",
            "grey": "#9E9E9E", "white": "#FFFFFF", "gold": "#C9A227"}

# ---------------------------------------------------------------- colours

POWER_RED, GND_BLACK, SDA_GREEN, SCL_YELLOW = "#D3382B", "#222222", "#2E9E4F", "#E0B02C"
SIGNAL_COLORS = [("blue", "#2C63D6"), ("orange", "#F07F1C"), ("purple", "#8A4BD6"),
                 ("teal", "#1FA6A6"), ("pink", "#D6458E"), ("brown", "#8D5A3B"),
                 ("olive", "#7C8A1E"), ("cyan", "#19A7CE"), ("lime", "#7CB342")]
INK, INK2, PAPER = "#1A212C", "#5B6470", "#FAFAF7"
BOARD_FILL, BOARD_EDGE, BOARD_TEXT = "#3B5068", "#27384A", "#EEF2F6"

# ---------------------------------------------------------------- text

STR = {
    "en": {
        "unplug": "Unplug USB first.",
        "stay": "USB can stay plugged in.",
        "stay_extra": "Only with an empty sketch on the board, or one that does not drive these pins. Connect the part end of each wire first, then the board pin.",
        "step": "Step {n} of {total}",
        "overview": "Overview: everything wired",
        "rails_title": "Power the breadboard rails",
        "rails_name": "the breadboard rails",
        "add": "Add {name}",
        "connect": "Connect {what}",
        "line": {"+": "red", "-": "blue"},
        "ep": {"top_rail": ("the {pol} rail at the top ({line} line, any hole; drawn above column {col})",) * 2,
               "bottom_rail": ("the {pol} rail at the bottom ({line} line, any hole; drawn below column {col})",) * 2,
               "hole": ("hole {hole}",) * 2,
               "pin": ("pin {pin}",) * 2,
               "mod_pin": ("pin {pin} of the {name}",) * 2},
        "wire": "{color} wire: {a} to {b}",
        "jumper": "Short {color} jumper: {a} to {b}",
        "bridge_wire": "Long black wire: the - rail at the top to the - rail at the bottom (blue lines, any holes; drawn at the right end)",
        "bands": {"black": "black", "brown": "brown", "red": "red", "orange": "orange",
                  "yellow": "yellow", "green": "green", "blue": "blue", "violet": "violet",
                  "grey": "grey", "white": "white", "gold": "gold"},
        "colors": {"red": "Red", "black": "Black", "green": "Green", "yellow": "Yellow",
                   "blue": "Blue", "orange": "Orange", "purple": "Purple", "teal": "Teal",
                   "pink": "Pink", "brown": "Brown", "olive": "Olive", "cyan": "Cyan",
                   "lime": "Lime"},
        "led_colors": {"red": "red", "yellow": "yellow", "green": "green", "blue": "blue", "white": "white"},
        "names": {"button": "push button", "led": "LED", "buzzer": "passive buzzer",
                  "potentiometer": "potentiometer", "speaker": "speaker",
                  "lcd1602-i2c": "LCD 1602 with I2C adapter", "joystick": "joystick module",
                  "hc-sr04": "HC-SR04 distance sensor", "pir": "PIR motion sensor"},
        "resistor_text": "{ohm} (4 bands: {b4}; 5 bands: {b5})",
        "place": {
            "button": "Push the button across the centre gap: legs in e{c}, e{c2}, f{c} and f{c2}.",
            "led": "Resistor {r}: legs in e{c} and e{c3}. LED: long leg (+) in d{c3}, short leg (-) in d{c4}.",
            "buzzer": "Resistor {r}: legs in e{c} and e{c3}. Buzzer: + leg in d{c3}, other leg in d{c6} (most 12 mm buzzers span 3 holes: check yours).",
            "potentiometer": "Potentiometer: its three legs in e{c}, e{c1} and e{c2} (most breadboard potentiometers fit three holes in a row).",
            "speaker": "Resistor {r}: legs in e{c} and e{c3}. The speaker itself stays off the breadboard, on its two leads.",
            "module": "Take the {name}. Read the pin names printed on it: the order drawn here is common, yours may differ.",
        },
        "check": {
            "button": "The two wired legs are diagonal (e{c} and f{c2}). Two legs on the same side can be joined inside the button and read as always pressed.",
            "led": "Long leg toward the resistor. If the LED is backwards it stays dark but takes no harm.",
            "buzzer": "The + leg (often the longer one) goes toward the resistor.",
            "potentiometer": "Outer legs to + and -, the middle leg is the signal.",
            "speaker": "Never wire a speaker straight to a pin or to a rail: the resistor keeps the current safe. The sound is quiet; real volume needs an amplifier module (not covered here).",
            "lcd1602-i2c": "Backlight on but no letters: turn the small contrast screw on the back. The I2C address is usually 0x27 or 0x3F.",
            "joystick": "Read SW with INPUT_PULLUP in the sketch: pressed reads LOW.",
            "hc-sr04": "No reading at all usually means Trig and Echo are swapped.",
            "pir": "Wait about a minute after power-up before trusting it. Pin order varies between boards: read the labels (sometimes under the white dome).",
        },
        "rails_check": "5V goes to the + rail (red line), GND to the - rail (blue line). Never join the + rail and the - rail.",
        "bridge": "This part uses the bottom - rail: the long black wire joins the two - rails.",
        "panel_title": "Wires in this step",
        "panel_overview": "Pin map",
        "panel_more": "+ {n} more: see guide.md",
        "faded": "Grey wires are already in place.",
        "existing": "Already wired: {what}.",
        "board_sub": "top view, USB on the left",
        "breadboard": "breadboard (half size, 30 columns)",
        "guide_title": "Wiring guide: {title}",
        "board_line": "Board: {board}. Breadboard: half size, 30 columns. Pins on this board: at most {ma} mA each.",
        "need_head": "You need",
        "need_breadboard": "A breadboard, half size or bigger.",
        "need_mm": "{n} male-to-male jumper wires (board and breadboard holes are female).",
        "need_fm": "{n} female-to-male jumper wires (module pins are male).",
        "need_spk": "The speaker's two leads go into the breadboard and onto the - rail; if they end in bare wire, push them in or use clip leads.",
        "need_res": "Resistor {ohm} for the {name}: 4 bands {b4}; 5 bands {b5}. Not sure? Check it with a multimeter or the label on the bag.",
        "need_part": "The {name}{extra}.",
        "pins_head": "Pin map",
        "safety_head": "Before you start",
        "safety": [
            "Every step says first whether USB must be unplugged. Power wires (5V, GND, rails, module VCC and GND) and whole modules: unplug. Signal wires only: USB can stay plugged in.",
            "Power the board only from USB: nothing in the DC jack or on VIN, so that unplugging USB really leaves it without power.",
            "The board keeps running the last sketch it got. Before you wire, upload an empty sketch (File > Examples > 01.Basics > BareMinimum), or make sure your sketch does not set any pin you wire as OUTPUT.",
            "Rails: use the + rail (red line) and the - rail (blue line) printed on your breadboard. Their order can differ from the drawing, and any hole along a rail works.",
            "Hole names: letter = row (a to j), number = column (1 to 30). Holes in the same column, a to e (or f to j), are joined inside the breadboard.",
            "Wire colours in the drawings are only a guide. Any colour works, but red for 5V and black for GND avoids mistakes.",
            "No mains voltage, ever. Everything here runs on the 5 V from the USB cable.",
        ],
        "final_head": "Final check before you plug in USB",
        "final": [
            "Nothing joins the + rail to the - rail.",
            "Every module: VCC on +, GND on -.",
            "The sketch on the board is empty, or drives none of the wired pins as OUTPUT.",
            "Plug in USB. The board's power LED lights up. If it does not, or anything gets warm, unplug at once and recheck.",
        ],
        "notes_head": "Notes",
        "note_prefix": "Note:",
        "check_head": "Check",
        "pin_table": "| Part | Pin | Board pin |",
        "note_ma": "This board allows at most {ma} mA per pin: resistor values are picked for that.",
        "note_i2c": "SDA and SCL are the same signals as A4 and A5: keep A4 and A5 free.",
        "note_serial": "{pin} is also the USB serial line on UNO R3: uploads can fail while something is wired to it.",
        "note_d13": "{name} reads D13, which also drives the on-board LED: INPUT_PULLUP may not read HIGH there. Prefer another pin.",
        "note_pir_digital": "On UNO R4 boards a PIR with a 3.3 V output may not read as HIGH on a digital pin. Use an analog pin and analogRead (above about 600 = motion).",
        "note_pir_analog": "Read the PIR with analogRead: above about 600 means motion. This works on both UNO R3 and R4.",
    },
    "it": {
        "unplug": "USB staccato.",
        "stay": "USB può restare attaccato.",
        "stay_extra": "Solo con uno sketch vuoto sulla scheda, o con uno che non comanda questi pin. Collega prima il capo del filo sul componente, poi quello sul pin della scheda.",
        "step": "Passo {n} di {total}",
        "overview": "Schema completo",
        "rails_title": "Alimenta le linee della breadboard",
        "rails_name": "le linee di alimentazione della breadboard",
        "add": "Aggiungi {name}",
        "connect": "Collega {what}",
        "line": {"+": "rossa", "-": "blu"},
        "ep": {"top_rail": ("dalla linea {pol} in alto (linea {line}, un foro qualsiasi; nel disegno sopra la colonna {col})",
                            "alla linea {pol} in alto (linea {line}, un foro qualsiasi; nel disegno sopra la colonna {col})"),
               "bottom_rail": ("dalla linea {pol} in basso (linea {line}, un foro qualsiasi; nel disegno sotto la colonna {col})",
                               "alla linea {pol} in basso (linea {line}, un foro qualsiasi; nel disegno sotto la colonna {col})"),
               "hole": ("dal foro {hole}", "al foro {hole}"),
               "pin": ("dal pin {pin}", "al pin {pin}"),
               "mod_pin": ("dal pin {pin} ({name})", "al pin {pin} ({name})")},
        "wire": "Filo {color}: {a} {b}",
        "jumper": "Ponticello {color}: {a} {b}",
        "bridge_wire": "Filo nero lungo: dalla linea - in alto alla linea - in basso (linee blu, fori qualsiasi; nel disegno a destra)",
        "bands": {"black": "nero", "brown": "marrone", "red": "rosso", "orange": "arancione",
                  "yellow": "giallo", "green": "verde", "blue": "blu", "violet": "viola",
                  "grey": "grigio", "white": "bianco", "gold": "oro"},
        "colors": {"red": "rosso", "black": "nero", "green": "verde", "yellow": "giallo",
                   "blue": "blu", "orange": "arancione", "purple": "viola", "teal": "verde acqua",
                   "pink": "rosa", "brown": "marrone", "olive": "verde oliva", "cyan": "azzurro",
                   "lime": "verde lime"},
        "led_colors": {"red": "rosso", "yellow": "giallo", "green": "verde", "blue": "blu", "white": "bianco"},
        "names": {"button": "pulsante", "led": "LED", "buzzer": "buzzer passivo",
                  "potentiometer": "potenziometro", "speaker": "speaker",
                  "lcd1602-i2c": "LCD 1602 con adattatore I2C", "joystick": "modulo joystick",
                  "hc-sr04": "sensore di distanza HC-SR04", "pir": "sensore di movimento PIR"},
        "resistor_text": "{ohm} (4 bande: {b4}; 5 bande: {b5})",
        "place": {
            "button": "Metti il pulsante a cavallo del canale centrale: gambe in e{c}, e{c2}, f{c} e f{c2}.",
            "led": "Resistenza {r}: gambe in e{c} ed e{c3}. LED: gamba lunga (+) in d{c3}, gamba corta (-) in d{c4}.",
            "buzzer": "Resistenza {r}: gambe in e{c} ed e{c3}. Buzzer: gamba + in d{c3}, l'altra in d{c6} (quasi tutti i buzzer da 12 mm prendono 3 fori: controlla il tuo).",
            "potentiometer": "Potenziometro: le tre gambe in e{c}, e{c1} ed e{c2} (quasi tutti i potenziometri da breadboard stanno in tre fori vicini).",
            "speaker": "Resistenza {r}: gambe in e{c} ed e{c3}. Lo speaker resta fuori dalla breadboard, sui suoi due fili.",
            "module": "Prendi questo modulo: {name}. Leggi i nomi dei pin stampati sul modulo: l'ordine del disegno è il più comune, il tuo può cambiare.",
        },
        "check": {
            "button": "Le due gambe collegate sono in diagonale (e{c} e f{c2}). Due gambe dallo stesso lato possono essere unite dentro il pulsante e risultare sempre premute.",
            "led": "Gamba lunga verso la resistenza. Se il LED è al contrario resta spento ma non si rompe.",
            "buzzer": "La gamba + (spesso la più lunga) va verso la resistenza.",
            "potentiometer": "Le gambe esterne vanno a + e -, quella centrale è il segnale.",
            "speaker": "Mai lo speaker dritto su un pin o su una linea: la resistenza tiene bassa la corrente. Il suono è debole; per il volume vero serve un modulo amplificatore (non incluso qui).",
            "lcd1602-i2c": "Luce accesa ma niente lettere: gira la vitina del contrasto sul retro. L'indirizzo I2C di solito è 0x27 o 0x3F.",
            "joystick": "Leggi SW con INPUT_PULLUP nello sketch: premuto vale LOW.",
            "hc-sr04": "Nessuna lettura di solito vuol dire Trig ed Echo invertiti.",
            "pir": "Aspetta circa un minuto dall'accensione prima di fidarti. L'ordine dei pin cambia tra i moduli: leggi le scritte (a volte sotto la cupola bianca).",
        },
        "rails_check": "5V va sulla linea + (rossa), GND sulla linea - (blu). Mai unire la linea + e la linea -.",
        "bridge": "Questo pezzo usa la linea - in basso: il filo nero lungo unisce le due linee -.",
        "panel_title": "Fili di questo passo",
        "panel_overview": "Mappa dei pin",
        "panel_more": "+ altri {n}: vedi guide.md",
        "faded": "I fili grigi sono già al loro posto.",
        "existing": "Già collegato: {what}.",
        "board_sub": "vista dall'alto, USB a sinistra",
        "breadboard": "breadboard (mezza, 30 colonne)",
        "guide_title": "Guida ai collegamenti: {title}",
        "board_line": "Scheda: {board}. Breadboard: mezza, 30 colonne. Pin di questa scheda: al massimo {ma} mA ciascuno.",
        "need_head": "Ti serve",
        "need_breadboard": "Una breadboard, mezza o più grande.",
        "need_mm": "{n} cavetti maschio-maschio (i fori della scheda e della breadboard sono femmina).",
        "need_fm": "{n} cavetti femmina-maschio (i pin dei moduli sono maschio).",
        "need_spk": "I due fili dello speaker vanno nella breadboard e sulla linea -; se finiscono con il filo nudo, infilali così o usa dei cavetti a coccodrillo.",
        "need_res": "Resistenza {ohm} per il {name}: 4 bande {b4}; 5 bande {b5}. Hai dubbi? Controlla con un multimetro o sull'etichetta della bustina.",
        "need_part": "{name}{extra}.",
        "pins_head": "Mappa dei pin",
        "safety_head": "Prima di iniziare",
        "safety": [
            "Ogni passo dice per prima cosa se staccare l'USB. Fili di alimentazione (5V, GND, linee + e -, VCC e GND dei moduli) e moduli interi: USB staccato. Solo fili di segnale: USB può restare attaccato.",
            "Alimenta la scheda solo dall'USB: niente nel jack DC né su VIN, così staccare l'USB la lascia davvero senza corrente.",
            "La scheda continua a eseguire l'ultimo sketch caricato. Prima di collegare, carica uno sketch vuoto (File > Esempi > 01.Basics > BareMinimum), oppure controlla che il tuo sketch non imposti come OUTPUT nessun pin che colleghi.",
            "Linee di alimentazione: usa la linea + (riga rossa stampata) e la linea - (riga blu) della tua breadboard. Il loro ordine può essere diverso dal disegno, e va bene qualsiasi foro lungo la linea.",
            "Nomi dei fori: lettera = riga (da a a j), numero = colonna (da 1 a 30). I fori della stessa colonna da a a e (o da f a j) sono uniti dentro la breadboard.",
            "I colori dei fili nei disegni sono solo una guida. Va bene qualsiasi colore, ma rosso per 5V e nero per GND evita errori.",
            "Mai tensione di rete. Qui tutto va con i 5 V del cavo USB.",
        ],
        "final_head": "Controllo finale prima di attaccare l'USB",
        "final": [
            "Niente unisce la linea + alla linea -.",
            "Ogni modulo: VCC sul +, GND sul -.",
            "Lo sketch sulla scheda è vuoto, o non usa come OUTPUT nessuno dei pin collegati.",
            "Attacca l'USB. Il LED di alimentazione della scheda si accende. Se non si accende, o qualcosa scalda, stacca subito e ricontrolla.",
        ],
        "notes_head": "Note",
        "note_prefix": "Nota:",
        "check_head": "Controllo",
        "pin_table": "| Componente | Pin | Pin della scheda |",
        "note_ma": "Questa scheda regge al massimo {ma} mA per pin: le resistenze sono scelte per questo.",
        "note_i2c": "SDA e SCL sono gli stessi segnali di A4 e A5: lascia liberi A4 e A5.",
        "note_serial": "Sulla UNO R3 {pin} è anche la linea seriale dell'USB: il caricamento può fallire se ci colleghi qualcosa.",
        "note_d13": "{name} legge D13, che comanda anche il LED sulla scheda: con INPUT_PULLUP può non leggere HIGH. Meglio un altro pin.",
        "note_pir_digital": "Sulle UNO R4 un PIR con uscita a 3,3 V può non essere letto come HIGH su un pin digitale. Usa un pin analogico e analogRead (sopra circa 600 = movimento).",
        "note_pir_analog": "Leggi il PIR con analogRead: sopra circa 600 vuol dire movimento. Funziona sia su UNO R3 sia su R4.",
    },
}


class SpecError(Exception):
    """The spec cannot be built; the message says why."""


# ---------------------------------------------------------------- helpers

_LINKY = re.compile(r"(?i)[a-z][a-z0-9+.-]*://|\bwww\.|@|\b(?:javascript|data|mailto|file|vbscript|ftp|https?|tel|sms):"
                    r"|[a-z0-9-]\.[a-z]{2,}\b")
_USB_TALK = ("usb", "plug")
_PUNCT = set(" .,:;!?'\"()/+-%&<>=*#_[]~0123456789\u00b0\u03a9\u03bc\u20ac$")
_TYPO = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-",
         "\u00b5": "\u03bc", "\u2126": "\u03a9"}


def _skeleton(text):
    """Lowercase ASCII letters only: accents and look-alike marks dropped, spaces and dots gone."""
    base = unicodedata.normalize("NFKD", text)
    return "".join(c for c in base.lower() if "a" <= c <= "z")


def _allowed_char(c):
    """Latin letters (accents included), digits and basic punctuation only."""
    if c in _PUNCT or ("a" <= c <= "z") or ("A" <= c <= "Z"):
        return True
    return 0xC0 <= ord(c) <= 0x24F and c not in "\u00d7\u00f7" and unicodedata.category(c).startswith("L")


def _bad_char(c):
    cp = ord(c)
    return (unicodedata.category(c) in ("Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp")
            or 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF)


def show(value, limit=40):
    """A safe, short rendering of untrusted input for error messages."""
    text = ascii(value)
    return text if len(text) <= limit else text[:limit - 3] + "..."


def clean_text(value, field, max_len, free=True):
    if not isinstance(value, str):
        raise SpecError("%s must be text" % field)
    if any(_bad_char(c) for c in value):
        raise SpecError("%s contains control or invisible characters" % field)
    if free:
        value = unicodedata.normalize("NFKC", "".join(_TYPO.get(c, c) for c in value))
    value = value.strip()
    if not value:
        raise SpecError("%s is empty" % field)
    if len(value) > max_len:
        raise SpecError("%s is longer than %d characters" % (field, max_len))
    if free:
        if _LINKY.search(value):
            raise SpecError("%s cannot contain links, domains or e-mail addresses" % field)
        if not all(_allowed_char(c) for c in value):
            raise SpecError("%s may use only Latin letters, digits and basic punctuation" % field)
        if any(word in _skeleton(value) for word in _USB_TALK):
            raise SpecError("%s cannot talk about USB or plugging in: the script writes the USB call of every step itself" % field)
    return value


def xml(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def md(text):
    """Escape user text for Markdown: no HTML, no links, no emphasis, no table breaks."""
    out = str(text).replace("\\", "\\\\")
    for a, b in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;")):
        out = out.replace(a, b)
    for ch in "[]|`*_#~":
        out = out.replace(ch, "\\" + ch)
    return out


def norm_pin(value, field):
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        raise SpecError("%s must be a pin name like D2 or A0" % field)
    v = value.strip().upper()
    if re.fullmatch(r"[0-9]{1,2}", v):
        v = "D" + str(int(v))
    m = re.fullmatch(r"D([0-9]{1,2})", v)
    if m:
        v = "D" + str(int(m.group(1)))
    if v in POWER_PINS or v in ("AREF", "IOREF", "RST", "NC"):
        raise SpecError("%s: %s is a power or special pin, not a signal pin" % (field, v))
    if v not in SIGNAL_PINS:
        raise SpecError("%s: unknown pin %s (use D0-D13, A0-A5, SDA or SCL)" % (field, show(value)))
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


def resistor_text(lang, r):
    T = STR[lang]
    return T["resistor_text"].format(ohm=fmt_ohm(r), b4=", ".join(T["bands"][b] for b in BANDS4[r]),
                                     b5=", ".join(T["bands"][b] for b in BANDS5[r]))


# ---------------------------------------------------------------- spec

SPEC_KEYS = {"title", "board", "language", "parts", "steps", "existing", "lcd_text"}
PART_KEYS = {"id", "type", "label", "pin", "pins", "color", "column"}
STEP_KEYS = {"title", "parts", "wires", "note", "usb"}


def _no_constants(name):
    raise SpecError("spec uses %s, which is not a number" % name)


def load_spec(path):
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(str(path), flags)
    except OSError as exc:
        raise SpecError("cannot read the spec: %s" % (exc.strerror or "error"))
    chunks, total = [], 0
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise SpecError("the spec must be a regular file")
        if st.st_size > MAX_SPEC_BYTES:
            raise SpecError("spec file is larger than %d bytes" % MAX_SPEC_BYTES)
        while True:
            block = os.read(fd, 65536)
            if not block:
                break
            total += len(block)
            if total > MAX_SPEC_BYTES:
                raise SpecError("spec file is larger than %d bytes" % MAX_SPEC_BYTES)
            chunks.append(block)
    finally:
        os.close(fd)
    try:
        data = json.loads(b"".join(chunks).decode("utf-8"), parse_constant=_no_constants)
    except RecursionError:
        raise SpecError("spec is nested too deeply")
    except UnicodeDecodeError:
        raise SpecError("spec is not valid UTF-8")
    except ValueError as exc:
        raise SpecError("spec is not valid JSON: %s" % str(exc)[:100])
    return validate(data)


def _str_list(value, where):
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise SpecError("%s must be a list of names" % where)
    return value


def validate(data):
    if not isinstance(data, dict):
        raise SpecError("spec must be a JSON object")
    unknown = set(data) - SPEC_KEYS
    if unknown:
        raise SpecError("unknown spec keys: %s" % ", ".join(show(k) for k in sorted(unknown)[:5]))
    title = clean_text(data.get("title", ""), "title", 80)
    board = data.get("board", "uno-r4-wifi")
    if not isinstance(board, str) or board not in BOARDS:
        raise SpecError("board must be one of: %s" % ", ".join(BOARDS))
    lang = data.get("language", "en")
    if not isinstance(lang, str) or lang not in STR:
        raise SpecError("language must be one of: %s" % ", ".join(STR))
    lcd_text = data.get("lcd_text", ["Hello,", "breadboard!"])
    if not isinstance(lcd_text, list) or len(lcd_text) != 2:
        raise SpecError("lcd_text must be a list of two lines")
    lcd_text = [clean_text(t, "lcd_text", 16) if t != "" else " " for t in lcd_text]

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
            raise SpecError("%s: unknown keys %s" % (where, ", ".join(show(k) for k in sorted(unknown)[:5])))
        pid = raw.get("id")
        if not isinstance(pid, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,15}", pid) or pid == "rails":
            raise SpecError("%s: id must be 1-16 lowercase letters, digits or _, starting with a letter (not 'rails')" % where)
        if pid in ids:
            raise SpecError("duplicate part id %s" % show(pid))
        ids.add(pid)
        ptype = raw.get("type")
        if not isinstance(ptype, str) or ptype not in PARTS:
            raise SpecError("%s: type must be one of: %s" % (where, ", ".join(PARTS)))
        info = PARTS[ptype]
        label = clean_text(raw["label"], where + ".label", 24) if "label" in raw else None
        color = raw.get("color", "red")
        if ptype == "led":
            if not isinstance(color, str) or color not in LED_COLORS:
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
                    raise SpecError("%s: %s has no signal %s (it has %s)" % (where, ptype, show(k), ", ".join(info["signals"])))
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

    # pin bookkeeping: fixed pins first, then automatic ones
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
            pwm = [x for x in pool if x in PWM_PINS]
            pin = pwm[0] if (p["type"] == "led" and pwm) else pool[0]
            pool.remove(pin)
            p["pins"][sig] = pin
            used_pins[pin] = "%s.%s" % (p["id"], sig)

    existing = _str_list(data.get("existing", []), "existing")
    for e in existing:
        if e != "rails" and e not in ids:
            raise SpecError("existing: unknown part %s" % show(e))
    existing = set(existing)
    by_id = {p["id"]: p for p in parts}
    for e in sorted(existing - {"rails"}):
        p, raw = by_id[e], parts_in[by_id[e]["index"]]
        info = PARTS[p["type"]]
        free_sigs = [s for s, k in info["signals"].items() if k not in ("sda", "scl")]
        given = raw.get("pins", {}) if isinstance(raw.get("pins"), dict) else {}
        has_pins = "pin" in raw or all(any(str(k).upper() == s for k in given) for s in free_sigs)
        if free_sigs and not has_pins:
            raise SpecError("existing part %s: give its pins as they are wired on the bench" % e)
        if info["kind"] in ("bb", "mixed") and p["column"] is None:
            raise SpecError("existing part %s: give its column as it sits on the breadboard" % e)

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
                raise SpecError("%s: unknown keys %s" % (where, ", ".join(show(k) for k in sorted(unknown)[:5])))
            st = {"title": clean_text(s["title"], where + ".title", 60) if "title" in s else None,
                  "note": clean_text(s["note"], where + ".note", 200) if "note" in s else None,
                  "parts": [], "wires": [], "force_unplug": False}
            usb = s.get("usb")
            if usb not in (None, "unplug"):
                raise SpecError("%s.usb can only be \"unplug\": the script decides when USB may stay plugged in" % where)
            st["force_unplug"] = usb == "unplug"
            for pid in _str_list(s.get("parts", []), where + ".parts"):
                if pid != "rails" and pid not in ids:
                    raise SpecError("%s: unknown part %s" % (where, show(pid)))
                if pid in existing:
                    raise SpecError("%s: %s is listed as existing" % (where, show(pid)))
                if pid in seen_parts:
                    raise SpecError("%s: part %s is placed in two steps" % (where, show(pid)))
                seen_parts.add(pid)
                st["parts"].append(pid)
            for w in _str_list(s.get("wires", []), where + ".wires"):
                if "." not in w:
                    raise SpecError("%s: wires take names like joy.SW" % where)
                if w in seen_wires:
                    raise SpecError("%s: wire %s is claimed twice" % (where, show(w)))
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
            if any(x in taken for x in range(c - 1, c + w + 1)):
                raise SpecError("%s overlaps or touches another part: leave one free column between parts" % p["id"])
            taken.update(range(c, c + w))
            p["col"] = c
            col = c + w + 1
    x = lay.col_x0 - 2 * PITCH  # module pins line up with breadboard columns
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
    """Every wire with its endpoints."""
    parts = project["parts"]
    wires = []
    plus_used, minus_used = set([1, 2, 30]), set([1, 2, 30])

    def add(wid, part, a, b, role, direct=False, lead=False):
        wires.append({"id": wid, "part": part, "a": a, "b": b, "role": role, "direct": direct, "lead": lead})

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
            add(pid + ".SIG", pid, ("pin", p["pins"]["SIG"]), ("hole", "a", c), "signal")
            add(pid + ".GND", pid, ("hole", "j", c + 2), ("rail", "bottom", "-", c + 2), "ground", direct=True)
            use_col(c)
            needs_bridge = needs_bridge or pid
        elif t == "led":
            c = p["col"]
            add(pid + ".SIG", pid, ("pin", p["pins"]["SIG"]), ("hole", "a", c), "signal")
            add(pid + ".GND", pid, ("hole", "a", c + 4), ("rail", "top", "-", c + 4), "ground", direct=True)
            use_col(c)
            minus_used.add(c + 4)
        elif t == "buzzer":
            c = p["col"]
            add(pid + ".SIG", pid, ("pin", p["pins"]["SIG"]), ("hole", "a", c), "signal")
            add(pid + ".GND", pid, ("hole", "a", c + 6), ("rail", "top", "-", c + 6), "ground", direct=True)
            use_col(c)
            minus_used.add(c + 6)
        elif t == "potentiometer":
            c = p["col"]
            add(pid + ".VCC", pid, ("hole", "a", c), ("rail", "top", "+", c), "power", direct=True)
            add(pid + ".WIPER", pid, ("pin", p["pins"]["WIPER"]), ("hole", "a", c + 1), "signal")
            add(pid + ".GND", pid, ("hole", "a", c + 2), ("rail", "top", "-", c + 2), "ground", direct=True)
            for x in (c, c + 1):
                use_col(x)
            minus_used.add(c + 2)
        elif t == "speaker":
            c = p["col"]
            add(pid + ".SIG", pid, ("pin", p["pins"]["SIG"]), ("hole", "a", c), "signal")
            use_col(c)
            use_col(c + 3)
    # module wires, after every breadboard column is known
    for p in parts:
        t, pid = p["type"], p["id"]
        info = PARTS[t]
        if t == "speaker":
            c = p["col"]
            add(pid + ".+", pid, ("mod", pid, "+"), ("hole", "a", c + 3), "speaker", lead=True)
            add(pid + ".-", pid, ("mod", pid, "-"), ("rail", "top", "-", None), "ground", lead=True)
        elif info["kind"] == "module":
            for pin in info["module"]["pins"]:
                if pin in info["power"]:
                    pol = info["power"][pin]
                    add("%s.%s" % (pid, pin), pid, ("mod", pid, pin), ("rail", "top", pol, None),
                        "power" if pol == "+" else "ground")
                else:
                    board_pin = p["pins"][pin]
                    role = "sda" if board_pin == "SDA" else "scl" if board_pin == "SCL" else "signal"
                    add("%s.%s" % (pid, pin), pid, ("mod", pid, pin), ("pin", board_pin), role)
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


def wire_signal_only(w):
    """A jumper between one board signal pin (D, A, SDA, SCL) and a part."""
    ends = (w["a"], w["b"])
    board = [e for e in ends if e[0] == "pin"]
    if len(board) != 1 or board[0][1] not in SIGNAL_PINS:
        return False
    other = ends[1] if ends[0][0] == "pin" else ends[0]
    return other[0] in ("hole", "mod") and w["role"] in ("signal", "sda", "scl")


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
        for i, c in enumerate(cells):  # a bend takes the whole cell
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

def part_name(project, p):
    T = STR[project["lang"]]
    base = T["names"][p["type"]]
    if p["label"]:
        return '%s "%s"' % (base, p["label"])
    if sum(1 for q in project["parts"] if q["type"] == p["type"]) > 1:
        return '%s "%s"' % (base, p["id"])
    return base


def build_steps(project, wires):
    T = STR[project["lang"]]
    parts = {p["id"]: p for p in project["parts"]}
    existing = project["existing"]
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
    claimed = {}
    for i, s in enumerate(spec_steps):
        for wid in s["wires"]:
            if wid not in wire_ids:
                raise SpecError("step %d: no wire named %s (see the check output for wire names)" % (i + 1, show(wid)))
            claimed[wid] = i
    part_step = {}
    for i, s in enumerate(spec_steps):
        for pid in s["parts"]:
            part_step[pid] = i
    for w in wires:
        owner = "rails" if w["part"] == "rails" else w["part"]
        if w["id"] in claimed:
            si = claimed[w["id"]]
            if owner not in existing and part_step.get(owner, -1) > si:
                raise SpecError("wire %s is claimed before its part is placed" % w["id"])
            w["step"] = si
        elif owner in existing:
            w["step"] = -1
        else:
            w["step"] = part_step.get(owner, -1)
    by_part = {}
    for w in wires:
        by_part.setdefault(w["part"], []).append(w)
    steps = []
    for i, s in enumerate(spec_steps):
        new_parts = s["parts"]
        new_wires = [w for w in wires if w["step"] == i]
        unplug = s["force_unplug"] or bool(new_parts) or any(not wire_signal_only(w) for w in new_wires)
        if not unplug:
            # signal wires only: still unplug unless the rails and every touched part are already powered
            needed = [wire_ids[x] for x in ("rails.5V", "rails.GND")]
            for w in new_wires:
                own = by_part.get(w["part"], [])
                needed += [pw for pw in own if not wire_signal_only(pw)]
                if "rails.bridge" in wire_ids and any(e[0] == "rail" and e[1] == "bottom" for pw in own for e in (pw["a"], pw["b"])):
                    needed.append(wire_ids["rails.bridge"])
            unplug = any(pw["step"] >= i for pw in needed)
        if s["title"]:
            title = s["title"]
        elif new_parts == ["rails"]:
            title = T["rails_title"]
        elif new_parts:
            names = [T["rails_name"] if p == "rails" else part_name(project, parts[p]) for p in new_parts]
            title = T["add"].format(name=", ".join(names))
        else:
            title = T["connect"].format(what=", ".join(w["id"] for w in new_wires))
        steps.append({"title": title, "parts": new_parts, "wires": new_wires,
                      "unplug": unplug, "note": s["note"]})
    if not steps:
        raise SpecError("nothing to wire: every part is marked existing")
    return steps


# ---------------------------------------------------------------- colours

def assign_colors(wires):
    i = 0
    for w in wires:
        role = w["role"]
        if role == "power":
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
    T = STR[project["lang"]]
    E = T["ep"]
    if ep[0] == "pin":
        return E["pin"][which].format(pin=ep[1])
    if ep[0] == "hole":
        return E["hole"][which].format(hole="%s%d" % (ep[1], ep[2]))
    if ep[0] == "rail":
        key = "top_rail" if ep[1] == "top" else "bottom_rail"
        return E[key][which].format(pol=ep[2], col=ep[3], line=T["line"][ep[2]])
    p = next(p for p in project["parts"] if p["id"] == ep[1])
    return E["mod_pin"][which].format(pin=ep[2], name=part_name(project, p))


def describe_wire(project, lay, w):
    T = STR[project["lang"]]
    if w["id"] == "rails.bridge":
        return T["bridge_wire"]
    key = "jumper" if w["direct"] else "wire"
    color = T["colors"][w["cname"]]
    if project["lang"] == "en" and key == "jumper":
        color = color.lower()
    return T[key].format(color=color, a=describe_ep(project, lay, w["a"], 0),
                         b=describe_ep(project, lay, w["b"], 1))


def jumper_counts(wires):
    """Jumper wires to have at hand: male-to-male and female-to-male."""
    mm = fm = 0
    for w in wires:
        if w["step"] < 0 or w["lead"]:
            continue  # already on the bench, or the speaker's own leads
        if w["a"][0] == "mod" or w["b"][0] == "mod":
            fm += 1
        else:
            mm += 1
    return mm, fm


# ---------------------------------------------------------------- svg

FONT = "Menlo, Consolas, 'DejaVu Sans Mono', monospace"
FONT_HEAD = "'Helvetica Neue', Arial, sans-serif"


class Svg:
    def __init__(self):
        self.out = []

    def add(self, s):
        self.out.append(s)

    def text(self, x, y, s, size=12, fill=INK, weight="normal", anchor="start", font=FONT, rotate=None, extra=""):
        tr = ' transform="rotate(%d %s %s)"' % (rotate, x, y) if rotate else ""
        self.add('<text x="%s" y="%s" font-family="%s" font-size="%s" font-weight="%s" fill="%s" text-anchor="%s"%s%s>%s</text>'
                 % (x, y, xml(font), size, weight, fill, anchor, tr, extra, xml(s)))

    def rect(self, x, y, w, h, fill, stroke="none", rx=0, sw=1, extra=""):
        self.add('<rect x="%s" y="%s" width="%s" height="%s" rx="%s" fill="%s" stroke="%s" stroke-width="%s"%s/>'
                 % (x, y, w, h, rx, fill, stroke, sw, extra))

    def circle(self, x, y, r, fill, stroke="none", sw=1):
        self.add('<circle cx="%s" cy="%s" r="%s" fill="%s" stroke="%s" stroke-width="%s"/>' % (x, y, r, fill, stroke, sw))

    def line(self, x1, y1, x2, y2, stroke, sw=2, extra=""):
        self.add('<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-width="%s"%s/>' % (x1, y1, x2, y2, stroke, sw, extra))


def draw_board(svg, project, lay):
    b = BOARDS[project["board"]]
    T = STR[project["lang"]]
    svg.rect(lay.bx0 - 18, lay.by0 + 54, 36, 45, "#B8BEC4", "#7D848A", rx=4)
    svg.rect(lay.bx0, lay.by0, lay.bw, lay.bh, BOARD_FILL, BOARD_EDGE, rx=12, sw=2)
    svg.text(lay.bx0 + 207, lay.by0 + 120, b["name"], size=24, fill=BOARD_TEXT, weight="bold", anchor="middle", font=FONT_HEAD)
    svg.text(lay.bx0 + 207, lay.by0 + 142, T["board_sub"], size=11, fill=BOARD_TEXT, anchor="middle")
    svg.text(lay.bx0 - 18, lay.by0 + 46, "USB", size=10, fill=INK2, anchor="middle")
    svg.rect(lay.bx0 + 72 - 9, lay.top_hdr_y - 9, len(TOP_HEADER) * PITCH, 18, "#111111", rx=2)
    for name in TOP_HEADER:
        if not name:
            continue
        x, y, _ = lay.pins["GND.top" if name == "GND" else name]
        svg.rect(x - 4, y - 4, 8, 8, "#D9D9D9")
        svg.text(x + 4, y + 16, name[1:] if re.fullmatch(r"D[0-9]+", name) else name, size=10,
                 fill=BOARD_TEXT, rotate=90)
    svg.rect(lay.bx0 + 54 - 9, lay.bot_hdr_y - 9, len(POWER_HEADER) * PITCH, 18, "#111111", rx=2)
    x = lay.bx0 + 54
    for name in POWER_HEADER:
        svg.rect(x - 4, lay.bot_hdr_y - 4, 8, 8, "#D9D9D9")
        svg.text(x + 4, lay.bot_hdr_y - 16, name, size=10, fill=BOARD_TEXT, rotate=-90)
        x += PITCH
    ax = lay.pins["A0"][0]
    svg.rect(ax - 9, lay.bot_hdr_y - 9, len(ANALOG_HEADER) * PITCH, 18, "#111111", rx=2)
    for name in ANALOG_HEADER:
        x = lay.pins[name][0]
        svg.rect(x - 4, lay.bot_hdr_y - 4, 8, 8, "#D9D9D9")
        svg.text(x + 4, lay.bot_hdr_y - 16, name, size=10, fill=BOARD_TEXT, rotate=-90)
    svg.text(lay.bx1 - 12, lay.top_hdr_y + 62, "DIGITAL", size=10, fill="#C9D3DD", anchor="end")
    svg.text(lay.bx0 + 54, lay.bot_hdr_y - 66, "POWER", size=10, fill="#C9D3DD")
    svg.text(ax, lay.bot_hdr_y - 66, "ANALOG", size=10, fill="#C9D3DD")


def draw_breadboard(svg, project, lay):
    T = STR[project["lang"]]
    svg.rect(lay.bb_x0, lay.bb_y0, lay.bb_x1 - lay.bb_x0, lay.bb_y1 - lay.bb_y0, "#F3EFE6", "#D8D2C4", rx=8, sw=2)
    x0, x1 = lay.col_x(1) - 14, lay.col_x(30) + 14
    for (side, pol), y in lay.rail_y.items():
        color = POWER_RED if pol == "+" else "#2C63D6"
        outer = (side == "top" and pol == "+") or (side == "bottom" and pol == "+")
        off = (-9 if side == "top" else 9) if outer else (9 if side == "top" else -9)
        svg.line(x0, y + off, x1, y + off, color, 2)
        svg.text(x0 - 14, y + 4, "+" if pol == "+" else "−", size=14, fill=color, weight="bold", anchor="middle")
        for c in range(1, 31):
            svg.rect(lay.col_x(c) - 3, y - 3, 6, 6, "#8C877A", rx=1)
    svg.rect(lay.bb_x0 + 10, lay.gutter_y - 7, lay.bb_x1 - lay.bb_x0 - 20, 14, "#E7E2D6")
    for c in range(1, 31):
        x = lay.col_x(c)
        for r in "abcdefghij":
            svg.rect(x - 3, lay.row_y[r] - 3, 6, 6, "#8C877A", rx=1)
    for r in "abcdefghij":
        svg.text(lay.col_x(1) - 22, lay.row_y[r] + 4, r, size=10, fill=INK2, anchor="middle")
        svg.text(lay.col_x(30) + 22, lay.row_y[r] + 4, r, size=10, fill=INK2, anchor="middle")
    svg.text(lay.bb_x1 - 8, lay.bb_y1 + 16, T["breadboard"], size=10, fill=INK2, anchor="end")


def draw_column_numbers(svg, lay):
    """Drawn after the wires, with a halo, so a wire never hides the number."""
    halo = ' stroke="#F3EFE6" stroke-width="4" paint-order="stroke"'
    for c in range(1, 31):
        x = lay.col_x(c)
        svg.text(x, lay.row_y["a"] - 12, str(c), size=9, fill=INK2, anchor="middle", weight="bold", extra=halo)
        svg.text(x, lay.row_y["j"] + 19, str(c), size=9, fill=INK2, anchor="middle", weight="bold", extra=halo)


def draw_resistor(svg, x1, x2, y, value):
    svg.line(x1, y, x2, y, "#8A8A8A", 3)
    mx = (x1 + x2) / 2
    svg.rect(mx - 17, y - 7, 34, 14, "#D9C7A0", "#8A7A55", rx=5)
    for i, b in enumerate(BANDS4.get(value, ("black",) * 4)):
        bx = mx - 11 + i * 6 + (3 if i == 3 else 0)
        svg.rect(bx, y - 7, 3, 14, BAND_HEX[b])
    for x in (x1, x2):
        svg.circle(x, y, 3.5, "#B0B0B0", "#555555")


def highlight_box(svg, x, y, w, h):
    svg.rect(x, y, w, h, "none", "#F2A900", rx=8, sw=2.5, extra=' stroke-dasharray="6 4"')


def draw_bb_part(svg, project, lay, p, highlight):
    t, c = p["type"], p.get("col")
    hx = lay.col_x
    width = PARTS[t]["width"]
    if t == "button":
        cx, cy = (hx(c) + hx(c + 2)) / 2, lay.gutter_y
        if highlight:
            highlight_box(svg, cx - 30, cy - 30, 60, 60)
        svg.rect(cx - 22, cy - 22, 44, 44, "#2A2A2A", rx=5)
        svg.rect(cx - 15, cy - 15, 30, 30, "#C9C9C9", rx=3)
        svg.circle(cx, cy, 8, "#1A1A1A")
        for (col, row) in ((c, "e"), (c + 2, "e"), (c, "f"), (c + 2, "f")):
            svg.circle(hx(col), lay.row_y[row], 3.5, "#B0B0B0", "#555555")
    elif t in ("led", "buzzer", "speaker"):
        if highlight:
            last = {"led": 4, "buzzer": 6, "speaker": 3}[t]
            highlight_box(svg, hx(c) - 12, lay.row_y["a"] - 28, hx(c + last) - hx(c) + 24, 96)
        draw_resistor(svg, hx(c), hx(c + 3), lay.row_y["e"], p["resistor"])
        if t == "led":
            xa, xk, y = hx(c + 3), hx(c + 4), lay.row_y["d"]
            mx = (xa + xk) / 2
            svg.line(xa, y, mx - 3, lay.row_y["b"] + 4, "#9A9A9A", 2)
            svg.line(xk, y, mx + 3, lay.row_y["b"] + 4, "#9A9A9A", 2)
            svg.add('<path d="M %s %s a 8 8 0 0 1 16 0 v 6 h -16 z" fill="%s" stroke="#555555" stroke-width="1"/>'
                    % (mx - 8, lay.row_y["b"] - 2, LED_COLORS[p["color"]][1]))
            svg.text(xa - 7, lay.row_y["c"] + 4, "+", size=10, fill=INK, weight="bold", anchor="middle")
            for x in (xa, xk):
                svg.circle(x, y, 3.5, "#B0B0B0", "#555555")
        elif t == "buzzer":
            xa, xk, y = hx(c + 3), hx(c + 6), lay.row_y["d"]
            mx = (xa + xk) / 2
            svg.circle(mx, lay.row_y["b"] + 9, 22, "#1E1E1E", "#000000")
            svg.circle(mx, lay.row_y["b"] + 9, 4, "#555555")
            svg.text(xa - 9, lay.row_y["c"] + 14, "+", size=11, fill=INK, weight="bold", anchor="middle")
            for x in (xa, xk):
                svg.circle(x, y, 3.5, "#B0B0B0", "#555555")
    elif t == "potentiometer":
        x0 = hx(c)
        if highlight:
            highlight_box(svg, x0 - 12, lay.row_y["a"] - 28, 2 * PITCH + 24, 96)
        svg.rect(x0 - 8, lay.row_y["b"] - 4, 2 * PITCH + 16, 44, "#2F5DA8", "#1D3E73", rx=4)
        svg.circle(x0 + PITCH, lay.row_y["c"] + 4, 13, "#E0E0E0", "#888888")
        svg.line(x0 + PITCH, lay.row_y["c"] - 6, x0 + PITCH, lay.row_y["c"] + 14, "#666666", 3)
        for i in range(3):
            svg.circle(x0 + i * PITCH, lay.row_y["e"], 3.5, "#B0B0B0", "#555555")
    bb_label(svg, lay, p, c, width)


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
    name = part_name(project, p)
    if highlight:
        box_w = max(w + 20, len(name) * 6.8 + 24)
        highlight_box(svg, x - 10, y - 30, box_w, h + 48)
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
        svg.circle(x + 42, y + 34, 24, "#C9CBCE", "#666666")
        svg.circle(x + w - 42, y + 34, 24, "#C9CBCE", "#666666")
    elif t == "pir":
        svg.rect(x, y, w, h, "#1E6B3A", "#144D2A", rx=5, sw=2)
        svg.circle(x + w / 2, y + 52, 38, "#F3F3F0", "#BBBBBB")
        svg.circle(x + w / 2, y + 52, 22, "none", "#DDDDDD")
    elif t == "speaker":
        svg.rect(x, y, w, h, "#222222", "#000000", rx=10)
        svg.add('<ellipse cx="%s" cy="%s" rx="26" ry="34" fill="#333333" stroke="#555555"/>' % (x + w / 2, y + 48))
        svg.add('<ellipse cx="%s" cy="%s" rx="13" ry="18" fill="#111111"/>' % (x + w / 2, y + 48))
    svg.rect(x + 9, y + h - 12, 18 * len(pins) + 18, 16, "#111111", rx=2)
    for i, pname in enumerate(pins):
        px = x + 18 * (i + 1)
        svg.rect(px - 4, y + h - 4, 8, 8, "#D9D9D9")
        label = {"-": "−"}.get(pname, pname)
        svg.text(px + 4, y + h - 16, label, size=10, fill="#FFFFFF", weight="bold", rotate=-90)
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
    return textwrap.wrap(text, width, break_on_hyphens=False) or [""]


def render_svg(project, lay, height, wires, steps, k=None):
    """k = step index, or None for the overview."""
    T = STR[project["lang"]]
    W = lay.width
    H = lay.bb_y1 + 36
    svg = Svg()
    svg.add('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d">' % (W, H, W, H))
    svg.rect(0, 0, W, H, PAPER)
    if k is None:
        heading, unplug = T["overview"], None
    else:
        heading = "%s: %s" % (T["step"].format(n=k + 1, total=len(steps)), steps[k]["title"])
        unplug = steps[k]["unplug"]
    if unplug is not None:
        bg, fg = ("#FDECEA", "#B3261E") if unplug else ("#E7F4EA", "#1E6B3A")
        msg = T["unplug"] if unplug else T["stay"]
        svg.rect(36, 22, 44 + 11 * len(msg) + 30, 44, bg, fg, rx=8, sw=2)
        if unplug:
            svg.add('<path d="M 52 57 L 65 33 L 78 57 Z" fill="%s"/>' % fg)
            svg.text(65, 54, "!", size=15, fill="#FFFFFF", weight="bold", anchor="middle")
        else:
            svg.circle(65, 44, 11, fg)
        svg.text(88, 51, msg, size=20, fill=fg, weight="bold", font=FONT_HEAD)
        svg.text(36, 92, project["title"], size=17, weight="bold", font=FONT_HEAD)
        svg.text(36, 112, heading[:120], size=14, fill=INK2, font=FONT_HEAD)
    else:
        svg.text(36, 48, project["title"], size=22, weight="bold", font=FONT_HEAD)
        svg.text(36, 74, heading[:120], size=14, fill=INK2, font=FONT_HEAD)
    draw_board(svg, project, lay)
    draw_breadboard(svg, project, lay)
    last = k if k is not None else len(steps) - 1
    current_parts = set(steps[k]["parts"]) if k is not None else set()
    shown_parts = set(project["existing"])
    for i in range(0, last + 1):
        shown_parts.update(steps[i]["parts"])
    for p in project["parts"]:
        if p["id"] not in shown_parts:
            continue
        hl = p["id"] in current_parts
        if PARTS[p["type"]]["kind"] in ("bb", "mixed"):
            draw_bb_part(svg, project, lay, p, hl)
        if "module" in PARTS[p["type"]]:
            draw_module(svg, project, lay, p, hl)
    for w in wires:
        if k is not None and w["step"] < k:
            wire_svg(svg, w, faded=True)
    for w in wires:
        if (k is None and w["step"] <= last) or (k is not None and w["step"] == k):
            wire_svg(svg, w, bold=k is not None)
    draw_column_numbers(svg, lay)
    # side panel
    px0, py0, px1, py1 = lay.panel
    bottom = py1 - 30
    svg.rect(px0, py0, px1 - px0, py1 - py0, "#FFFFFF", "#D5D9DE", rx=8, sw=1.5)
    y = py0 + 28
    if k is None:
        svg.text(px0 + 16, y, T["panel_overview"], size=15, weight="bold", font=FONT_HEAD)
        y += 24
        lines = ["%s: %s → %s" % (part_name(project, p), sig, pin)
                 for p in project["parts"] for sig, pin in p["pins"].items()]
        for n, line in enumerate(lines):
            chunk = wrap(line, 60)
            if y + 17 * len(chunk) > bottom:
                svg.text(px0 + 16, y, T["panel_more"].format(n=len(lines) - n), size=12, fill=INK2)
                break
            for ln in chunk:
                svg.text(px0 + 16, y, ln, size=12)
                y += 17
    else:
        svg.text(px0 + 16, y, T["panel_title"], size=15, weight="bold", font=FONT_HEAD)
        y += 24
        items = steps[k]["wires"]
        for n, w in enumerate(items):
            chunk = wrap(describe_wire(project, lay, w), 58)
            if y + 16 * len(chunk) > bottom:
                svg.text(px0 + 16, y, T["panel_more"].format(n=len(items) - n), size=12, fill=INK2)
                break
            svg.rect(px0 + 16, y - 8, 18, 6, w["color"], rx=2)
            for ln in chunk:
                svg.text(px0 + 42, y, ln, size=12)
                y += 16
            y += 4
        if k > 0 or project["existing"]:
            svg.text(px0 + 16, py1 - 14, T["faded"], size=11, fill=INK2)
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
        for sig, pin in p["pins"].items():
            if pin in ("D0", "D1") and project["board"] == "uno-r3":
                notes.append(T["note_serial"].format(pin=pin))
            if pin == "D13" and (p["type"], sig) in INPUT_SIGNALS:
                notes.append(T["note_d13"].format(name=part_name(project, p)))
        if p["type"] == "pir":
            out = p["pins"]["OUT"]
            if out.startswith("A"):
                notes.append(T["note_pir_analog"])
            elif pin_ma == 8:
                notes.append(T["note_pir_digital"])
    return notes


def part_fmt(project, p):
    c = p.get("col")
    fmt = {}
    if c is not None:
        fmt = {"c": c, "c1": c + 1, "c2": c + 2, "c3": c + 3, "c4": c + 4, "c6": c + 6}
    if p.get("resistor"):
        fmt["r"] = resistor_text(project["lang"], p["resistor"])
    return fmt


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
        fmt = part_fmt(project, p)
        if t in T["place"]:
            lines.append(T["place"][t].format(**fmt))
        if "module" in PARTS[t] and t != "speaker":
            lines.append(T["place"]["module"].format(name=part_name(project, p)))
        checks.append(T["check"][t].format(**fmt))
    for w in step["wires"]:
        lines.append(describe_wire(project, lay, w) + ".")
        if w["id"] == "rails.bridge":
            checks.append(T["bridge"])
    for pid in dict.fromkeys(w["part"] for w in step["wires"]):
        if pid != "rails" and pid not in step["parts"]:
            p = parts[pid]
            checks.append(T["check"][p["type"]].format(**part_fmt(project, p)))
    return lines, checks


def render_guide(project, lay, steps, wires, image_ext):
    T = STR[project["lang"]]
    b = BOARDS[project["board"]]
    usb_mark = {True: "**⚠\ufe0f %s**" % T["unplug"], False: "**%s**" % T["stay"]}
    out = ["# %s" % md(T["guide_title"].format(title=project["title"])), "",
           T["board_line"].format(board=b["name"], ma=b["pin_ma"]), "",
           "![%s](overview.%s)" % (md(T["overview"]), image_ext), "",
           "## %s" % T["safety_head"], ""]
    out += ["- %s" % s for s in T["safety"]]
    mm, fm = jumper_counts(wires)
    out += ["", "## %s" % T["need_head"], "", "- %s" % T["need_breadboard"]]
    if mm:
        out.append("- %s" % T["need_mm"].format(n=mm))
    if fm:
        out.append("- %s" % T["need_fm"].format(n=fm))
    for p in project["parts"]:
        if p["id"] in project["existing"]:
            continue
        extra = ""
        if p["type"] == "led":
            extra = " (%s)" % T["led_colors"][p["color"]]
        out.append("- %s" % md(T["need_part"].format(name=part_name(project, p), extra=extra)))
        if p.get("resistor"):
            r = p["resistor"]
            out.append("- %s" % md(T["need_res"].format(
                ohm=fmt_ohm(r), name=part_name(project, p),
                b4=", ".join(T["bands"][x] for x in BANDS4[r]), b5=", ".join(T["bands"][x] for x in BANDS5[r]))))
        if p["type"] == "speaker":
            out.append("- %s" % T["need_spk"])
    out += ["", "## %s" % T["pins_head"], "", T["pin_table"], "| --- | --- | --- |"]
    for p in project["parts"]:
        for sig, pin in p["pins"].items():
            out.append("| %s | %s | %s |" % (md(part_name(project, p)), md(sig), pin))
    if project["existing"]:
        names = [T["rails_name"] if e == "rails" else part_name(project, next(p for p in project["parts"] if p["id"] == e))
                 for e in sorted(project["existing"])]
        out += ["", md(T["existing"].format(what=", ".join(names)))]
    out += ["", "## %s" % T["notes_head"], ""] + ["- %s" % n for n in notes_for(project)]
    for i, s in enumerate(steps):
        out += ["", "## %s" % T["step"].format(n=i + 1, total=len(steps)), "",
                usb_mark[s["unplug"]], ""]
        if not s["unplug"]:
            out += [T["stay_extra"], ""]
        out += ["*%s*" % md(s["title"]), ""]
        lines, checks = step_lines(project, lay, s)
        out += ["%d. %s" % (j + 1, md(l)) for j, l in enumerate(lines)]
        if s["note"]:
            out += ["", "%s %s" % (T["note_prefix"], md(s["note"]))]
        out += ["", "![%s](step-%02d.%s)" % (md(T["step"].format(n=i + 1, total=len(steps))), i + 1, image_ext)]
        if checks:
            out += ["", "%s: %s" % (T["check_head"], " ".join(md(c) for c in checks))]
    out += ["", "## %s" % T["final_head"], "", usb_mark[True], ""]
    out += ["%d. %s" % (j + 1, s) for j, s in enumerate(T["final"])]
    return "\n".join(out) + "\n"


def plan_json(project, steps, wires):
    T = STR[project["lang"]]
    return {
        "generator": GENERATOR, "version": 1, "complete": True,
        "title": project["title"], "board": project["board"], "language": project["lang"],
        "parts": [{"id": p["id"], "type": p["type"], "label": p["label"], "pins": p["pins"],
                   "column": p.get("col"), "resistor_ohm": p.get("resistor")} for p in project["parts"]],
        "existing": sorted(project["existing"]),
        "steps": [{"n": i + 1, "title": s["title"], "usb": "unplug" if s["unplug"] else "may-stay-plugged",
                   "usb_line": T["unplug"] if s["unplug"] else T["stay"],
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


OUR_IMAGE = re.compile(r"(?:step-[0-9]{2}|overview)\.(?:svg|png)", re.IGNORECASE | re.ASCII)
OUR_TEXT = {"guide.md", "plan.json"}


def _is_our_plan(path):
    try:
        st = os.lstat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size > 1024 * 1024:
            return False
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return False
    return isinstance(data, dict) and data.get("generator") == GENERATOR


def prepare_out(out, targets=()):
    """targets: the file names this build will write, checked before anything is touched."""
    out = Path(out)
    if out.is_symlink():
        raise SpecError("output folder %s is a symlink: pick a real folder" % out)
    if out.exists() and not out.is_dir():
        raise SpecError("%s exists and is not a folder" % out)
    out.mkdir(parents=True, exist_ok=True)
    entries = list(out.iterdir())
    if entries and not _is_our_plan(out / "plan.json"):
        raise SpecError("output folder %s is not empty and holds no earlier build of this skill: pick an empty folder" % out)
    ours = [e for e in entries if e.name.casefold() in OUR_TEXT or OUR_IMAGE.fullmatch(e.name)]
    for e in ours + [out / t for t in targets]:  # check everything before touching anything
        try:
            st = os.lstat(e)
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:
            raise SpecError("refusing to write: %s is not a plain file" % e)
    for e in ours:
        if OUR_IMAGE.fullmatch(e.name) or e.name.casefold() == "guide.md":
            os.unlink(e)  # old images and guide of an earlier build: never mix old and new
    return out


def safe_write(path, data):
    """Write through a new temp file and an atomic rename: never follows links into other files."""
    path = Path(path)
    if isinstance(data, str):
        data = data.encode("utf-8")
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        st = None
    if st is not None and (not stat.S_ISREG(st.st_mode) or st.st_nlink > 1):
        raise SpecError("refusing to write: %s is not a plain file" % path)
    fd, tmp = tempfile.mkstemp(prefix=".wiring-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _looks_like_chrome(path):
    return re.search(r"(?i)chrom", os.path.basename(path)) is not None


def _runnable(path):
    return os.path.isfile(path) and os.access(path, os.X_OK)


def chrome_env_problem():
    env = os.environ.get("CHROME_PATH")
    if env is None or env == "":
        return None
    if os.path.isabs(env) and _runnable(env) and _looks_like_chrome(env):
        return None
    return "CHROME_PATH ignored: it must be the absolute path of a Chrome or Chromium program"


def find_chrome():
    env = os.environ.get("CHROME_PATH")
    if env and chrome_env_problem() is None:
        return env
    home = os.path.expanduser("~")
    fixed = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
             "/Applications/Chromium.app/Contents/MacOS/Chromium",
             os.path.join(home, "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
             os.path.join(home, "Applications/Chromium.app/Contents/MacOS/Chromium")]
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base and os.path.isabs(base):
            fixed.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
    for c in fixed:
        if _runnable(c):
            return c
    # PATH lookup by hand: absolute folders only, never the current folder
    cwd = os.path.realpath(os.getcwd())
    exts = [".exe"] if os.name == "nt" else [""]
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        if not folder or not os.path.isabs(folder) or os.path.realpath(folder) == cwd:
            continue
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
            for ext in exts:
                p = os.path.join(folder, name + ext)
                if _runnable(p):
                    return p
    return None


def chrome_cmd(chrome, svg_path, png_path, w, h, profile):
    return [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
            "--no-default-browser-check", "--disable-extensions", "--disable-sync",
            "--disable-background-networking", "--disable-component-update", "--disable-default-apps",
            "--no-pings", "--mute-audio", "--use-mock-keychain", "--password-store=basic",
            "--user-data-dir=%s" % profile, "--window-size=%d,%d" % (w, h),
            "--force-device-scale-factor=2", "--screenshot=%s" % png_path, Path(svg_path).resolve().as_uri()]


def png_ok(data):
    """A complete PNG: signature, IHDR first, IEND last."""
    return (len(data) > 45 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR"
            and data[-12:] == b"\x00\x00\x00\x00IEND\xaeB`\x82")


def render_png(chrome, svg_path, w, h):
    """PNG bytes, or None. Chrome writes only inside its own temp folder."""
    tmp = tempfile.mkdtemp(prefix="wiring-chrome-")
    try:
        shot = os.path.join(tmp, "shot.png")
        cmd = chrome_cmd(chrome, svg_path, shot, w, h, os.path.join(tmp, "profile"))
        try:
            done = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  timeout=PNG_TIMEOUT, check=False)
            if done.returncode != 0:
                return None
            with open(shot, "rb") as f:
                data = f.read()
        except (subprocess.TimeoutExpired, OSError):
            return None
        return data if png_ok(data) else None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def build(spec_path, out, png=False, lang=None):
    project = load_spec(spec_path)
    if lang:
        if lang not in STR:
            raise SpecError("language must be one of: %s" % ", ".join(STR))
        project["lang"] = lang
    lay, wires, steps, height = compute(project)
    jobs = [("overview", None)] + [("step-%02d" % (i + 1), i) for i in range(len(steps))]
    targets = [name + ext for name, _ in jobs for ext in (".svg", ".png")] + sorted(OUR_TEXT)
    out = prepare_out(out, targets)
    # the marker first: a build cut short still leaves a folder the next build accepts
    safe_write(out / "plan.json", json.dumps({"generator": GENERATOR, "version": 1, "complete": False}) + "\n")
    warnings = []
    files, sizes = [], {}
    for name, k in jobs:
        text, W, H = render_svg(project, lay, height, wires, steps, k)
        safe_write(out / (name + ".svg"), text)
        sizes[name] = (W, H)
        files.append(out / (name + ".svg"))
    ext = "svg"
    if png:
        problem = chrome_env_problem()
        if problem:
            warnings.append(problem)
        chrome = find_chrome()
        if not chrome:
            warnings.append("no Chrome or Chromium found, so SVG only (CHROME_PATH can point at another install)")
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=PNG_WORKERS) as pool:
                shots = list(pool.map(lambda job: render_png(chrome, out / (job[0] + ".svg"), *sizes[job[0]]), jobs))
            if all(shots):
                for (name, _), data in zip(jobs, shots):
                    safe_write(out / (name + ".png"), data)
                    files.append(out / (name + ".png"))
                ext = "png"
            else:
                warnings.append("Chrome did not produce every PNG, so the guide uses the SVG files")
    safe_write(out / "guide.md", render_guide(project, lay, steps, wires, ext))
    safe_write(out / "plan.json", json.dumps(plan_json(project, steps, wires), indent=2, ensure_ascii=False) + "\n")
    files += [out / "guide.md", out / "plan.json"]
    return project, steps, files, warnings


def summary(project, steps, wires):
    T = STR[project["lang"]]
    b = BOARDS[project["board"]]
    lines = ["Board: %s (max %d mA per pin)" % (b["name"], b["pin_ma"]), "Parts:"]
    for p in project["parts"]:
        bits = ["%s=%s" % kv for kv in p["pins"].items()]
        if p.get("col"):
            bits.append("column %d" % p["col"])
        if p.get("resistor"):
            bits.append("resistor %s" % resistor_text(project["lang"], p["resistor"]))
        if p["id"] in project["existing"]:
            bits.append("existing")
        lines.append("  %s (%s): %s" % (p["id"], p["type"], ", ".join(bits)))
    mm, fm = jumper_counts(wires)
    lines.append("Jumpers: %d male-to-male, %d female-to-male" % (mm, fm))
    lines.append("Steps (the text in brackets is the USB line to use):")
    for i, s in enumerate(steps):
        lines.append("  %d. [%s] %s" % (i + 1, T["unplug"] if s["unplug"] else T["stay"], s["title"]))
        for w in s["wires"]:
            lines.append("       %s" % w["id"])
    lines.append("Notes:")
    lines += ["  - %s" % n for n in notes_for(project)]
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
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    ap = argparse.ArgumentParser(description="Breadboard wiring guide for Arduino UNO boards")
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
            project, steps, files, warnings = build(args.spec, args.out, args.png, args.lang)
            for f in files:
                print("wrote %s" % f)
            for wmsg in warnings:
                print("note: %s" % wmsg)
            print("%d steps; USB: %s" % (len(steps), ", ".join("unplug" if s["unplug"] else "may stay" for s in steps)))
    except SpecError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except OSError as exc:
        print("error: cannot read or write a file: %s" % (exc.strerror or "error"), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
