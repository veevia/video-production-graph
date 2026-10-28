"""#169 and #170, from the Water chat's E21 and E22: a clear `--notes` error, and no redaction warning from a count field."""
from __future__ import annotations

from types import SimpleNamespace as NS

from tests.test_preflight import held
from vpg import cli
from vpg.tools import verify

WF = NS(nodes=[], edges=[])


def _warnings(result_data):
    st = NS(inputs=NS(frozen=False, sources=[]), runtime=NS(nodes={"n11_capture": NS(result_data=result_data)}),
            history=NS(versions=[]))
    return [w for w in verify.release_warnings(NS(state=st, project_dir=None), WF, []) if "declared redactions" in w]


def test_a_list_size_or_a_setting_never_declares_a_redaction():
    assert _warnings({"redaction_name_count": 16, "veil_frames": 0, "redact_ratio": 0.4}) == []   # Water N11, v12 and v13
    assert _warnings({"redactions": [[0, 0, 10, 10]]}) == ["n11_capture declared redactions (1 entries); no engine tool "
                                                          "verifies where they sit, so check every frame where product UI moves"]
    assert _warnings({"redacted": True}) and _warnings({"veils": {"S1": [1, 2]}})            # a flag or a map still declares
    for drawn in ({"veil_frames": 120}, {"redactions": 3}, {"redacted": 1}, {"redacted_frames": 240},
                  {"redacted_name_count": 3}, {"veiled_frame_ratio": 0.12}):
        assert _warnings(drawn), drawn                                                       # a count of drawn ones still warns


def test_inline_preflight_notes_get_a_clear_error(tmp_path, capsys):
    proj, eng = held(tmp_path, when="always")
    token = eng.rt("d_build").preflight["token"]
    code = cli.main(["preflight", str(proj), "d_build", "--token", token, "--verdict", "revise", "--actor", "agent:critic",
                     "--notes", "the title overlaps the chart in chapter two; move it up by 40 px"])
    err = capsys.readouterr().err
    assert code == 4 and "--notes expects the path of a file with the reviewer's notes" in err
    assert "no file at 'the title overlaps the chart in chapter two; move it up b…' (relative to" in err
    assert "could not read or write" not in err
    missing = tmp_path / ("deep/" * 12) / "review-notes-for-the-second-chapter.md"   # a long absolute path keeps its tail
    assert cli.main(["preflight", str(proj), "d_build", "--token", token, "--verdict", "revise", "--actor", "agent:critic",
                     "--notes", str(missing)]) == 4
    err = capsys.readouterr().err
    assert "review-notes-for-the-second-chapter.md'" in err and "relative to the current directory" not in err
