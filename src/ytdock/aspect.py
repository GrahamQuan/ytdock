"""Validated display ratios and square-pixel output dimensions."""

import re
from fractions import Fraction

from .core import UserError
from .i18n import t

PRESETS = ("16:9", "9:16", "1:1", "4:3", "3:4")


def dimensions(video, ratio):
    if not isinstance(ratio, str) or not re.fullmatch(
        r"\s*[0-9]{1,4}\s*[:/]\s*[0-9]{1,4}\s*", ratio
    ):
        raise UserError(t("aspect.invalid"))
    try:
        value = Fraction(re.sub(r"\s+", "", ratio).replace(":", "/"))
    except (ValueError, ZeroDivisionError):
        raise UserError(t("aspect.invalid")) from None
    if not Fraction(1, 10) <= value <= 10:
        raise UserError(t("aspect.invalid"))
    # Retain the source's longest dimension, with exact ratio and even pixels.
    w, h = value.numerator, value.denominator
    unit = max(1, max(video["width"], video["height"]) // (2 * max(w, h)))
    width, height = 2 * w * unit, 2 * h * unit
    if max(width, height) > 8192:
        raise UserError(t("aspect.invalid"))
    return width, height
