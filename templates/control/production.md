---
doc: production
# Machine contract read by vpg.tools.media, pacing and package.
resolution: [1920, 1080]
fps: 30
video_codec: h264
pix_fmt: yuv420p
audio_codec: aac
sample_rate: 48000
narration_wps: 2.5            # planning estimate for the runtime check
unit_pause_s: 0.4
head_tail_s: 1.0
deliverables:                 # current artifact key → path inside the package
  - {key: master, name: 01-master/master.mp4}
  - {key: captions, name: 01-master/master.srt}
  - {key: silent, name: 02-silent/silent.mp4}
  - {key: voice_stem, name: 03-stems/voice.wav}
  - {key: music_stem, name: 03-stems/music-sfx.wav}
  - {key: script, name: 04-script/script.json}
  - {key: release_summary, name: 05-release/release-summary.md}
---
# Production — <fill: film title>

> Access: written by N09 Visual System; read by N07, N10–N15, N23, N24. Locked when Wei approves N09.

## Technical pipeline
<fill: the renderer, TTS, assembly and QA chain, and the command bound to each node in vpg.project.yaml>

## Tools and models
<fill: each tool with version or pin, licence status and the node it serves (see docs/research)>

## Asset specifications
<fill: formats and sizes per asset type; provenance recorded per asset>

## Scene structure
<fill: chapters → beats → scenes; how timing follows the measured narration>

## Resolution and frame rate
<fill: 1920×1080 at 30 fps unless the brief says otherwise>

## Audio requirements
<fill: −16 LUFS ±1, true peak ≤ −1 dBTP, look-ahead ducking, no attenuation inside speech>

## Naming conventions
<fill: e.g. work/<node>/a<attempt>/…, versions v1, v2 …>

## Folder structure
<fill: controls/, sources/, pipeline/, work/, .vpg/, deliverables>

## Rendering rules
<fill: deterministic renders; never overwrite an accepted output; supersede, don't delete>

## Assembly instructions
<fill: order of operations; caption timing policy; loudness passes>

## Delivery formats
<fill: the deliverables listed in the front-matter, and who receives them>
