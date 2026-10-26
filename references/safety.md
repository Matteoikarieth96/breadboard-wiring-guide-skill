# Safety background

Everything in this skill runs on the 5 V of the USB cable. That is low
voltage, but a wrong wire can still destroy a pin, a module or the board, and
a short can make a part hot.

## The USB call on every step

- Unplug USB first: any step that places a part or a module, or touches a
  power wire (5V, GND, the + and - rails, a module's VCC or GND).
- USB can stay plugged in: only steps that add or move signal wires (D pins,
  A pins, SDA, SCL) on parts that are already powered and placed.
- Moving a power wire or a module later: unplug again. When in doubt, unplug.

The script decides the call for each step. A spec can make a step stricter
(`"usb": "unplug"`), never looser.

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
