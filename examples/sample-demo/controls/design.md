---
doc: design
palette: {ink: "#1A1F26", paper: "#FFFFFF", brand: "#0B5CAD", mist: "#EEF1F4", signal: "#E03E2D", slate: "#5A6572"}
text_pairs:
  - {fg: paper, bg: brand, use: large}
  - {fg: paper, bg: brand, use: body}
  - {fg: ink, bg: paper, use: body}
  - {fg: ink, bg: mist, use: body}
  - {fg: slate, bg: paper, use: body}
fonts: ["Mona Sans"]
approved_assets:
  - {path: assets/brand/northwind-mark.png, sha256: ca25370a7e02fdd419107bdbb157a204ee1086602051acd18b4db43083961f50, role: logo}
prohibited: ["font:inter", "font:arial", "color:#ffbf00"]
---
# Design — Northwind Water Monitor teaser (fictional)

> Access: written by N09; locked when approved.

## Visual language
Minimal and precise: bold caps on the Northwind blue field, white type, one recurring signal line.

## Layout system
1920×1080; title-safe x 96–1824, y 54–1026; nothing essential below y 900 while captioned.

## Typography
Mona Sans throughout. Titles use bold caps at 72–96 px; labels use regular or semibold at 30–40 px (never below 22 px).

## Color
Tokens: ink, paper, brand, mist, signal, slate. Text uses only the approved pairs. The signal red always comes with a label, so colour is never the only cue.

## Composition
One focal point per frame; the narrated element is the largest or brightest thing on screen.

## Illustration principles
Diagrams are drawn from the product notes. They explain the signal against the expected range and show nothing decorative.

## Motion behavior
A slow push-in within each scene and a 0.4 s crossfade at each seam; the signal line carries across seams.

## Accessibility
WCAG 2.2 AA contrast, no flashing, captions in a sidecar.

## Legibility
≥ 22 px at 1080p; dwell ≥ max(1 s, 0.24 s per word); ≤ 8 text elements per scene.

## Brand constraints
The Northwind mark is used as supplied, scaled uniformly, with clear space equal to a quarter of its height.

## Approved and prohibited patterns
Approved: bold-caps title on the blue field, labelled signal, ring on the real UI card. Prohibited: Inter or Arial, the amber #FFBF00, gradients on white cards.
