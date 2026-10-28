# Third-party notices

## Mona Sans

The fictional sample includes `examples/sample-demo/assets/fonts/MonaSansVF.ttf`.
Copyright 2022 The Mona Sans Project Authors, with Reserved Font Name "Mona".
It is redistributed unmodified under the SIL Open Font License 1.1; the complete license and
copyright notice are next to the font in `OFL-MonaSans.txt`.
Upstream: https://github.com/github/mona-sans.

## Dependencies and optional integrations

Python dependencies are installed separately, not vendored: pydantic, PyYAML, jsonschema, NumPy,
Pillow and pytest. ffmpeg, ffprobe and Tesseract are separately installed system tools.
Their own licenses apply. macOS `say` is provided by the operating system and is not distributed.

Glassbox's original backend, frontend and documentation are covered by MIT. It references system
fonts but bundles no web fonts, CDN scripts or external design-reference files. Playwright and its
Chromium download are optional test dependencies installed separately; their own licenses apply.
Node.js is used for router tests and is not an application runtime dependency.

Remotion, cloud image/TTS providers, workspace skills (including video-shotcraft), custom agents,
MCP servers and Workflow OS are optional integration references, not bundled implementations.
Verify their licenses, account permissions and data-handling terms before using them.
The MIT license does not relicense any of those tools or the Mona Sans font.
