"""Real macOS-compatible filenames, supplied literally or as terminal input."""

import shlex

import pytest

from ytdock.core import UserError
from ytdock.path_utils import local_path


@pytest.mark.parametrize(
    "name",
    [
        "中文 视频 🎬.mp4",
        "a'b\"c.mp4",
        "[video] (1) & #!?;$`clip`.mp4",
        "back\\slash.mp4",
        " leading and trailing.mp4 ",
        "trailing-tab.mp4\t",
        "line\nbreak\r.mp4",
        "café-e\u0301.mp4",
    ],
)
@pytest.mark.parametrize("spelling", ["literal", "quoted", "escaped", "double"])
def test_special_filenames(tmp_path, name, spelling):
    path = tmp_path / name
    path.touch()
    raw = str(path)
    values = {
        "literal": raw,
        "quoted": shlex.quote(raw),
        "escaped": "".join("\\" + c if not c.isalnum() and c not in "/._-" else c for c in raw),
        "double": '"' + "".join("\\" + c if c in '\\"$`' else c for c in raw) + '"',
    }
    assert local_path(values[spelling]) == path.resolve()


def test_literal_wins_over_shell_spelling(tmp_path):
    literal = tmp_path / "a\\ b.mp4"
    decoded = tmp_path / "a b.mp4"
    literal.touch()
    decoded.touch()
    assert local_path(str(literal)) == literal.resolve()


def test_nul_is_invalid(tmp_path):
    with pytest.raises(UserError):
        local_path(str(tmp_path / "bad\x00.mp4"))
