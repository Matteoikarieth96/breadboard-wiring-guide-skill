# Safety background

Everything in this skill runs on the 5 V of the USB cable. That is low
voltage, but a wrong wire can still destroy a pin, a module or the board, and
a short can make a part hot.

## The USB call on every step

- Unplug USB first: any step that places a part or a module, or touches a
  power wire (5V, GND, the + and - rails, a module's VCC or GND).
- USB can stay plugged in: only steps that add signal wires (D pins, A pins,
  SDA, SCL) to parts whose power wires, and the rails, are already in place.
- Moving a power wire or a module later: unplug again. When in doubt, unplug.

The script decides the call for each step, including that precondition. A
spec can make a step stricter (`"usb": "unplug"`), never looser.

## Before the first wire

- **Only USB power.** Nothing in the DC jack or on VIN, so that unplugging
  USB really leaves the board without power.
- **The sketch on the board.** The board runs the last sketch it got, also
  while you wire. If that sketch sets a pin as OUTPUT, a button or joystick
  switch to GND, a potentiometer at its end stop or a sensor output on the
  same pin can short it. Upload an empty sketch first (File > Examples >
  01.Basics > BareMinimum), or check that the sketch drives none of the pins
  you wire.
- **Rails.** Use the + rail (red line) and the - rail (blue line) printed on
  your breadboard. Their order can differ from the drawing, rail holes have
  no column numbers, and any hole along a rail works.

## Pin current

| Board | Per pin | Logic level |
| --- | --- | --- |
| UNO R3 | 20 mA recommended (40 mA absolute maximum) | 5 V |
| UNO R4 Minima, UNO R4 WiFi | 8 mA | 5 V |

The script picks the smallest common resistor that keeps each pin at 80% of
its limit or less, assuming a low LED forward voltage (more current, so the
pick stays safe for every LED of that colour). A speaker or buzzer is counted
as zero ohms for the same reason. Bigger resistors are always fine: the LED
gets dimmer or the sound quieter.

## Polarity

- LEDs: long leg toward the resistor.
- Modules: VCC to the + rail, GND to the - rail. A module powered backwards
  can be destroyed in a second, so read the labels on your module, not only
  the drawing.
- Never a wire between the + rail and the - rail: that shorts the USB supply.
- A speaker never goes straight to a rail: 8 ohm across 5 V draws about
  0.6 A and gets hot. Its + lead goes to the resistor's column only.

## Out of scope

Not covered, so do not improvise them from this skill:
- mains voltage of any kind;
- motors, servos, LED strips, relays, solenoids (they need their own supply);
- batteries and battery modules, chargers;
- boards with 3.3 V logic (5 V module signals can damage them), and boards
  outside the UNO family.

## Disclaimer

The drawings show common pin orders. Your module may differ: the labels on
the part win. Check the datasheet of your parts. Use at your own risk.
