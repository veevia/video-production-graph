---
name: Glass Box
description: A local review room for AI-produced films, in Present's editorial monochrome.
colors:
  paper: "#fafafa"
  white: "#ffffff"
  ink: "#171717"
  black: "#000000"
  charcoal: "#4d4d4d"
  stone: "#666666"
  ash: "#c9c9c9"
  smoke: "#a8a8a8"
  line: "#ebebeb"
  graphite: "#8f8f8f"
typography:
  page-title:
    fontFamily: "Helvetica Neue, Arial, sans-serif"
    fontSize: "40px"
    fontWeight: 400
    lineHeight: 1
    letterSpacing: "-0.035em"
  section-heading:
    fontFamily: "Helvetica Neue, Arial, sans-serif"
    fontSize: "22px"
    fontWeight: 400
    lineHeight: 1.3
    letterSpacing: "-0.02em"
  sub-heading:
    fontFamily: "Helvetica Neue, Arial, sans-serif"
    fontSize: "17px"
    fontWeight: 400
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  body:
    fontFamily: "Helvetica Neue, Arial, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.45
  card-heading:
    fontFamily: "Helvetica Neue, Arial, sans-serif"
    fontSize: "19px"
    fontWeight: 400
    lineHeight: 1.3
    letterSpacing: "-0.015em"
  page-title-compact:
    fontFamily: "Helvetica Neue, Arial, sans-serif"
    fontSize: "30px"
    fontWeight: 400
    lineHeight: 1
    letterSpacing: "-0.035em"
  secondary:
    fontFamily: "Helvetica Neue, Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.45
  small:
    fontFamily: "Helvetica Neue, Arial, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "11px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "0.08em"
  context:
    fontFamily: "Helvetica Neue, Arial, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.45
  id:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "0"
  row-title:
    fontFamily: "Helvetica Neue, Arial, sans-serif"
    fontSize: "17px"
    fontWeight: 400
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  room-title:
    fontFamily: "Helvetica Neue, Arial, sans-serif"
    fontSize: "30px"
    fontWeight: 400
    lineHeight: 1.08
    letterSpacing: "-0.035em"
  wordmark:
    fontFamily: "Helvetica Neue, Arial, sans-serif"
    fontSize: "26px"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "0.18em"
  deck:
    fontFamily: "Helvetica Neue, Arial, sans-serif"
    fontSize: "17px"
    fontWeight: 400
    lineHeight: 1.45
  band-title:
    fontFamily: "Helvetica Neue, Arial, sans-serif"
    fontSize: "26px"
    fontWeight: 400
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  frame-label:
    fontFamily: "Helvetica Neue, Arial, sans-serif"
    fontSize: "22px"
    fontWeight: 400
    lineHeight: 1
    letterSpacing: "-0.03em"
  button:
    fontFamily: "Helvetica Neue, Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.2
  status:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "11px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "0.06em"
  level:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "11px"
    fontWeight: 500
    lineHeight: 1
    letterSpacing: "0.04em"
rounded:
  mark: "2px"
  chip: "4px"
  sheet: "6px"
  ring: "11px"
