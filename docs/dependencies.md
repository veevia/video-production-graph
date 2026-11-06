# Dependencies and project boundary

## Runtime

Python 3.11+ with `requirements.txt` supplies pydantic, PyYAML, jsonschema, NumPy,
Pillow and pytest. ffmpeg and ffprobe are external media tools. Tesseract is
required when OCR privacy scanning is enabled.

The engine loads workflow definitions, schemas, rubrics and templates relative
to its checkout. Production projects live outside it. Renderers, TTS providers
and agent hosts are optional bindings configured by each project; the engine
does not call a model or network service.

## Synthetic sample

The sample uses generated geometric cards, invented labels and a licensed font.
`examples/sample-demo/pipeline/fixtures.py` reproduces its image inputs. Those
labels are an embedded bitmap, so the rasters do not depend on FreeType.
The sample renders with Pillow and ffmpeg. macOS `say` is optional; the alternative
tone track is explicitly degraded and cannot establish listening quality.

## Glassbox

The optional local viewer uses Python's standard library on macOS/Linux, plus
optional PyYAML. POSIX file locking is not supported on Windows.
There is no frontend build, CDN, hosted account or model API dependency.
Project and library roots are explicit.

Playwright/Chromium and ffmpeg support isolated browser tests. Node.js runs
frontend module tests. These are development dependencies, not requirements
for serving the app.

## Publication boundary

The public inventory contains engine/app code, generic contracts, blank templates,
public documentation and synthetic tests. Production inputs, media, preferences,
feedback, histories and private design records do not belong in it.
See [maintainer guidance](standalone-sync.md).

Original code uses MIT. The bundled Mona Sans font retains its original SIL
license. External tools retain their own licenses; see
[third-party notices](../THIRD_PARTY_NOTICES.md).
