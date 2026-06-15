# DESIGN.md — visual design brief for the SpO₂ screening app

Direction (chosen by the project owner): **friendly & approachable health app, light
mode, soft neutrals with a single calm accent.** Phase D (the 4-page web app) is built
to this. **Visual design is NOT frozen** — it can be changed anytime; this is just the
starting point so the first build is intentional and stays consistent across sessions.

## Feel
Warm, calm, reassuring — a consumer health app, not a cold clinical instrument and not
a flashy dark dashboard. Rounded corners, generous whitespace, soft shadows, large
readable numbers. The user is checking a sensitive health result in the morning; the
tone is gentle and never alarming.

## Color tokens (light)
Neutrals:
- `--bg`: `#F7F6F3` (warm off-white, app background)
- `--surface`: `#FFFFFF` (cards)
- `--border`: `#E7E5E0`
- `--text`: `#1F2421`
- `--text-muted`: `#6E6A63`

Single brand accent (buttons, links, active nav, focus rings):
- `--accent`: `#2DA39A` (calm teal)
- `--accent-hover`: `#238079`
- `--accent-soft`: `#E6F4F2` (tint background)

Severity colors — used ONLY for band chips/markers (you must be able to tell bands
apart; kept muted, never harsh). Always pair the color with the text label; never rely
on color alone:
- normal → bg `#E8F5EE` / text `#2E7D52`
- mild → bg `#FFF4E0` / text `#B5790E`
- moderate → bg `#FDEBDD` / text `#C2611F`
- severe → bg `#FBE9E7` / text `#C0392B`
- insufficient → bg `#EFEEEB` / text `#6E6A63`

## Type
- Body: **Inter** (via CDN) with `system-ui` fallback — friendly and highly legible.
- Result numbers/headings: same family, semibold, large. The band and lowest SpO₂
  should be the biggest things on the Verdict page.

## Shape & spacing
- Radius: cards `16px`, buttons/inputs `10px`.
- Shadow: soft, e.g. `0 1px 3px rgba(0,0,0,.06)`.
- Padding: airy — cards ~`20–24px`, comfortable gaps between elements.
- Buttons: primary = solid `--accent` with white text; secondary = soft (`--accent-soft`
  background) or ghost. Rounded; min `44px` tall for easy tapping.

## Layout
- Top nav bar: app name on the left, four tabs — **Nights, Live, Verdict, Chat**.
  Responsive (collapses on narrow screens).
- Each page is a centered column, max width ~`960px`, white cards on the `--bg`.
- Persistent footer on every page (small, muted):
  *"Screening tool — not a diagnosis or treatment. Consult a sleep specialist."*

## Page-specific notes
- **Nights (history):** list of cards, one per night — session number, date, and a
  severity chip. Tap a card to select it for Verdict/Chat.
- **Live:** one large line chart (SpO₂ as the accent line, HR as a muted secondary
  line), soft gridlines, current values shown large above the chart.
- **Verdict:** the band is the hero — large chip + a plain-language sentence ("Last
  night fell in the mild range") + key numbers (lowest SpO₂, events/hour, time below
  90%) + the disclaimer. If insufficient, show a calm "Not enough data for a result —
  the recording was too short," not a band.
- **Chat:** simple message thread about the selected night; plain-language answers; a
  small note that it explains the data, it does not diagnose.

## Copy tone
Plain, warm, responsible. Never "you have sleep apnea." Always an estimate + "consult a
sleep specialist." Friendly, minimal jargon.

## Accessibility
- Contrast AA minimum. Never signal a band by color alone — always include the label.
- Tap targets ≥ `44px`; visible focus rings using `--accent`.