spacing:
  hair: "4px"
  tight: "8px"
  pair: "14px"
  head: "18px"
  group: "22px"
  block: "34px"
  section: "56px"
  gutter: "clamp(20px, 4vw, 64px)"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    typography: "{typography.button}"
    rounded: "{rounded.chip}"
    padding: "0 14px"
    height: "34px"
  button-primary-hover:
    backgroundColor: "{colors.black}"
  button-secondary:
    backgroundColor: "{colors.white}"
    textColor: "{colors.ink}"
    typography: "{typography.button}"
    rounded: "{rounded.chip}"
    padding: "0 14px"
    height: "34px"
  button-decide:
    backgroundColor: "{colors.white}"
    textColor: "{colors.ink}"
    typography: "{typography.button}"
    rounded: "{rounded.chip}"
    padding: "0 14px"
    height: "34px"
  button-small:
    backgroundColor: "{colors.white}"
    textColor: "{colors.ink}"
    rounded: "{rounded.chip}"
    padding: "0 10px"
    height: "28px"
  band-approve:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.button}"
    rounded: "{rounded.chip}"
    padding: "0 14px"
    height: "34px"
  masthead:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    height: "65px"
  decide-band:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    rounded: "{rounded.sheet}"
    padding: "22px 26px 24px"
  field:
    backgroundColor: "{colors.white}"
    textColor: "{colors.ink}"
    rounded: "{rounded.chip}"
    padding: "0 8px"
    height: "32px"
  sheet:
    backgroundColor: "{colors.white}"
    rounded: "{rounded.sheet}"
    padding: "18px 22px"
  decision-card:
    backgroundColor: "{colors.white}"
    rounded: "{rounded.sheet}"
    padding: "14px 16px 16px"
  frame:
    backgroundColor: "{colors.white}"
    rounded: "{rounded.sheet}"
    width: "196px"
  frame-rail:
    backgroundColor: "{colors.white}"
    rounded: "{rounded.sheet}"
    width: "120px"
  pipeline-node:
    backgroundColor: "{colors.white}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sheet}"
    padding: "10px 12px"
    width: "196px"
    height: "66px"
  node-drawer:
    backgroundColor: "{colors.white}"
    padding: "22px 26px 42px"
    width: "760px"
  room-tab:
    textColor: "{colors.stone}"
    typography: "{typography.label}"
    padding: "8px 6px 7px"
  room-tab-selected:
    textColor: "{colors.ink}"
  status-mark:
    textColor: "{colors.charcoal}"
    typography: "{typography.status}"
    size: "12px"
  status-mark-strong:
    textColor: "{colors.ink}"
  status-mark-quiet:
    textColor: "{colors.stone}"
  level-p1:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    typography: "{typography.level}"
    rounded: "{rounded.chip}"
    padding: "0 5px"
    height: "18px"
  level-p2:
    backgroundColor: "{colors.white}"
    textColor: "{colors.ink}"
    typography: "{typography.level}"
    rounded: "{rounded.chip}"
    padding: "0 5px"
    height: "18px"
  level-p3:
    backgroundColor: "{colors.white}"
    textColor: "{colors.ink}"
    typography: "{typography.level}"
    rounded: "{rounded.chip}"
    padding: "0 5px"
    height: "18px"
  level-p4:
    backgroundColor: "{colors.white}"
    textColor: "{colors.charcoal}"
    typography: "{typography.level}"
    rounded: "{rounded.chip}"
    padding: "0 5px"
    height: "18px"
  level-unrated:
    backgroundColor: "{colors.white}"
    textColor: "{colors.stone}"
    typography: "{typography.level}"
    rounded: "{rounded.chip}"
    padding: "0 5px"
    height: "18px"
---

# Design System: Glass Box

## Overview

**Creative North Star: "Present's editorial monochrome, in operate mode"**

Glass Box is where the owner of AI-produced films decides, watches and traces how each version was made. It borrows the owner's Present reference (an editorial, monochrome presentation system) and applies it in operate mode: familiar navigation and controls, a fixed type scale, and the brand in precise details rather than spectacle. The frames of the films are the only colour on screen; everything the app draws is ink on paper.

Three ideas carry the system. Decisions sit beside their film and evidence. Films read as a contact sheet: rows of frames, newest first, each with its status underneath. The pipeline is drawn, not tabulated: a hairline traced through the workflow up to where the engine run stands.

**Key Characteristics:**

* Ten neutrals from paper to ink; the films' frames carry all the colour.
* Status as a drawn glyph, a word, a weight and a line style, never colour; Needs you means only the owner.
* Hairlines, 6 px and 4 px corners and no shadows; one ink surface, the Decide band.
* 11 to 12 px mono scaffolding under 13 to 40 px regular-weight sans; bold only in the wordmark.
* Motion only for state and transfer: 150 to 250 ms transitions and the pipeline's one-time trace.

### Adaptations from Present

Present is a scrolling presentation; Glass Box is a tool used between production rounds. Each departure below is deliberate, with its reason.

