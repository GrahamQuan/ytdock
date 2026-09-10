"""Parse local-file input shared by every feature, without invoking a shell."""

import shlex
from pathlib import Path

from .core import UserError
from .i18n import t


def _shell_path_words(value: str) -> list[str]:
    # shlex preserves \$ and \` inside double quotes, unlike macOS shells.
    # Remove only those escapes; preserve literal backslashes and single quotes.
    normalized = []
    quote = None
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and quote != "'" and index + 1 < len(value):
            following = value[index + 1]
            if quote == '"' and following in "$`":
                normalized.append(following)
            else:
                normalized.extend((char, following))
            index += 2
            continue
        if char in ("'", '"'):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
        normalized.append(char)
        index += 1
    return shlex.split("".join(normalized))


def local_path(value: str) -> Path:
    if not value or "\x00" in value:
        raise UserError(t("enter_the_absolute_path_of_one_local_video_file_multiple"))
    # Prefer actual filenames: whitespace, quotes, backslashes and newlines are
    # legal filename characters on macOS. Never evaluate shell expressions.
    for spelling in dict.fromkeys((value, value.strip())):
        try:
            literal = Path(spelling).expanduser()
        except RuntimeError:
            raise UserError(t("unable_to_expand_the_home_directory_use_an_absolute_path")) from None
        if literal.is_absolute() and literal.is_file():
            return literal.resolve()
    # Finder/Terminal may supply a quoted or backslash-escaped single path.
    # Parse the original text so an escaped trailing space remains part of it.
    try:
        parts = _shell_path_words(value)
    except ValueError:
        raise UserError(t("unmatched_quotes_in_the_path_paste_the_complete_path_again")) from None
    if len(parts) != 1:
        raise UserError(t("only_one_file_is_supported_quote_paths_containing_spaces_or"))
    try:
        path = Path(parts[0]).expanduser()
    except RuntimeError:
        raise UserError(t("unable_to_expand_the_home_directory_use_an_absolute_path")) from None
    if not path.is_absolute():
        raise UserError(t("use_an_absolute_local_file_path_is_supported_not_a"))
    if not path.exists():
        raise UserError(t("file_not_found_check_the_path"))
    if not path.is_file():
        raise UserError(t("use_a_single_regular_video_file_not_a_directory_or"))
    return path.resolve()
