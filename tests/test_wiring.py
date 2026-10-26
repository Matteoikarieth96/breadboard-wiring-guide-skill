"""Tests for scripts/wiring.py. Run: python3 -m unittest discover -s tests"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import wiring as W  # noqa: E402

EXAMPLES = sorted((ROOT / "examples").glob("*.json"))


def spec(**kw):
    base = {"title": "Test bench", "board": "uno-r4-wifi",
            "parts": [{"id": "btn", "type": "button", "pin": "D2"}]}
    base.update(kw)
    return base


def compute(data):
    project = W.validate(data)
    lay, wires, steps, height = W.compute(project)
    return project, lay, wires, steps


class TempDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write_spec(self, data, name="spec.json"):
        p = self.tmp / name
        p.write_text(json.dumps(data), encoding="utf-8")
        return p


class TestValidation(unittest.TestCase):
    def bad(self, data, needle):
        with self.assertRaises(W.SpecError) as cm:
            compute(data)
        self.assertIn(needle, str(cm.exception))

    def test_unknown_keys(self):
        self.bad(spec(colour="red"), "unknown spec keys")
        self.bad(spec(parts=[{"id": "b", "type": "button", "pinn": "D2"}]), "unknown keys")

    def test_board_and_type(self):
        self.bad(spec(board="mega"), "board must be one of")
        self.bad(spec(parts=[{"id": "x", "type": "servo"}]), "type must be one of")

    def test_ids(self):
        self.bad(spec(parts=[{"id": "Bad Id", "type": "button"}]), "id must be")
        self.bad(spec(parts=[{"id": "rails", "type": "button"}]), "id must be")
        self.bad(spec(parts=[{"id": "a", "type": "button"}, {"id": "a", "type": "led"}]), "duplicate part id")

    def test_pins(self):
        self.bad(spec(parts=[{"id": "a", "type": "button", "pin": "D2"},
                             {"id": "b", "type": "led", "pin": "2"}]), "used twice")
        self.bad(spec(parts=[{"id": "a", "type": "button", "pin": "5V"}]), "power or special pin")
        self.bad(spec(parts=[{"id": "a", "type": "button", "pin": "D14"}]), "unknown pin")
        self.bad(spec(parts=[{"id": "p", "type": "potentiometer", "pin": "D3"}]), "analog pin")
        self.bad(spec(parts=[{"id": "a", "type": "button", "pin": "SDA"}]), "I2C modules only")
        self.bad(spec(parts=[{"id": "s", "type": "lcd1602-i2c"},
                             {"id": "p", "type": "potentiometer", "pin": "A4"}]), "same signal as SDA/SCL")
        self.bad(spec(parts=[{"id": "j", "type": "joystick", "pins": {"VRZ": "A0"}}]), "has no signal")
        self.bad(spec(parts=[{"id": "j", "type": "joystick", "pin": "A0"}]), "several signals")

    def test_pin_aliases(self):
        project = W.validate(spec(parts=[{"id": "a", "type": "button", "pin": 7},
                                         {"id": "p", "type": "potentiometer", "pin": "a2"}]))
        self.assertEqual(project["parts"][0]["pins"], {"SIG": "D7"})
        self.assertEqual(project["parts"][1]["pins"], {"WIPER": "A2"})

    def test_text_limits(self):
        self.bad(spec(title=""), "title is empty")
        self.bad(spec(title="x" * 81), "longer than")
        self.bad(spec(title="evil \u202e text"), "invisible")
        self.bad(spec(title="tag \U000e0041 smuggle"), "invisible")
        self.bad(spec(parts=[{"id": "a", "type": "button", "label": "zero\u200bwidth"}]), "invisible")
        self.bad(spec(parts=[{"id": "a", "type": "button", "label": 5}]), "must be text")

    def test_too_many_parts(self):
        parts = [{"id": "b%d" % i, "type": "led"} for i in range(W.MAX_PARTS + 1)]
        self.bad(spec(parts=parts), "at most")

    def test_breadboard_full(self):
        parts = [{"id": "b%d" % i, "type": "led"} for i in range(6)]
        self.bad(spec(parts=parts), "does not fit")

    def test_columns(self):
        self.bad(spec(parts=[{"id": "a", "type": "button", "column": 1}]), "does not fit")
        self.bad(spec(parts=[{"id": "a", "type": "button", "column": 10},
                             {"id": "b", "type": "led", "column": 11}]), "overlaps")
        self.bad(spec(parts=[{"id": "s", "type": "pir", "column": 5}]), "only applies to breadboard")

    def test_led_color(self):
        self.bad(spec(parts=[{"id": "a", "type": "led", "color": "ultraviolet"}]), "LED color")
        self.bad(spec(parts=[{"id": "a", "type": "button", "color": "red"}]), "only applies to LEDs")

    def test_steps(self):
        parts = [{"id": "j", "type": "joystick"}]
        self.bad(spec(parts=parts, steps=[{"title": "x", "parts": ["j"], "usb": "stay"}]),
                 "can only be \"unplug\"")
        self.bad(spec(parts=parts + [{"id": "b", "type": "button"}], steps=[{"parts": ["j"]}]),
                 "in no step")
        self.bad(spec(parts=parts, steps=[{"parts": ["j"]}, {"wires": ["j.SW"]}, {"wires": ["j.SW"]}]),
                 "claimed twice")
        self.bad(spec(parts=parts, steps=[{"parts": ["j"]}, {"wires": ["j.NOPE"]}]), "no wire named")
        self.bad(spec(parts=parts, steps=[{"wires": ["j.SW"]}, {"parts": ["j"]}]),
                 "before its part is placed")
        self.bad(spec(parts=parts, steps=[{"title": "empty"}]), "no parts and no wires")
        self.bad(spec(parts=parts, existing=["j"], steps=[{"parts": ["j"]}]), "listed as existing")

    def test_existing(self):
        self.bad(spec(existing=["ghost"]), "unknown part")
        self.bad(spec(existing=["rails", "btn"]), "nothing to wire")

    def test_one_i2c_module(self):
        self.bad(spec(parts=[{"id": "a", "type": "lcd1602-i2c"}, {"id": "b", "type": "lcd1602-i2c"}]),
                 "only one I2C module")


class TestFiles(TempDir):
    def test_too_big(self):
        p = self.tmp / "big.json"
        p.write_bytes(b" " * (W.MAX_SPEC_BYTES + 1))
        with self.assertRaises(W.SpecError):
            W.load_spec(p)

    def test_not_json(self):
        p = self.tmp / "bad.json"
        p.write_text("{nope", encoding="utf-8")
        with self.assertRaises(W.SpecError):
            W.load_spec(p)

    def test_missing_file(self):
        with self.assertRaises(W.SpecError):
            W.load_spec(self.tmp / "absent.json")


class TestPinsAndResistors(unittest.TestCase):
    def test_auto_pins_skip_i2c(self):
        project, *_ = compute(spec(parts=[
            {"id": "s", "type": "lcd1602-i2c"},
            {"id": "p1", "type": "potentiometer"}, {"id": "p2", "type": "potentiometer"},
            {"id": "j", "type": "joystick"}, {"id": "m", "type": "pir"},
        ]))
        used = [pin for p in project["parts"] for pin in p["pins"].values()]
        self.assertNotIn("A4", used)
        self.assertNotIn("A5", used)
        self.assertEqual(len(used), len(set(used)))
        pir = next(p for p in project["parts"] if p["id"] == "m")
        self.assertTrue(pir["pins"]["OUT"].startswith("D"))  # analog pins ran out

    def test_auto_digital_starts_at_d2(self):
        project, *_ = compute(spec(parts=[{"id": "a", "type": "button"}, {"id": "b", "type": "led"}]))
        self.assertEqual([p["pins"]["SIG"] for p in project["parts"]], ["D2", "D3"])

    def resistor(self, board, part):
        project, *_ = compute(spec(board=board, parts=[part]))
        return project["parts"][0]["resistor"]

    def test_led_resistors(self):
        self.assertEqual(self.resistor("uno-r3", {"id": "l", "type": "led", "color": "red"}), 220)
        self.assertEqual(self.resistor("uno-r4-wifi", {"id": "l", "type": "led", "color": "red"}), 680)
        self.assertEqual(self.resistor("uno-r4-minima", {"id": "l", "type": "led", "color": "blue"}), 470)

    def test_speaker_and_buzzer_resistors(self):
        self.assertEqual(self.resistor("uno-r3", {"id": "s", "type": "speaker"}), 330)
        self.assertEqual(self.resistor("uno-r4-wifi", {"id": "s", "type": "speaker"}), 1000)
        self.assertEqual(self.resistor("uno-r4-wifi", {"id": "b", "type": "buzzer"}), 1000)

    def test_current_stays_under_limit(self):
        for board, info in W.BOARDS.items():
            for color, (vf, _) in W.LED_COLORS.items():
                r = W.pick_resistor(vf, info["pin_ma"])
                self.assertLessEqual((5.0 - vf) / r * 1000, info["pin_ma"] * 0.8, (board, color))
            r = W.pick_resistor(0.0, info["pin_ma"])
            self.assertLessEqual(5.0 / r * 1000, info["pin_ma"] * 0.8, board)


class TestUsbRule(unittest.TestCase):
    def test_default_steps_unplug(self):
        _, _, _, steps = compute(spec(parts=[{"id": "a", "type": "button"}, {"id": "j", "type": "joystick"}]))
        self.assertEqual(steps[0]["parts"], ["rails"])
        self.assertTrue(all(s["unplug"] for s in steps))

    def test_signal_only_step_may_stay(self):
        _, _, _, steps = compute(spec(
            parts=[{"id": "j", "type": "joystick"}],
            steps=[{"parts": ["j"]}, {"wires": ["j.VRX", "j.VRY", "j.SW"]}]))
        self.assertTrue(steps[1]["unplug"])  # rails step comes first
        self.assertTrue(steps[1]["parts"] == ["j"])
        self.assertFalse(steps[2]["unplug"])
        self.assertEqual({w["id"] for w in steps[1]["wires"]}, {"j.GND", "j.+5V"})

    def test_power_wire_forces_unplug(self):
        _, _, _, steps = compute(spec(
            existing=["rails"], parts=[{"id": "j", "type": "joystick"}],
            steps=[{"parts": ["j"]}, {"wires": ["j.SW", "j.GND"]}]))
        self.assertTrue(steps[1]["unplug"])

    def test_force_unplug(self):
        _, _, _, steps = compute(spec(
            existing=["rails"], parts=[{"id": "j", "type": "joystick"}],
            steps=[{"parts": ["j"]}, {"wires": ["j.SW"], "usb": "unplug"}]))
        self.assertTrue(steps[1]["unplug"])

    def test_existing_wires_have_no_step(self):
        _, _, wires, steps = compute(spec(
            existing=["rails", "btn"],
            parts=[{"id": "btn", "type": "button"}, {"id": "l", "type": "led"}]))
        self.assertEqual(len(steps), 1)
        self.assertTrue(all(w["step"] == -1 for w in wires if w["part"] in ("rails", "btn")))

    def test_bridge_goes_with_first_button(self):
        _, _, wires, steps = compute(spec(parts=[{"id": "l", "type": "led"}, {"id": "b", "type": "button"}]))
        bridge = next(w for w in wires if w["id"] == "rails.bridge")
        self.assertEqual(bridge["step"], 2)
        self.assertNotIn("rails.bridge", [w["id"] for w in wires if w["part"] == "l"])

    def test_no_bridge_without_button(self):
        _, _, wires, _ = compute(spec(parts=[{"id": "l", "type": "led"}]))
        self.assertNotIn("rails.bridge", [w["id"] for w in wires])


def segments(path):
    return list(zip(path, path[1:]))


def overlap(s1, s2):
    (a, b), (c, d) = s1, s2
    if a[0] == b[0] == c[0] == d[0]:
        lo1, hi1 = sorted((a[1], b[1]))
        lo2, hi2 = sorted((c[1], d[1]))
        return min(hi1, hi2) - max(lo1, lo2) > 0
    if a[1] == b[1] == c[1] == d[1]:
        lo1, hi1 = sorted((a[0], b[0]))
        lo2, hi2 = sorted((c[0], d[0]))
        return min(hi1, hi2) - max(lo1, lo2) > 0
    return False


def inside(pt, rect):
    x0, y0, x1, y1 = rect
    return x0 < pt[0] < x1 and y0 < pt[1] < y1


class TestRouting(unittest.TestCase):
    def check_project(self, data):
        project, lay, wires, steps = compute(data)
        rects = [(lay.bx0, lay.by0, lay.bx1, lay.by1), (lay.bb_x0, lay.bb_y0, lay.bb_x1, lay.bb_y1), lay.panel]
        rects += [(x, y, x + w, y + h) for (x, y, w, h) in lay.modules.values()]
        for w in wires:
            path = w["path"]
            self.assertGreaterEqual(len(path), 2, w["id"])
            for p, q in segments(path):
                self.assertTrue(p[0] == q[0] or p[1] == q[1], "diagonal segment in %s" % w["id"])
            if not w["direct"]:
                for pt in path[1:-1]:
                    for r in rects:
                        self.assertFalse(inside(pt, r), "%s bends inside an obstacle at %s" % (w["id"], pt))
        routed = [w for w in wires if not w["direct"]]
        for i, w1 in enumerate(routed):
            for w2 in routed[i + 1:]:
                for s1 in segments(w1["path"]):
                    for s2 in segments(w2["path"]):
                        self.assertFalse(overlap(s1, s2), "%s overlaps %s" % (w1["id"], w2["id"]))
        return wires

    def test_examples(self):
        self.assertTrue(EXAMPLES)
        for ex in EXAMPLES:
            with self.subTest(example=ex.name):
                self.check_project(json.loads(ex.read_text(encoding="utf-8")))

    def test_every_part_together(self):
        parts = [{"id": t.replace("-", "")[:8], "type": t} for t in W.PARTS if t not in ("led", "buzzer")]
        parts += [{"id": "lamp", "type": "led"}]
        self.check_project(spec(parts=parts))

    def test_r3_board(self):
        self.check_project(spec(board="uno-r3", parts=[{"id": "l", "type": "led", "pin": "D13"},
                                                       {"id": "z", "type": "buzzer", "pin": "D3"}]))


class TestOutput(TempDir):
    def build(self, data, out="out", **kw):
        p = self.write_spec(data)
        return W.build(p, self.tmp / out, **kw)

    def test_examples_build_and_parse(self):
        for ex in EXAMPLES:
            with self.subTest(example=ex.name):
                out = self.tmp / ex.stem
                project, steps, files, _ = W.build(ex, out)
                for f in files:
                    self.assertTrue(f.is_file(), f)
                    if f.suffix == ".svg":
                        ET.parse(str(f))
                self.assertEqual(len(list(out.glob("step-*.svg"))), len(steps))
                plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
                self.assertEqual(len(plan["steps"]), len(steps))

    def test_usb_line_opens_every_step(self):
        for lang, unplug, stay in (("en", "**⚠\ufe0f Unplug USB first.**", "**USB can stay plugged in.**"),
                                   ("it", "**⚠\ufe0f USB staccato.**", "**USB può restare attaccato.**")):
            ex = ROOT / "examples" / "add-a-joystick.json"
            out = self.tmp / ("joy-" + lang)
            W.build(ex, out, lang=lang)
            text = (out / "guide.md").read_text(encoding="utf-8")
            sections = text.split("\n## ")[1:]
            seen = []
            for sec in sections:
                lines = [l for l in sec.splitlines()[1:] if l.strip()]
                if sec.startswith(("Step", "Passo", "Final", "Controllo finale")):
                    self.assertIn(lines[0], (unplug, stay), sec[:40])
                    seen.append(lines[0])
            self.assertIn(unplug, seen)
            self.assertIn(stay, seen)

    def test_escaping(self):
        nasty = '<script>alert(1)</script> & "q" ]]>'
        data = spec(title=nasty[:80], parts=[{"id": "b", "type": "button", "label": "<b>x</b> [l](u)"}])
        self.build(data)
        for f in (self.tmp / "out").glob("*.svg"):
            raw = f.read_text(encoding="utf-8")
            self.assertNotIn("<script", raw)
            self.assertNotIn("<b>", raw)
            ET.parse(str(f))
        guide = (self.tmp / "out" / "guide.md").read_text(encoding="utf-8")
        self.assertNotIn("<script", guide)
        self.assertNotIn("<b>", guide)
        self.assertNotIn("[l](u)", guide)
        self.assertIn("&lt;script&gt;", guide)

    def test_deterministic(self):
        ex = ROOT / "examples" / "mini-arcade.json"
        W.build(ex, self.tmp / "a")
        W.build(ex, self.tmp / "b")
        for f in (self.tmp / "a").iterdir():
            self.assertEqual(f.read_bytes(), (self.tmp / "b" / f.name).read_bytes(), f.name)

    def test_refuses_foreign_folder(self):
        out = self.tmp / "out"
        out.mkdir()
        (out / "notes.txt").write_text("mine", encoding="utf-8")
        with self.assertRaises(W.SpecError):
            self.build(spec())
        self.assertEqual((out / "notes.txt").read_text(encoding="utf-8"), "mine")

    def test_rebuild_removes_stale_steps_only(self):
        self.build(spec(parts=[{"id": "a", "type": "button"}, {"id": "b", "type": "led"}]))
        out = self.tmp / "out"
        self.assertTrue((out / "step-03.svg").is_file())
        (out / "keep-me.txt").write_text("x", encoding="utf-8")
        self.build(spec())
        self.assertFalse((out / "step-03.svg").exists())
        self.assertTrue((out / "keep-me.txt").exists())

    @unittest.skipIf(os.name == "nt", "symlinks")
    def test_refuses_symlinks(self):
        victim = self.tmp / "victim.txt"
        victim.write_text("safe", encoding="utf-8")
        out = self.tmp / "out"
        self.build(spec())
        (out / "step-01.svg").unlink()
        os.symlink(victim, out / "step-01.svg")
        with self.assertRaises(W.SpecError):
            self.build(spec())
        self.assertEqual(victim.read_text(encoding="utf-8"), "safe")
        linked = self.tmp / "linked"
        os.symlink(self.tmp / "elsewhere", linked)
        with self.assertRaises(W.SpecError):
            W.build(self.write_spec(spec()), linked)
        self.assertFalse((self.tmp / "elsewhere").exists())

    @unittest.skipIf(os.name == "nt", "symlinks")
    def test_safe_write_never_follows(self):
        victim = self.tmp / "victim.txt"
        victim.write_text("safe", encoding="utf-8")
        link = self.tmp / "link.svg"
        os.symlink(victim, link)
        with self.assertRaises((W.SpecError, OSError)):
            W.safe_write(link, "boom")
        self.assertEqual(victim.read_text(encoding="utf-8"), "safe")

    def test_chrome_command_is_an_argument_list(self):
        cmd = W.chrome_cmd("/opt/chrome", self.tmp / "a b.svg", self.tmp / "a.png", 100, 80, "/tmp/prof")
        self.assertIsInstance(cmd, list)
        self.assertEqual(cmd[0], "/opt/chrome")
        self.assertIn("--user-data-dir=/tmp/prof", cmd)
        self.assertTrue(cmd[-1].startswith("file://"))
        self.assertIn("a%20b.svg", cmd[-1])

    def test_png_falls_back_to_svg_without_chrome(self):
        old = W.find_chrome
        W.find_chrome = lambda: None
        try:
            _, _, files, no_chrome = self.build(spec(), png=True)
        finally:
            W.find_chrome = old
        self.assertTrue(no_chrome)
        self.assertIn("step-01.svg", (self.tmp / "out" / "guide.md").read_text(encoding="utf-8"))

    @unittest.skipUnless(os.environ.get("WIRING_TEST_PNG") == "1" and W.find_chrome(), "set WIRING_TEST_PNG=1 with Chrome installed")
    def test_png_render(self):
        _, _, files, _ = self.build(spec(), png=True)
        pngs = [f for f in files if f.suffix == ".png"]
        self.assertTrue(pngs)
        self.assertEqual(pngs[0].read_bytes()[:8], b"\x89PNG\r\n\x1a\n")


class TestDocs(unittest.TestCase):
    def test_spec_format_example_is_valid(self):
        text = (ROOT / "references" / "spec-format.md").read_text(encoding="utf-8")
        block = text.split("```json", 1)[1].split("```", 1)[0]
        _, _, _, steps = compute(json.loads(block))
        self.assertTrue(steps[0]["unplug"])
        self.assertFalse(steps[-1]["unplug"])

    def test_skill_frontmatter(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        front = text.split("---", 2)[1]
        fields = dict(line.split(": ", 1) for line in front.strip().splitlines())
        self.assertEqual(fields["name"], "arduino-breadboard-guide")
        self.assertLessEqual(len(fields["description"]), 1024)
        self.assertLess(len(text.splitlines()), 500)

    def test_usb_phrases_in_skill_match_script(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        for lang in ("en", "it"):
            self.assertIn(W.STR[lang]["unplug"], text)
            self.assertIn(W.STR[lang]["stay"], text)


class TestCli(TempDir):
    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = W.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_parts(self):
        code, out, _ = self.run_main(["parts"])
        self.assertEqual(code, 0)
        for t in W.PARTS:
            self.assertIn(t, out)

    def test_check_and_error(self):
        code, out, _ = self.run_main(["check", str(ROOT / "examples" / "night-light.json")])
        self.assertEqual(code, 0)
        self.assertIn("Unplug USB first.", out)
        bad = self.write_spec(spec(board="nope"))
        code, _, err = self.run_main(["check", str(bad)])
        self.assertEqual(code, 2)
        self.assertIn("error:", err)

    def test_build(self):
        code, out, _ = self.run_main(["build", str(ROOT / "examples" / "night-light.json"), "--out", str(self.tmp / "o")])
        self.assertEqual(code, 0)
        self.assertTrue((self.tmp / "o" / "guide.md").is_file())


if __name__ == "__main__":
    unittest.main()