* **Masthead scale.** Present's masthead is 76 px plus its 8 px rule, translucent with a 20 px backdrop blur, turns dark over the dark band, carries numbered section links, and scales its wordmark from 26 to 40 px. Glass Box keeps the 8 px black rule, the centred bold wordmark and the mono navigation, but in a 65 px plain-paper masthead with a fixed 26 px wordmark that never changes theme. Reason: the masthead is chrome on every view, so the first viewport goes to the decisions and the frames; a solid ground keeps the navigation crisp over scrolling frames; there are no sections to number.
* **Scan line instead of reading progress.** Present's second rule tracks reading progress. Glass Box's 1 px rule at the masthead's foot appears only while the library scan runs: dashed ink while it walks the folders, a solid ink rule growing from the left while it reads files. Reason: a working view has no reading progress to report, and the long background scan does.
* **Fixed title scale.** Present's titles are fluid, clamp(40px, 4.6vw, 72px) at -0.045em with a 0.98 line-height. Glass Box fixes page titles at Present's floor, 40 px (30 px on phones) at -0.035em, and the room title at 30 px. Reason: operate mode keeps one scale on every screen size, and the decision count or a film's full name must leave room for the band and the first frames.
* **No kickers or section numbers.** Present opens each section with a small numbered kicker above the title. Glass Box puts nothing above a heading. Reason: eyebrows are below the craft floor; context sits beside or below the heading.
* **Installed type.** Present embeds Liberation Sans under the alias Maison Sans. Glass Box uses the installed Helvetica Neue (Present's own first fallback), then Arial, and the platform's ui-monospace. Reason: the app is local only and downloads no fonts.
* **Monochrome status vocabulary.** Present separates kinds by shape and stroke pattern before tone, and marks the active card with an ink border rather than a colour. Glass Box extends that into a full status vocabulary: a 12 px drawn glyph, a word, a weight and a line style, with no status colour at all (not even one red for P1 or failure), and status dots that never pulse. Reason: the frames are the only colour, and status has to survive greyscale, small sizes and colour blindness.
* **A pipeline that reflows.** Present's runtime diagram is a fixed 1472 px canvas scaled with CSS zoom, illegible on a phone (its 10 px labels shrink to about 2.4 px). Glass Box lays real node cards out in phase lanes and draws the connectors over them from their measured positions; at 760 px and below the lanes stack and one vertical line runs down a 26 px track. Lane names still stand vertically, as in Present, but in 11 px stone mono instead of 9.5 px graphite. Reason: every node is a control that must stay legible and reachable at 390 px, and graphite small text is under 4.5:1.
* **One signal, no halo.** Present runs several phase-offset 7 px signals, each with a 4 px, 9 % ink halo, on every path. Glass Box sends one 7 px ink dot, without a halo, along the line into the node that is running, only while the selected version is in progress; the trace draws once in 0.9 s instead of 2.4 s. Reason: motion reports a transfer that is actually happening, and a tool reopens the view too often for a long draw.
* **No reveals, orbits or pulses.** Present's scroll reveals, revolving loops, pulses and graph bloom are absent. Reason: content must be there at once when the owner comes back; motion is kept for state and transfer.
* **The List table and the versions Table.** Present has no tables. Glass Box keeps hairline tables where scanning by attribute matters: the Films List of every video file (file, role, film and version, duration, size, date) and the versions Table in the review room (status, open findings by level, checks, notes and source, with A and B ticks for Compare). They use Present's materials: mono 11 px uppercase stone headers over an ash rule, 14 px rows on line rules, mono tabular numbers. Reason: the owner asked to keep every capability, the file lists and the checks table included, and a contact sheet cannot compare sizes, dates or counts side by side.
* **The node drawer.** Present has no drawers or dialogs. Glass Box opens a node's evidence in a full-height white sheet from the right over an ink scrim, and asks for confirmation in small white dialogs. Reason: the evidence stays one step away without leaving the pipeline, and the owner asked to keep the node drawer with all its content.
* **The ink climax becomes the Decide band.** Present's single dark moment is a full-bleed ink section. Glass Box's one ink surface is the Decide band, a 6 px sheet inside the page. Reason: the darkest moment marks the one place where a decision is recorded.
* **No capsule.** Present reserves one ink capsule for a diagram's legend. Glass Box has no capsules or pills; the pipeline's key is a plain line of drawn swatches. Reason: the ring at the waiting gate is the one rounded emphasis, and nothing competes with it.

## Colors

The palette is Present's, strictly monochrome. Contrast pairs are WCAG AA for their uses.

### Neutral

* Paper `#fafafa`: the page ground and the masthead, and text on ink; the mask behind the ring's label, the ground of the rail's owner tab and of selected table rows.
* White `#ffffff`: sheets, cards, frames, fields, buttons, menus, the drawer and dialogs; slightly brighter than the page.
* Ink `#171717`: text, primary buttons, selected and active states, the Decide band, the waiting ring and the run trace (17.2:1 on paper).
* Black `#000000`: the 8 px rule on top of the masthead, the ground behind the player and painted stills, and the hover of the ink primary button. Never text.
* Charcoal `#4d4d4d`: secondary text, Passed and Outside the engine marks, finding marks on the timeline, the dashed line of builds outside the engine and the dotted line of runs the engine never recorded, the rule of the conflict box inside the band (8.1:1 on paper).
* Stone `#666666`: labels, context lines, the quiet marks (Not run, Not reviewed, Partly reviewed, Superseded, Unknown) and the border of every field (5.5:1 on paper, 5.7:1 on white; the smallest text colour allowed).
* Ash `#c9c9c9`: quiet strokes, button and chip outlines, the pipeline frame, the not-reached line, table header rules, link underlines, the thread rule and scrollbar thumbs; secondary text on ink, such as the effect line (10.8:1 on ink).
* Smoke `#a8a8a8`: dashed outlines (unrated levels, frames without a master, provisional badges) and the scrollbar thumb on hover.
* Line `#ebebeb`: hairlines, sheet and card borders, and the outline of a node that has not run.
* Graphite `#8f8f8f`: non-text marks only, the caption cues and unrated finding marks on the timeline (3.1:1 on paper). Never text.

### Named Rules

**No status colour.** Status is a drawn glyph, a word, a weight and a line style. Colour never carries meaning, and no accent blue exists.

**Frames are the colour.** Posters, stills and players are the only coloured surfaces; nothing decorative competes with them.

## Typography

One sans for content (Helvetica Neue, falling back to Arial; nothing is downloaded) and one mono for scaffolding (the platform's ui-monospace, then SF Mono, Menlo and Consolas). The hierarchy comes from the contrast between 11 to 12 px mono scaffolding and 13 to 40 px sans content, not from bold weights.

### Hierarchy

* Page title: 40 px, weight 400, uppercase, line-height 1, tracking -0.035em, balanced wrapping, at most 28 characters wide (the compact 30 px step at 760 px and below).
* Room title: 30 px uppercase, line-height 1.08. One line when it fits, else two balanced lines; only when two lines are not enough does it step down 2 px at a time, never below 18 px. It is never cut.
* Decision title in the Decide band: 26 px, tracking -0.02em, line-height 1.2, at most 44 characters wide.
* Section heading: 22 px, weight 400, tracking -0.02em, line-height 1.3.
* Card heading: 19 px, tracking -0.015em, balanced, for the gate or release on a decision card.
* Row title and sub-heading: 17 px, weight 400, tracking -0.01em, so a section reads clearly above its rows.
* Deck: one sentence under a page title, 17 px / 1.45 in charcoal. The ask in the Decide band and the Activity summary are 17 px as well.
* Body: 15 px / 1.45; secondary text 14 px charcoal; small notes (why lines, rule text, the pipeline key, buttons in tight rows) 13 px.
* Buttons: sans 14 px with a 1.2 line-height; small buttons 13 px.
* Labels and navigation: mono 11 px uppercase, tracking 0.08em, stone (ink when current).
* Status words: mono 11 px uppercase, tracking 0.06em. A status phrase longer than 28 characters ("Decided by you · engine waits", "Passed (not recorded by the engine run)") keeps its glyph but reads in sentence-case sans 13 px.
* Level chips: mono 11 px at weight 500, tracking 0.04em.
* Context: sans 13 px, stone, for the facts beside a heading or under a row; ids, version labels, times and counts in mono 12 px with tabular numerals. Monospace is for navigation, labels, counts, times, ids and status words only.
* Typographic frames: the version label at 22 px (15 px in the rail), tracking -0.03em, over its duration and date in mono 11 px.
* Prose (reasons, decks, row sentences, verdict sentences, notes and threads, Activity included) is at most 70 characters wide. Two runs of short facts may go wider: the room's status line (120 characters, at 14 px below 1280 px wide) and the Inbox Producers line (90).
* Wordmark: 26 px, weight 700, tracking 0.18em with a matching indent, uppercase. The only bold display type.

### Named Rules

**No eyebrows.** Nothing sits above a heading: no kicker, no section number. Context goes beside or below it, in 13 px stone.

## Layout

* Container: at most 1600 px, centred, with gutters of clamp(20px, 4vw, 64px). Pages start 34 px under the masthead (26 px on phones, 20 px in the review room) and end 72 px above the footer.
* Rhythm: 8 px inside a group, 14 px between a label and its content, 18 px under a section heading, 22 to 34 px between groups, 56 px between sections; more space above a heading than below it.
* Masthead: sticky plain paper, an 8 px black rule on top and a 1 px line below, 65 px tall, in three columns (navigation, centred wordmark, search and Help) from 1024 px. Between 761 and 1023 px it has two rows (the wordmark with search and Help, then the navigation); at 760 px and below the search is a 32 px icon button that opens the field full width as a third row. The Inbox link carries the count of waiting decisions in mono (ink, stone at zero).
* Inbox: the count as the title and one deck sentence; the decision band (auto-fill cards at least 340 px wide, 22 px apart; a gate the owner has decided is never a card); the Producers line 20 px under the band; Recent films 24 px below that (the first four rows of the contact sheet, compact, without the status sentence, so the first row's frames come up inside the first viewport at 1440x900 even with three decision cards); then the Producers section; then For your information, "not counted as decisions", with its collapsed groups (Waiting for the producer, In progress, Not reviewed, Superseded).
* Films: All and Needs you (a segmented control with counts) and the search stay in view; everything else sits in one View menu (show only Jobs, Library or Archive; sort by newest, name or size; the Sheet or List layout; the library scan status) in a white 6 px panel under its button. The sheet has one row per film, twelve at a time, each with a sideways strip of 196 px frames (156 px on phones); the List layout is a table of every video file, sixty at a time. The address keeps the filter, search, layout and sort.
* Review room: the title, one sentence about the selected version, the rail of 120x68 frames (112 px wide on phones) with its count pager in a 76 px column at the right end (52 px on phones), or the versions Table instead; then the tab bar (Watch, Decide, Compare, Pipeline, Files) with Decide and Table at its right end, and a 22 px gap to the panel. Watch is two columns (the player, the timeline and the note composer under it; then the one verdict line and the first three findings), about 1.55 to 1 from 1280 px and 1.3 to 1 below. Decide is two columns from the top (the ink band, the gate's evidence, what changed and the open findings on the left; the compact player beside the band, then the verdicts, on the right). Everything stacks below 1024 px.
* Pipeline: a 6 px frame with a 1 px ash rule around the key, the one-line run summary and the lanes; phase lanes whose names stand vertically in a 26 px column from 1024 px (horizontal, above the nodes, below that); nodes of 196 px in rows with 26 px between rows and 36 px between nodes, so the connectors stay visible between them. At 760 px and below the lanes stack, the nodes take the full width after a 26 px track, and one vertical line runs down that track.
* Activity: the title, the pickup summary at 17 px and a deck; a segmented control (All, Waiting for the producer, Picked up, Addressed, with counts); the filters (folded behind Filter until there is a note to filter); Copy for chat and Download .md; then every note, newest first. A link to one note opens Activity on its thread, outlined in ink.

## Elevation & Depth

Nothing casts a shadow. Depth comes from paper against white, hairlines, and the ink frame or ring on the selected or waiting item. The masthead is plain paper over a 1 px line; nothing is blurred. Overlays are flat sheets over an ink scrim: the node drawer over 28 % ink, dialogs over 35 %. Selection thickens a stroke instead of lifting a surface: a 1 px inset ink ring inside a 1 px ink border marks the current choice (the decision picker, the chosen kind in the note composer, a checked preference), and a pressed tag gets a 1 px inset ink underline. One drift is on record: the Films View panel casts a soft drop shadow (0 10px 28px, 8 % black); it is a defect to remove, not a pattern to copy.

## Shapes

Sheets, cards, frames, the player, menus, panels and dialogs use 6 px corners; chips, buttons, fields, level marks, segmented controls and the conflict box use 4 px; timeline marks and focus outlines use 2 px (4 px inside the room tabs). The node drawer is square, flush with the right edge of the window. No capsules and no pills: the one ring, around the waiting gate, is a rounded rectangle drawn 6 px outside the node with 11 px corners, so it follows the node's own 6 px corners. Small round forms are glyphs only: the Needs you dot, the 6 px dot on the Decide tab, the owner's note marks on the timeline. Strokes are 1 px for infrastructure (hairlines, outlines and connectors; the dotted not-recorded line is 1.25 px), and 1.5 px for glyphs, icons, the P2 chip, the selected frame's outline, the ring and the run trace.

## Components

* Status mark: a 12 px drawn glyph (1.5 px strokes) and a mono word. Needs you (filled ink dot), Passed (check, charcoal), Rework (cross, ink, weight 600), In progress (dashed ring, ink), Not run and Not reviewed (dashed square, stone), Partly reviewed (dashed square with a bar, stone), Superseded (dash and the word, stone; version labels are struck where superseded versions are listed), Unknown (question mark, stone), Outside the engine (dashed arrow, charcoal), Passed but not recorded by the engine run (check and those words). The owner's own decisions have their own glyph: an ink ring with a check (You approved) or a cross (You requested changes) inside it. A gate he has decided while the engine run still waits reads "Decided by you · engine waits" with that ring, in the pipeline, the gates list, the drawer and the Decide band, and it is never counted as a decision. Needs you means only the owner: its dot appears only where he has not decided. (The engine run card in Files still draws that dot beside "Waiting at" after he has decided; that is drift, not the rule.)
* Owner mark: the owner's verdict on a version (his latest Approve or Request changes on the version or its release), wherever the version appears: under its status mark on the contact sheet, in the versions Table and the For your information rows, and as a clause of the room sentence ("You requested changes Oct 2, 4:05 PM; the producer picked it up for v13."). It is the ring glyph and "You approved" or "You requested changes", then where the producer is: "· waiting for the producer", "· picked up for v13", "· addressed in v13" or "· reopened, waiting for the producer", in sans 12 px ink. On the rail it is a compact paper tab over the frame's foot (1 px ink outline, 3 px corners, 11 px words: "Changes · v13", "Approved") so the rail keeps its height; the full words are in the frame's accessible name.
* Level chip: mono 11 px at weight 500 in a 4 px box, 18 px tall and at least 26 px wide. P1 ink fill with paper text, P2 a 1.5 px ink outline, P3 a 1 px charcoal outline with ink text, P4 a 1 px ash outline with charcoal text, Unrated a 1 px dashed smoke outline with stone text.
* Polarity: a glyph and the word: better (up arrow, charcoal), worse (down arrow, ink at 600), no change (equals, stone), not comparable (question mark, stone). A metric measured on a different basis reads Uncertain beside a "basis differs" chip (a dashed ink outline with a warning glyph), and its What changed cell takes a dashed ink rule on top.
* Buttons: 34 px tall, sans 14 px, 4 px corners, 14 px side padding; small buttons 28 px, 13 px, 10 px padding. Primary (ink fill, black on hover) once per view at most, for the decisive action (Approve, Record preference, Save on a note, the confirming button of a dialog; inside the ink band Approve is a paper fill with ink text and Request changes a paper outline); secondary (white with an ash outline, ink on hover); Decide (white with an ink outline and the Needs you dot; the first Inbox card's Decide is that page's ink primary; in the room's tab bar it is 28 px tall); quiet (underlined text). Disabled buttons fade to 45 % (50 % inside the band) and say why nearby.
* Frame: wherever a version frame appears (rail, contact sheet, Inbox cards for versions) the still comes first and the poster is the fallback; Watch's player keeps the poster. A 16:9 white sheet with a line border holding the poster, a still, or a typographic frame (the version label over its duration and date) until a still is drawn. Once a still is painted the frame turns black behind it, the typographic face hides, and the still fades in. The selected frame has an ink border and a 1.5 px ink outline offset by 3 px; frames without a master are dashed smoke on paper ("no master yet"). Strips fade an edge, through a mask (48 px on the contact sheet, 56 px in the rail), only where frames are hidden beyond it, and the rail's pager names how many ("← 4 newer", "7 older →") without covering a frame.
* One still per version: each version gets exactly one still, a frame a third of the way into its master (past the title card most versions share), captured once by the browser at 240 px wide, kept in its local cache and reused wherever that version appears. Only frames in view ask for one, two at a time; frames scrolled away keep their typographic face.
* Decision card: a white 6 px card with a line border; the frame on top (for a gate of the run, a typographic frame: its display name over its number), then the ask as the heading (19 px), the film under it in sans 14 px stone, one factual sentence in 14 px charcoal (how long the engine run has waited, or how the version did: reviews, open findings by level and its runtime against the brief) and Decide.
* Runtime against the brief: the brief's runtime is read as a window (a cap is the upper bound; a target alone allows 10 % either way), and a version outside it says so in words and numbers, never with a badge or a colour. The room sentence adds "It runs 01:12.4 against the brief's 60 s cap; the script targets 70 s."; the verdict line ends with the same clause; a release card on the Inbox always gives the runtime against the cap, and the card of the gate that set the brief adds "this gate caps the film at 60 s, and the latest version, v12, runs 01:12.4"; the Decide band lists it as a conflict and counts how many versions with a master fit.
* Decide band: the one dark surface, ink with paper text, 6 px corners, padding 22, 26 and 24 px: the decision as a 26 px title, the ask at 17 px, one factual sentence, the conflicts in a 4 px charcoal-ruled box with a warning glyph and the word, the effect line in ash (future tense until saved: "Approving records your decision for the producer."), then Approve and Request changes; the reason field ("What should change? (needed)") opens inside it only after Request changes. Focus outlines turn paper inside the band. Once the owner has decided, the band shows his mark (the ring glyph with "Decided by you · engine waits", "You approved" or "You requested changes") with the time, his reason and the producer's thread, and offers Record a new decision only where the server takes one.
* Verdict line: one hairline-ruled disclosure with the mark glyph and "4 of 4 reviewers pass · 12 checks pass · 3 open findings, all P4" (plus the runtime clause when the version runs outside the brief); it opens to the reviewer lines.
* Finding row: level chip, a short title (wrapping, at most about 80 characters) with the node in words below, your answer's state when you answered, and a time button; selecting it opens a white sheet with the evidence, Agree, Disagree, Note and Details, and your answers with their threads. Watch shows the first three, by severity then time.
* Thread: under the owner's text, a 1 px ash rule at the left of the producer's answers, at most 70 characters wide, one line per step with a 12 px glyph: Picked up (ack glyph, with the producer's plan), Addressed in vN (check, the version linked), Reopened by you (arrow); Reopen opens an inline field. A state chip in mono names where the producer is: Waiting for the producer (clock, stone), Reopened (arrow, stone), Picked up (ack glyph, ink), Addressed in v13 (check, ink).
* Producers on the Inbox: the Producers line under the decision band sums up every job in one sentence after the mono label ("2 notes waiting for the producer, the oldest for 3 h · 1 picked up · 1 addressed.") and ends with See each note, which jumps to the Producers section. There, under each job's film, each note is one line: its state chip, the target in words (15 px, underlined) and, on its own line in 14 px charcoal, the owner's note (its kind and first sentence, quoted) while it waits, or the producer's first sentence, quoted, once picked up or addressed. Each line links to its thread in Activity. Copy for the producer appears for a job once a note has waited 15 minutes.
* Timeline: lanes of marks over a single slider, a 1 px ink playhead and a mono time scale; lane names in mono 11 px with counts ("My notes: 2 timed of 3"). Finding marks are 8x12 px white boxes with 2 px corners: P1 ink fill, P2 a 1.5 px ink outline, P3 a charcoal outline, P4 a shorter charcoal outline (3:1 against paper or better), unrated dashed graphite. The owner's notes are 9 px ink rings, key frames 1 px charcoal ticks, caption cues 4 px graphite bars. Findings and My notes show by default; Key frames and Captions open on request.
* Drawn pipeline: white node cards (196 px, 6 px corners) on paper lanes, with outlines and connectors that mean one thing each: ink solid (1 px outline, 1.5 px line) for the engine run, charcoal dashed for built outside the engine, charcoal dotted for passed but not recorded by the engine run, and a line-grey 1 px outline with stone words (ash line) for not run. Connectors run behind the cards and turn in the gutter above the next node. A 1.5 px ink ring circles the waiting gate with a paper-backed mono label on its top edge, "Waits here", or "Engine waits here" once the owner has decided; the key draws the same outlines and the same ring and adds the decided mark when it applies. One line above the lanes sums up the run ("The engine run waits at the brief gate (N03) since Oct 2; 2 nodes ran in it; 5 versions built outside it. The full run card is in Files.").
* Node drawer: a full-height white sheet from the right, up to 760 px wide, with a 1 px line at its left edge, over a 28 % ink scrim; it slides in 24 px with a fade. Esc closes it.
* Dialogs: white 6 px sheets with a line border, 560 px wide (780 px for Help), padding 22 and 26 px, over a 35 % ink scrim; the confirming button is the dialog's ink primary.
* Tabs: mono 11 px uppercase, stone, with a 1 px ink underline on the selected tab; a 6 px ink dot marks Decide only while a decision waits. The tab bar scrolls sideways, so its 2 px ink focus ring is drawn inside the tab (offset -2 px, 4 px corners) and is never clipped.
* Segmented controls: mono 11 px uppercase buttons in a 4 px ash box with line dividers; the pressed one is ink with paper text, and counts follow the words.
* Form fields: inputs, selects, search fields and text areas are white with a stone border (5.7:1 on white, above the 3:1 non-text minimum), 4 px corners and 32 px height (text areas at least 72 px); focus is the 2 px ink outline, and selects and text areas darken to charcoal on hover. Checkboxes and radios take the ink accent. (The masthead search still lightens to ash on hover; that is drift, not the rule.)
* Menus: the search list and the More menu are white 6 px sheets with a 1 px ink border; the highlighted search option is ink with paper text, and More items take a paper ground on hover and focus.
* Browser surfaces: selected text is paper on ink, the caret and form accents are ink, scrollbars are 10 px ash thumbs (smoke on hover) on a transparent track, focus is a 2 px ink outline offset by 2 px, and links have a 1 px ash underline 3 px below the text that turns ink on hover.
* Motion: colour, border and background transitions take 150 ms, the disclosure chevron and the drawer's entrance 200 ms, a still's fade-in 200 ms and the scan line 250 ms, all on cubic-bezier(.2, .7, .15, 1). The one authored moment is the pipeline: the run trace draws once in 0.9 s, after 0.1 s, when the Pipeline tab opens (resizing moves the line without replaying it), and a 7 px ink dot travels the line into the running node every 2.4 s, at an even speed, only while the selected version is in progress. Reduced motion turns every animation and transition off, draws the trace static and hides the dot.

### Named Rules

**Needs you means you.** The filled ink dot and the words Needs you mark only what waits for the owner. What he has decided reads as his, with the ring glyph, even while the engine run still waits.

**One still per version.** A version shows the same single still everywhere, taken a third of the way into its own master; a still never stands in for another version.

## Do's and Don'ts

* Do let the frames carry the colour; keep everything the app draws monochrome.
* Do pair every status with a glyph and a word; never rely on colour, and never on a glyph alone.
* Do keep Needs you for the owner alone, and give his own decisions, and the engine's wait after them, the ring glyph and their own words.
* Do keep one primary action per view and put decisions only in the Decide band.
* Do say what will happen before acting and what happened after it, in plain words ("Approving records your decision for the producer." then "Your approval was recorded for the producer").
* Do state a version's runtime against the brief in words and numbers wherever it runs outside the brief.
* Do keep field borders at 3:1 or more against their ground (stone on white).
* Do keep tap targets at least 24 px tall at 390 px wide, including disclosures, checkboxes and radios; links in running text take 5 px of padding above and below.
* Do keep prose and notes at most 70 characters wide, Activity included.
* Don't add an eyebrow, kicker or section number above a heading.
* Don't add shadows, gradients, pills, side-tab borders (a thick bar down one side of a card) or a second accent colour. The strips' edge fade is the one gradient, a mask that says frames are hidden there; the 1 px ash rule beside a thread is a hairline indent, not a side tab.
* Don't animate for decoration: no entrance choreography, no hover lifts; motion explains a transfer or a state change.
* Don't use mono for prose; it is for navigation, labels, ids, counts, times, status words and code.
* Don't use graphite, or any grey lighter than stone, for text on paper or white.
