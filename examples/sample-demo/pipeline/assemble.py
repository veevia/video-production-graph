"""N14 command: assemble the master from scenes, voice and a ducked music bed; normalise loudness in two passes; write
speech-locked captions, stems and a silent master.

`caption_offset_s` in the agent's assembly-params.json shifts every cue. The dry run's first attempt uses +0.35 s, a
deliberate caption-lag defect that N18 must catch and N22 must route back here.

Both two-input audio filters get fixed 1024-sample frames (asetnsamples). Otherwise ffmpeg's frame interleaving varies
from run to run, the mix differs by a few bits, and identical inputs no longer give an identical master (which breaks
memo-skips downstream).
"""
import json
from pathlib import Path

from _ctx import FFMPEG, done, duration, load, read_json, sh

ctx = load()
wd = Path(ctx["workdir"])
params = read_json(ctx["agent_outputs"]["assembly_params"]) if ctx["agent_outputs"].get("assembly_params") else {}
off, T = float(params.get("caption_offset_s", 0.0)), float(params.get("crossfade_s", 0.4))
scenes_path = Path(ctx["inputs"]["scenes"][-1])
scenes = read_json(scenes_path)
frames = {Path(p).name: p for p in ctx["inputs"]["scene_frames"]}
timings = read_json(ctx["inputs"]["timings"][-1])
voice = ctx["inputs"]["voice"][-1]
total = scenes["runtime_s"]

segs = []
for i, s in enumerate(scenes["scenes"]):
    dur = s["end"] - s["start"] + (T if i < len(scenes["scenes"]) - 1 else 0)
    n = int(round(dur * 30))
    seg = wd / f"seg{i}.mp4"
    sh([FFMPEG, "-v", "error", "-y", "-loop", "1", "-framerate", "30", "-i", frames[s["image"]], "-frames:v", str(n),
        "-vf", f"zoompan=z='min(1+0.0006*on,1.04)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1920x1080:fps=30,format=yuv420p",
        "-c:v", "libx264", "-crf", "16", "-preset", "fast", str(seg)])
    segs.append(seg)
inputs, chain, last = [], [], "0:v"
for p in segs:
    inputs += ["-i", str(p)]
for i in range(1, len(segs)):
    off_t = scenes["scenes"][i]["start"]
    out = f"x{i}"
    chain.append(f"[{last}][{i}:v]xfade=transition=fade:duration={T}:offset={off_t:.3f}[{out}]")
    last = out
video = wd / "video.mp4"
sh([FFMPEG, "-v", "error", "-y", *inputs, "-filter_complex", ";".join(chain) or "null", "-map", f"[{last}]" if chain else "0:v",
    "-t", f"{total:.3f}", "-c:v", "libx264", "-crf", "16", "-preset", "fast", "-pix_fmt", "yuv420p", "-r", "30", str(video)])

FRAMES = "asetnsamples=n=1024:p=0"   # fixed frame size → bit-identical mixes for identical inputs
music = wd / "music_stem.wav"
expr = "0.10*sin(2*PI*110*t)*(0.65+0.35*sin(2*PI*0.12*t))+0.06*sin(2*PI*164.81*t)+0.05*sin(2*PI*220*t)"
sh([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"aevalsrc={expr}:s=48000:d={total:.3f}", "-i", voice, "-filter_complex",
    f"[0:a]volume={params.get('music_gain', 0.5)},afade=t=in:d=1,afade=t=out:st={total - 1.5:.3f}:d=1.5,{FRAMES}[m];"
    f"[1:a]aresample=48000,{FRAMES}[v];[m][v]sidechaincompress=threshold=0.02:ratio=8:attack=150:release=700[ducked]",
    "-map", "[ducked]", "-ac", "2", str(music)])
premix = wd / "premix.wav"
sh([FFMPEG, "-v", "error", "-y", "-i", voice, "-i", str(music), "-filter_complex",
    f"[0:a]aresample=48000,pan=stereo|c0=c0|c1=c0,{FRAMES}[v];[1:a]{FRAMES}[m];[v][m]amix=inputs=2:normalize=0[a]", "-map", "[a]", "-t", f"{total:.3f}", str(premix)])
measure = sh([FFMPEG, "-hide_banner", "-i", str(premix), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"])
m = json.loads(measure[measure.rindex("{"):measure.rindex("}") + 1])
mix = wd / "mix.wav"
sh([FFMPEG, "-v", "error", "-y", "-i", str(premix), "-af",
    f"loudnorm=I=-16:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
    f"measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true", "-ar", "48000", str(mix)])
master = wd / "master.mp4"
sh([FFMPEG, "-v", "error", "-y", "-i", str(video), "-i", str(mix), "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
    "-ar", "48000", "-t", f"{total:.3f}", "-movflags", "+faststart", str(master)])
silent = wd / "silent.mp4"
sh([FFMPEG, "-v", "error", "-y", "-i", str(video), "-c:v", "copy", "-an", str(silent)])
voice_stem = wd / "voice_stem.wav"
sh([FFMPEG, "-v", "error", "-y", "-i", voice, "-ar", "48000", str(voice_stem)])


def stamp(t):
    ms = int(round(max(0.0, t) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


units = timings["units"]
cues = []
for i, u in enumerate(units):
    start = max(0.0, u["start"] - 0.2) + off
    end = u["end"] + 0.25 + off
    if i + 1 < len(units):
        end = min(end, units[i + 1]["start"] - 0.2 + off - 0.06)
    cues.append(f"{i + 1}\n{stamp(start)} --> {stamp(end)}\n{u['text']}\n")
(wd / "captions.srt").write_text("\n".join(cues))
done(ctx, {"master": "master.mp4", "captions": "captions.srt", "voice_stem": "voice_stem.wav", "music_stem": "music_stem.wav",
           "silent": "silent.mp4"}, {"duration_s": round(duration(master), 3), "caption_offset_s": off})
