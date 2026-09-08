import pytest

from ytdock import ffmpeg
from ytdock.core import UserError


def tools(directory, names=("ffmpeg", "ffprobe")):
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        file = directory / name
        file.write_text("test executable")
        file.chmod(0o755)
    return directory


@pytest.fixture
def environment(monkeypatch):
    for name in ("YTDOCK_FFMPEG_DIR", "YTDOCK_FFMPEG", "YTDOCK_FFPROBE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(ffmpeg, "search_directories", lambda: iter([]))

    def output(path, *args):
        if "-version" in args:
            return f"{path.name} version 7.1"
        if "-encoders" in args:
            return " V..... libx264 encoder\n A..... aac encoder"
        if "-decoders" in args:
            return "\n".join(
                " V..... " + name + " decoder"
                for name in ("h264", "vp8", "vp9", "libdav1d", "aac", "opus", "vorbis")
            )
        return "-fps_mode"

    monkeypatch.setattr(ffmpeg, "output", output)
    return output


def test_missing(environment):
    with pytest.raises(UserError, match="brew install ffmpeg"):
        ffmpeg.find_media_tools()


def test_complete_and_space_path(tmp_path, environment):
    directory = tools(tmp_path / "my ffmpeg")
    assert ffmpeg.find_media_tools(directory=directory) == {
        "ffmpeg": str(directory / "ffmpeg"),
        "ffprobe": str(directory / "ffprobe"),
    }


def test_missing_probe(tmp_path, environment):
    directory = tools(tmp_path / "incomplete", ("ffmpeg",))
    with pytest.raises(UserError, match="ffprobe.*程序不存在"):
        ffmpeg.find_media_tools(directory=directory)


@pytest.mark.parametrize("missing", ["libx264", "aac", "libdav1d", "vp9"])
def test_insufficient_capabilities(tmp_path, environment, monkeypatch, missing):
    directory = tools(tmp_path / "limited")
    monkeypatch.setattr(
        ffmpeg,
        "output",
        lambda path, *args: environment(path, *args).replace(" " + missing + " ", " removed "),
    )
    with pytest.raises(UserError, match="缺少"):
        ffmpeg.find_media_tools(directory=directory)


def test_bad_candidate_skipped(tmp_path, environment, monkeypatch):
    bad, good = tools(tmp_path / "old"), tools(tmp_path / "new")
    monkeypatch.setattr(ffmpeg, "search_directories", lambda: iter([bad, good]))

    def output(path, *args):
        result = environment(path, *args)
        return result.replace(" libx264 ", " limited ") if path.parent == bad else result

    monkeypatch.setattr(ffmpeg, "output", output)
    assert ffmpeg.find_media_tools()["ffmpeg"] == str(good / "ffmpeg")


def test_prefer_complete_pair(tmp_path, environment, monkeypatch):
    first, second = tools(tmp_path / "first", ("ffmpeg",)), tools(tmp_path / "second")
    monkeypatch.setattr(ffmpeg, "search_directories", lambda: iter([first, second]))
    assert ffmpeg.find_media_tools()["ffmpeg"] == str(second / "ffmpeg")


def test_explicit_single_program_has_priority(tmp_path, environment, monkeypatch):
    first, second = tools(tmp_path / "explicit", ("ffmpeg",)), tools(tmp_path / "path")
    monkeypatch.setattr(ffmpeg, "search_directories", lambda: iter([second]))
    warnings = []
    result = ffmpeg.find_media_tools(ffmpeg=first / "ffmpeg", warn=warnings.append)
    assert result == {"ffmpeg": str(first / "ffmpeg"), "ffprobe": str(second / "ffprobe")}


def test_explicit_failure_warns_and_falls_back(tmp_path, environment, monkeypatch):
    good = tools(tmp_path / "good")
    monkeypatch.setattr(ffmpeg, "search_directories", lambda: iter([good]))
    warnings = []
    result = ffmpeg.find_media_tools(directory=tmp_path / "missing", warn=warnings.append)
    assert result["ffmpeg"] == str(good / "ffmpeg")
    assert warnings and "程序不存在" in warnings[0]


def test_cli_overrides_environment(tmp_path, environment, monkeypatch):
    cli, env = tools(tmp_path / "cli"), tools(tmp_path / "env")
    monkeypatch.setenv("YTDOCK_FFMPEG_DIR", str(env))
    assert ffmpeg.find_media_tools()["ffmpeg"] == str(env / "ffmpeg")
    assert ffmpeg.find_media_tools(directory=cli)["ffmpeg"] == str(cli / "ffmpeg")


def test_homebrew_discovery_with_gui_path(tmp_path, monkeypatch):
    directory = tools(tmp_path / "brew/bin")
    monkeypatch.setattr(ffmpeg.os, "get_exec_path", lambda: ["/usr/bin", "/bin"])
    monkeypatch.setattr(ffmpeg, "HOMEBREW_DIRS", (str(directory),))
    assert directory in list(ffmpeg.search_directories())


def test_external_file_not_executable(tmp_path, environment):
    directory = tools(tmp_path / "tools")
    (directory / "ffmpeg").chmod(0o644)
    with pytest.raises(UserError, match="没有执行权限"):
        ffmpeg.find_media_tools(directory=directory)


def test_failure_details_and_timeout(tmp_path, environment, monkeypatch):
    directory = tools(tmp_path / "tools")

    def failed(*args):
        raise UserError("执行超时")

    monkeypatch.setattr(ffmpeg, "output", failed)
    with pytest.raises(UserError, match="执行超时"):
        ffmpeg.find_media_tools(directory=directory)


def test_parser_does_not_accept_codec_name_in_description():
    assert "libx264" not in ffmpeg.codec_names(" V..... libx264rgb Similar to libx264")
