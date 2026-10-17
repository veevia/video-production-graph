---
doc: production
resolution: [1920, 1080]
fps: 30
video_codec: h264
pix_fmt: yuv420p
audio_codec: aac
sample_rate: 48000
narration_wps: 2.8
unit_pause_s: 0.45
head_tail_s: 1.5
deliverables:
  - {key: master, name: 01-master/master.mp4}
  - {key: captions, name: 01-master/master.srt}
  - {key: silent, name: 02-silent/silent.mp4}
  - {key: voice_stem, name: 03-stems/voice.wav}
  - {key: music_stem, name: 03-stems/music.wav}
  - {key: script, name: 04-script/script.json}
  - {key: release_summary, name: 05-release/release-summary.md}
---
# Production — Northwind Water Monitor teaser (fictional)

> Access: written by N09; locked when approved.

## Technical pipeline
Commands in vpg.project.yaml: `pipeline/assets.py` (N11), `pipeline/voice.py` (N12, macOS `say`), `pipeline/scenes.py` (N13, PIL), `pipeline/assemble.py` (N14, ffmpeg).

## Tools and models
PIL for plates, macOS `say` for narration (example only; real productions use an identity-locked TTS with ASR QC), ffmpeg for assembly and loudness.

## Asset specifications
PNG, 1920×1080 for plates; the logo as supplied.

## Scene structure
Three beats, one per narration unit; scene boundaries follow the measured narration.

## Resolution and frame rate
1920×1080 at 30 fps.

## Audio requirements
−16 LUFS ±1 integrated, true peak ≤ −1 dBTP, a quiet music bed ducked under speech.

## Naming conventions
work/<node>/a<attempt>/…; versions v1, v2, ….

## Folder structure
controls/, sources/, assets/, pipeline/, fixtures/ (dry run only), work/, .vpg/.

## Rendering rules
Deterministic renders; outputs are superseded, never deleted.

## Assembly instructions
Scene crossfades of 0.4 s; captions one cue per unit, speech-locked; two-pass loudness normalisation.

## Delivery formats
The front-matter deliverables, zipped with a per-file manifest.
