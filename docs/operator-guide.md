# Operator guide

Run commands from the vpg checkout using the environment installed from `requirements.txt`.
No workspace, model API, MCP server or custom agent is needed for the engine.
See the [worker capabilities](skill-bindings.md) and [extension guide](extension-guide.md).

1. Run `python -m vpg validate`, `python -m vpg docs --check` and `python -m vpg dry-run`.
2. Initialize with `python -m vpg init /absolute/path/to/film --name "Film"`.
3. Put the requester's actual brief in `sources/brief.md`; add authorized product evidence and brand
   authority. A brief alone does not satisfy source validation.
4. Bind production commands in `vpg.project.yaml`. The sample demonstrates asset, voice, scene and
   assembly contracts; it is not a production renderer or TTS service.
5. Run `python -m vpg run /absolute/path/to/film` until it waits, passes or blocks.
6. At `WAITING_RUNTIME`, run `python -m vpg tasks /absolute/path/to/film`. Give each packet to a worker.
   Respect declared outputs and control-document access; submit using the packet's token and command.
   Review JSON must echo the bytes it judged. Use an independent reviewer, not the producer.
7. At `WAITING_HUMAN`, present the evidence to the named human. Only after their actual decision,
   record `python -m vpg approve /absolute/path/to/film <node> --actor "<name>" --reason "<their words>"`.
8. At `BLOCKED`, read `.vpg/escalations/`, report the cause, and have an authorized human resolve it.
   Never reset a budget merely to hide repeated failure.
9. After interruption, repeat `run`; approved work is retained. Approved controls change only through
   `change-request`. Engine/config changes require `accept-config` and fresh review.
10. Before handoff, check the package manifest, `ledger-verify`, release approval, outstanding human
    dimensions and learning proposal. Applying learning to the reusable rule base needs human approval.

## Privacy and publication

Keep production projects and name lists outside this public checkout. Configure `privacy.names` with
an authorized external list when captured UI contains people. OCR requires Tesseract and fails closed
when it cannot finish. Moving UI needs declared `live_windows`; see the extension guide.
This scanner is a gate, not a promise that arbitrary media is safe to publish.

The CLI's `publish` command makes local version copies only when the production config enables it.
It does not authorize uploading media to GitHub or any service. The repository contains fictional
fixtures only. Release, listening and taste remain named human decisions.
