"""Legacy behavior assertions use Chinese explicitly; production defaults to English."""

import pytest

from ytdock.i18n import get_language, set_language


@pytest.fixture(autouse=True)
def chinese_contract_messages():
    previous = get_language()
    set_language("zh-CN")
    yield
    set_language(previous)
