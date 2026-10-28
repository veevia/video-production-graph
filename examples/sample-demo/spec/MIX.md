---
spec: {family: vpg-spec/v1, doc: mix, version: 1}
loudness: {integrated_lufs: -16.0, tolerance_lu: 1.0, true_peak_max_dbtp: -1.0, precheck_max_limiting_db: 6.0}
vo_over_music: {metric: audio.vo_over_music.p10, version: 2, floor_lu: 10.0}
ducking: {depth_db: 9, attack_ms: 120, release_ms: 400, look_ahead_ms: 150}
rules:
  - id: MX-VOM-001
    level: MUST
    statement: The voice sits above the music by at least the floor, measured on the fixed basis.
    rationale: Below about 10 LU, speech over music loses intelligibility.
    source: R-AUD-002
    enforcement: {type: tool, metric: audio.vo_over_music.p10, gate: GT-VOM-001}
    examples:
      do: ["Measure both stems the same way every build"]
      dont: ["Re-meter only the voice after a mix change"]
  - id: MX-VOM-002
    level: MUST
    statement: The voice sits above the music by the benchmark films' typical margin (the median), not only at its quietest moments.
    rationale: The 10th percentile follows a film's quietest moments, where the benchmarks themselves differ; the median kept apart the versions whose music crowded the voice.
    source: "R-AUD-002"
    enforcement: {type: tool, metric: audio.vo_over_music.median, gate: GT-VOM-004}
    examples:
      do: ["Duck the bed under every phrase, not only under the quietest words"]
      dont: ["A bed that sits a few LU under the voice for the whole film"]
---
# Mix spec

## Intent
The voice leads; the pad fills the pauses.

## Measurement basis
Both stems are downmixed to the mean of their channels and metered with BS.1770 momentary loudness.
