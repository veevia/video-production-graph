---
spec: {family: vpg-spec/v1, doc: design, version: 1}
# Machine contract. Exact values live in DESIGN.tokens.json; these keys name roles by token reference.
tokens: DESIGN.tokens.json
ground:
  policy: "<fill: muted | brand-field | image>"
  roles: {ground: "{color.ground}", figure: "{color.figure}", accent: "{color.accent}", muted: "{color.muted}"}
type_scale: {display: "{type.display}", title: "{type.title}", body: "{type.body}", label: "{type.label}"}
grid: {columns: 12, margin: "{space.margin}", gutter: "{space.gutter}", safe_area: [96, 54, 1824, 1026]}
product_ui: {player_width_px: 960, min_text_px_at_player: 8, min_capture_scale: 0.6}
components: [title-card, ui-frame, data-callout, chip, lower-third]
style_frames: [title, product_ui, data_callout, closing]   # rendered at the look gate before any build
rules:
  - id: DS-GROUND-001
    level: MUST
    statement: The ground is quiet; saturated colour appears only as one accent per frame.
    rationale: A saturated ground behind every frame competes with the content and reads as decoration, not story.
    source: "<fill: the feedback id or benchmark it answers, e.g. F003 or benchmark REF-A>"
    enforcement: {type: tool, metric: still.ground_colourfulness.median, gate: GT-GROUND-001}
    examples:
      do: ["A charcoal or off-white ground with the accent on one chip"]
      dont: ["A full-frame saturated gradient behind every scene"]
  - id: DS-TEXT-001
    level: MUST
    statement: No text, chip or card overlaps another at any moment, transitions included.
    rationale: Overlaps during a fade read as a glitch; held-text audits miss them (R-LAY-001).
    source: "<fill: e.g. R-LAY-001 and the feedback that saw it>"
    enforcement: {type: tool, metric: text.overlap_all.count, gate: GT-OVERLAP-001}
    examples:
      do: ["The outgoing label clears before the incoming one fades up"]
      dont: ["Two labels cross-fading in the same spot"]
  - id: DS-UI-001
    level: MUST
    statement: Product UI stays readable in a 960-pixel-wide player.
    rationale: Viewers watch embedded; UI text that is legible at 1080p full screen can be unreadable there.
    source: "<fill>"
    enforcement: {type: tool, metric: still.ocr_word_px.median, gate: GT-UI-001}
    examples:
      do: ["Crop and scale the capture so its body text is at least 8 px at player width"]
      dont: ["A full-window capture shrunk into a quarter of the frame"]
  - id: DS-TYPE-001
    level: SHOULD
    statement: One type system in defined roles across titles, labels and bookends.
    rationale: Consistency across the film is what makes it look designed.
    source: "<fill>"
    enforcement: {type: judge}
    examples:
      do: ["Display for titles, body for captions, label for chips"]
      dont: ["A new family introduced for one scene"]
---
# Design spec

> Read by N09 (writes it), the look gate, N13 and N17. Tokens hold exact values; this prose holds intent; Do and
> Don't mark boundaries; lint, diff and gates enforce. Change a rule by retiring it, never by deleting it.

## Intent
<fill: one paragraph on what the frames should feel like and why>

## Ground and figure
<fill: the ground policy and how figure and accent sit on it>

## Colour roles
<fill: what each colour token is for; colour is never the only cue>

## Type
<fill: the type scale and its roles>

## Grid
<fill: columns, margins, safe area, the caption band>

## Product UI
<fill: how captures are cropped and scaled to stay legible at player width>

## Components
<fill: the components and when each is used>

## Do and don't
<fill: the boundaries, with reference frames>
