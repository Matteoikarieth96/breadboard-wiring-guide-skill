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
  the user asks, after seeing the sketch and the port.
- **Generated files** (`check` output, `guide.md`, `plan.json`, images)
  repeat text from the spec. SKILL.md tells Claude to treat them as data and
  to take the USB call only from the script's own fields.
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
| Markup or script injected through titles and labels | All text is XML-escaped in SVG; Markdown escapes HTML characters, brackets, pipes, backticks, `*`, `_`, `#`, `~`; notes sit behind a fixed "Note:" prefix | `scripts/wiring.py` (`xml`, `md`) |
| Spec text imitating the USB line or carrying links | Titles, labels, notes and LCD text that mention USB or plugging in, or hold links or e-mail addresses, are refused | `scripts/wiring.py` (`clean_text`) |
| Invisible, direction-changing or non-XML characters | Rejected at validation (control, format, private-use, unassigned, variation selectors) | `scripts/wiring.py` (`clean_text`) |
| Terminal escape codes in error lines | Untrusted values are printed with `ascii()`, cut to 40 characters | `scripts/wiring.py` (`show`) |
| Huge, odd or malformed spec | Regular files only (no FIFOs or devices), 256 KB limit, NaN and deep nesting refused, strict keys and types, at most 12 parts and 20 steps | `scripts/wiring.py` (`load_spec`, `validate`) |
| Overwriting or deleting the user's files | Writes only fixed file names; a non-empty folder must hold a `plan.json` made by this skill; every file it would touch is checked first (plain file, one link); writes go to a temp file renamed into place; deletes only its own old images | `scripts/wiring.py` (`prepare_out`, `safe_write`) |
| Running a planted program as Chrome | `CHROME_PATH` must be an absolute path to a program named like Chrome or Chromium; the PATH search skips relative folders and the working folder | `scripts/wiring.py` (`chrome_env_problem`, `find_chrome`) |
| Chrome side effects | Argument list, no shell, 90 s timeout, temporary profile, mock keychain (no macOS keychain prompts), background networking off; it writes only inside its own temp folder, and a failed or partial render falls back to SVG with a note | `scripts/wiring.py` (`chrome_cmd`, `render_png`, `build`) |
| A step saying USB can stay plugged in when it cannot | The USB call is computed: signal wires only, and only once the rails and every touched part's power wires are in place; a spec can only force "unplug" | `scripts/wiring.py` (`build_steps`, `wire_signal_only`) |
| Pin overcurrent | Resistors picked for 80% of the board's per-pin limit, worst case | `scripts/wiring.py` (`pick_resistor`) |
| An old sketch driving a wired pin | Guide and SKILL.md ask for an empty sketch before wiring, before any "USB can stay plugged in" step and before power-on | `SKILL.md`, `scripts/wiring.py` (`STR`) |

Tests for these cases: `tests/test_wiring.py` (`TestValidation`,
`TestUsbRule`, `TestReviewFixes`, `TestOutput.test_escaping`,
`test_refuses_symlinks`, `test_safe_write_never_follows`,
`test_refuses_foreign_folder`, `test_current_stays_under_limit`).

## Residual risks

- Chrome or Chromium renders the generated SVG. The text in it is escaped
  and it references nothing external, but a browser bug is outside this
  skill's control. Skip `--png` to avoid running a browser.
- Module pin orders vary between makers. The guide says to read the labels
  on the part, but a user who follows the drawing blindly can still power a
  module backwards.
- The skill cannot see the real bench. A photo check by Claude can miss a
  wire one hole off.
- The checks on links run just before writing; a process racing on the
  same folder at the same moment is not defended against.
- The script cannot know which sketch runs on the board. It relies on the
  user following the empty-sketch rule.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting: open the repository's
**Security** tab and choose **Report a vulnerability**. Do not open a public
issue for security problems.
