---
spec: {family: vpg-spec/v1, doc: design, version: 1}
tokens: DESIGN.tokens.json
ground:
  policy: muted
  roles: {ground: "{color.ground}", figure: "{color.figure}", accent: "{color.accent}", muted: "{color.muted}"}
type_scale: {display: "{type.display}", title: "{type.title}", body: "{type.body}", label: "{type.label}"}
grid: {columns: 12, margin: "{space.margin}", gutter: "{space.gutter}", safe_area: [96, 54, 1824, 1026]}
product_ui: {player_width_px: 960, min_text_px_at_player: 8, min_capture_scale: 0.6}
components: [title-card, ui-frame, data-callout, chip]
style_frames: [title, product_ui, data_callout, closing]
rules:
  - id: DS-GROUND-001
    level: MUST
    statement: The ground is the pale mist; the brand blue appears as one accent per frame.
    rationale: The brief asks for calm and precise; a saturated ground would compete with the plant data.
    source: brief (calm and precise) and the synthetic benchmark SYN-REF
    enforcement: {type: tool, metric: still.ground_colourfulness.median, gate: GT-GROUND-001}
    examples:
      do: ["Mist ground, ink text, the signal chip in brand blue"]
      dont: ["A blue-to-red gradient behind every scene"]
  - id: DS-TEXT-001
    level: MUST
    statement: No text, chip or card overlaps another at any moment, transitions included.
    rationale: Overlaps during a fade read as a glitch (R-LAY-001).
    source: R-LAY-001
    enforcement: {type: tool, metric: text.overlap_all.count, gate: GT-OVERLAP-001}
    examples:
      do: ["The plant label clears before the signal label fades up"]
      dont: ["Two labels cross-fading in one spot"]
  - id: DS-UI-001
    level: MUST
    statement: The dashboard stays readable in a 960-pixel-wide player.
    rationale: Plant managers watch it embedded in a status page, not full screen.
    source: brief (audience)
    enforcement: {type: tool, metric: still.ocr_word_px.median, gate: GT-UI-001}
    examples:
      do: ["Crop to the plant row and the outlook card"]
      dont: ["The whole dashboard shrunk into the frame"]
  - id: DS-TYPE-001
    level: SHOULD
    statement: Mona Sans in four roles across the whole teaser.
    rationale: One type system reads as one product.
    source: design.md fonts
    enforcement: {type: judge}
    examples:
      do: ["Display for the product name, label for chips"]
      dont: ["A second family for the outlook card"]
---
# Design spec

> Example family for the fictional sample. Read by N09, the look gate, N13 and N17.

## Intent
Calm and precise: a pale ground, ink type, and one blue accent that always marks the signal.

## Ground and figure
The mist ground stays put; figures are ink or white on the brand blue.

## Colour roles
Blue marks the signal and nothing else; red is reserved for a breach and never carries meaning alone.

## Type
Mona Sans: display for the product name, title for scene heads, body for captions, label for chips.

## Grid
Twelve columns inside the 96-pixel margins; nothing essential below the caption band.

## Product UI
The dashboard is cropped to the plant row and the outlook card, never shown whole.

## Components
A title card, the UI frame, a data callout and the signal chip.

## Do and don't
Do let one chip carry the colour. Don't put colour on the ground.
