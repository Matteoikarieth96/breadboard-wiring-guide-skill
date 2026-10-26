# Security

## Threat model

What the skill reads, runs and writes, and what could be hostile:

- **Inputs**: the JSON spec (written by Claude from what the user says, or by
  the user), and photos, datasheets or sketches the user shares in chat. All
  of it is untrusted: titles, labels and notes end up in SVG and Markdown
  files that a browser or viewer may render.
- **Instructions hidden in inputs** (text in a photo, a datasheet, a pasted
  sketch, a spec) are data, not commands. SKILL.md says so.
- **Commands** the skill runs: `python3 scripts/wiring.py`; with `--png`, a
  headless Chrome or Chromium with a throwaway profile, opening only the SVG
  files the script just wrote. Uploading code to a board happens only when
  the user asks.
- **Network**: none. The script imports only the Python standard library and
  opens no sockets; the SVG files load no fonts, scripts or images.
- **Outputs**: SVG, PNG, `guide.md` and `plan.json` in the output folder the
  user picks.
- **Physical**: wrong wiring can damage parts. The script decides the USB
  call per step and the resistor values; SKILL.md tells Claude never to
  weaken either.

## Mitigations

| Risk | Mitigation | File |
| --- | --- | --- |
| Markup or script injected through titles and labels | All text is XML-escaped in SVG; Markdown escapes `<`, `>`, `&`, brackets, pipes and backticks | `scripts/wiring.py` (`xml`, `md`) |
| Invisible or direction-changing characters in text | Rejected at validation (control, format, private-use characters) | `scripts/wiring.py` (`clean_text`) |
| Huge or malformed spec | 256 KB limit, strict keys and types, at most 12 parts and 20 steps | `scripts/wiring.py` (`load_spec`, `validate`) |
| Overwriting files outside the output folder | Writes only fixed file names; refuses a symlinked folder or file (`O_NOFOLLOW`); refuses a non-empty folder that holds no earlier build; deletes only its own old `step-NN` files | `scripts/wiring.py` (`prepare_out`, `safe_write`) |
| Shell injection through paths | Chrome runs with an argument list, no shell, a 90 s timeout and a temporary profile | `scripts/wiring.py` (`chrome_cmd`, `render_png`) |
| A spec saying USB can stay plugged in when it cannot | The USB call is computed; a spec can only force "unplug" | `scripts/wiring.py` (`build_steps`) |
| Pin overcurrent | Resistors picked for 80% of the board's per-pin limit, worst case | `scripts/wiring.py` (`pick_resistor`) |

Tests for these cases: `tests/test_wiring.py` (`TestValidation`,
`TestOutput.test_escaping`, `test_refuses_symlinks`,
`test_safe_write_never_follows`, `test_refuses_foreign_folder`,
`TestUsbRule`, `test_current_stays_under_limit`).

## Residual risks

- Chrome or Chromium renders the generated SVG. The text in it is escaped
  and it references nothing external, but a browser bug is outside this
  skill's control. Skip `--png` to avoid running a browser.
- Module pin orders vary between makers. The guide says to read the labels
  on the part, but a user who follows the drawing blindly can still power a
  module backwards.
- The skill cannot see the real bench. A photo check by Claude can miss a
  wire one hole off.
- The checks on symlinks run just before writing; a process racing on the
  same folder at the same moment is not defended against.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting: open the repository's
**Security** tab and choose **Report a vulnerability**. Do not open a public
issue for security problems.
