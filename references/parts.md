# Parts: what they need and how to test them

Notes for the test sketches and for troubleshooting. Pin names on modules
vary between makers: always read what is printed on the part.

## Push button (4 legs)

- The legs come in two joined pairs. Wired legs must be diagonal, which the
  layout already does: one leg to the signal pin, the opposite corner to GND.
- Sketch: `pinMode(pin, INPUT_PULLUP)`. Released reads `HIGH`, pressed reads
  `LOW`. No external resistor needed.
- A press can bounce for a few milliseconds: ignore changes for about 20 ms.
- Avoid D13 for inputs: the on-board LED on that pin can keep INPUT_PULLUP
  from reading HIGH.

## LED

- Long leg (+, anode) toward the resistor, short leg (-, cathode) to GND.
  The flat edge of the rim is the - side.
- Sketch: `pinMode(pin, OUTPUT)`, `digitalWrite(pin, HIGH)` lights it.
  Brightness with `analogWrite(pin, 0..255)` works on the pins marked `~`
  (3, 5, 6, 9, 10, 11).
- Backwards it simply stays dark.

## Passive buzzer

- The layout puts its legs 3 holes apart (7.6 mm), the usual spacing of 12 mm
  buzzers. Measure yours: if the spacing differs, say so before wiring.
- Sketch: `tone(pin, 1000)` for a 1 kHz note, `noTone(pin)` to stop.
- Through the resistor it is quiet but safe for the pin.
- Active buzzers (they beep on plain DC) usually need more current than an
  UNO pin should give: not covered here.

## Potentiometer

- Outer legs to + and -, middle leg (wiper) to an analog pin.
- Most breadboard potentiometers fit three holes in a row. One with wider
  legs needs a different layout: say so before wiring.
- Sketch: `analogRead(pin)` gives 0 to 1023 from one end to the other.
  Turning it the "wrong" way just swaps the ends.

## Speaker (8 ohm)

- Always through the series resistor. A bare speaker on a pin is close to a
  short circuit.
- Sketch: `tone(pin, 440, 200)` plays a short note.
- Quiet on purpose. Real volume needs an amplifier module or a transistor
  stage: not covered here.

## LCD 1602 with I2C adapter

- Four wires: GND, VCC (5 V), SDA, SCL. On UNO boards SDA and SCL are the
  same signals as A4 and A5.
- Library: "LiquidCrystal I2C" from the Library Manager (or any library for
  PCF8574 adapters).
- Address is usually `0x27` or `0x3F`. An I2C scanner sketch prints it.
- Backlight on but no text: turn the contrast screw on the adapter. No
  backlight at all: check the small jumper on the adapter and the 5 V wire.

## Joystick module

- Two potentiometers (X and Y) and a push switch. Labels vary: `VRx`, `VRX`,
  `URX`; `+5V` or `VCC`.
- Sketch: `analogRead` on X and Y gives about 512 at rest and 0 or 1023 at
  the ends. `pinMode(sw, INPUT_PULLUP)`: pressing the stick reads `LOW`.
- Which way is X depends on how you hold it: test and swap in code.

## HC-SR04 distance sensor

- 5 V part with four pins: VCC, Trig, Echo, GND.
- Sketch: set Trig `HIGH` for 10 microseconds, then
  `pulseIn(echo, HIGH, 30000)`. Distance in cm is about the pulse length in
  microseconds divided by 58. A result of 0 means no echo.
- Range roughly 2 cm to 4 m. Soft or slanted surfaces give odd readings.
- Always no echo: Trig and Echo are swapped, or the sensor faces the wires.

## PIR motion sensor (HC-SR501 style)

- Three pins: VCC, OUT, GND, in an order that changes between boards. Labels
  are on the board, sometimes hidden under the white dome.
- Powered from the 5 V rail. Its output is about 3.3 V when it sees motion.
- Sketch: `analogRead(pin)` above about 600 means motion; this works on UNO
  R3 and R4. `digitalRead` often works on an R3 but with little margin, and
  may not on an R4.
- After power-up it needs up to a minute to settle. Two small screws set the
  range and how long the output stays on; a jumper picks single or repeated
  triggering.
