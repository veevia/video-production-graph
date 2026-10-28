import shlex

import pytest

import inbox
import server
from glassbox.web import make_server
from synthetic import write_engine


def test_explicit_roots_and_opt_in_library():
    with pytest.raises(SystemExit) as error:
        server.parse_args([])
    assert error.value.code == 2
    args = server.parse_args(["--root", "/projects", "--root", "/other"])
    assert args.roots == ["/projects", "/other"]
    assert args.library_roots is None
    args = server.parse_args(["--root", "/projects", "--library-root", "/films", "--library-root", "/clips"])
    assert args.library_roots == ["/films", "/clips"]
    with pytest.raises(SystemExit):
        server.parse_args(["--root", "/projects", "--library-root", "/films", "--no-library"])


def test_bundled_engine_is_relative_to_the_app_not_cwd(tmp_path, monkeypatch):
    engine = write_engine(tmp_path)
    monkeypatch.setattr(server, "HERE", str(engine / "apps" / "glassbox"))
    monkeypatch.chdir(tmp_path)
    assert server.bundled_engine() == str(engine)
    assert server.parse_args(["--root", str(tmp_path)]).engine == str(engine)
    monkeypatch.setattr(server, "HERE", str(tmp_path / "unbundled" / "viewer"))
    assert server.bundled_engine() is None


def test_invalid_roots_and_engine_fail_before_serving(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(server, "bundled_engine", lambda: None)
    assert server.main(["--root", str(tmp_path)]) == 2
    assert "--engine is required" in capsys.readouterr().err
    assert server.main(["--root", str(tmp_path), "--engine", str(tmp_path)]) == 2
    assert "no readable workflow graph" in capsys.readouterr().err
    assert server.main(["--root", str(tmp_path / "missing")]) == 2
    assert "not a directory" in capsys.readouterr().err


@pytest.mark.parametrize("protected", ["projects", "library", "engine", "static"])
def test_data_store_cannot_write_under_read_only_roots(tmp_path, protected):
    folders = {name: tmp_path / name for name in ("projects", "library", "engine", "static")}
    for folder in folders.values():
        folder.mkdir()
    data = folders[protected] / "private-data"
    with pytest.raises(ValueError, match="outside the read-only roots"):
        make_server([str(folders["projects"])], str(folders["engine"]), str(data),
                    library_roots=[str(folders["library"])], static_dir=str(folders["static"]))
    assert not data.exists()


def test_data_store_symlink_cannot_escape_the_boundary(tmp_path):
    projects = tmp_path / "projects"
    projects.mkdir()
    link = tmp_path / "alias"
    link.symlink_to(projects, target_is_directory=True)
    with pytest.raises(ValueError, match="outside the read-only roots"):
        make_server([str(projects)], str(tmp_path / "engine"), str(link / "data"))
    assert not (projects / "data").exists()


def test_offline_producer_hint_includes_required_roots_and_connection_settings(tmp_path, monkeypatch, capsys):
    project = tmp_path / "a film"
    nested = project / "sources"
    nested.mkdir(parents=True)
    (project / "vpg.project.yaml").write_text("name: Fictional film\n")
    data = tmp_path / "private notes"

    def unavailable(*args):
        raise inbox.Down("connection refused")

    monkeypatch.setattr(inbox, "_inbox_get", unavailable)
    assert inbox.main(["--project", str(nested), "--base", "http://127.0.0.1:4427",
                       "--data-dir", str(data), "--ack", "note-id", "--reply", "A fictional plan"]) == 2
    message = capsys.readouterr().err
    assert f"--root {shlex.quote(str(project))}" in message
    assert "--port 4427" in message
    assert f"--data-dir {shlex.quote(str(data))}" in message
    assert "--engine DIR" in message and "python3 -B" in message
    assert not data.exists()


@pytest.mark.parametrize("base", ["https://127.0.0.1:4420", "http://example.invalid:4420"])
def test_start_hint_explains_the_loopback_http_boundary(base):
    args = inbox.parse(["--project", "/fictional-film", "--base", base])
    assert "use a loopback HTTP --base" in inbox._start_hint(args)


def test_producer_help_uses_a_neutral_example(capsys):
    assert inbox.main(["--help"]) == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "(for example 'Film producer (Copilot chat)')" in help_text
