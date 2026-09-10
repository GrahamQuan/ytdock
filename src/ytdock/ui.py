"""prompt_toolkit input, scrollable choices and throttled in-place status."""

import asyncio
import time

from prompt_toolkit.application import Application as ToolkitApplication
from prompt_toolkit.application.current import get_app
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import Frame as BaseFrame
from prompt_toolkit.widgets import Label as BaseLabel
from prompt_toolkit.widgets import TextArea

from .controller import execute
from .core import Choice, clock, default_choice, percent, safe_text, size
from .i18n import LANGUAGES, get_language, language_name, set_language, t


def Application(**kwargs):
    from .screen import current_screen

    screen = current_screen()
    if screen:
        return screen.page(**kwargs)
    keys = KeyBindings()

    @keys.add("tab")
    def next_focus(event):
        event.app.layout.focus_next()

    @keys.add("s-tab")
    def previous_focus(event):
        event.app.layout.focus_previous()

    kwargs["key_bindings"] = merge_key_bindings([kwargs.get("key_bindings", KeyBindings()), keys])
    return ToolkitApplication(**kwargs)


def navigation_label(mode):
    from .screen import current_screen

    return ConditionalContainer(
        Label(lambda: navigation(mode)), filter=Condition(lambda: current_screen() is None)
    )


STYLE = Style.from_dict(
    {
        "frame.border": "#5f87af",
        "selected": "bold #87d7af",
        "navigation.border": "#87d7af bg:default noreverse",
        "navigation.active": "bold #16251c bg:#87d7af noreverse",
        "title": "bold",
        "hint": "#888888",
        "resolution frame.border": "#87d7af",
        "subtitle frame.border": "#888888",
    }
)


def Frame(body, *, title="", style="", **kwargs):
    def focused():
        return get_app().layout.has_focus(body)

    return HSplit(
        [BaseFrame(body, title=title, style=style, **kwargs)],
        style=lambda: "class:resolution" if focused() else "class:subtitle",
    )


class Label(BaseLabel):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Allocate explanatory text before expanding the scrollable choice list.
        self.window.height = Dimension(min=1, weight=100)


def choice_window(control):
    # Let prompt_toolkit measure wrapped content on every render, including resize.
    return Window(
        control, height=Dimension(min=1, max=14), wrap_lines=True, dont_extend_height=True
    )


class ShortcutControl(FormattedTextControl):
    def __init__(self, text):
        self.source = text
        self.width = 80
        super().__init__(self.formatted)

    def formatted(self):
        lines = []
        for group in self.source().split("\n"):
            line = ""
            for action in group.split(" · "):
                candidate = line + " · " + action if line else action
                if line and get_cwidth(candidate) > self.width:
                    lines.append(line)
                    line = action
                else:
                    line = candidate
            lines.append(line)
        return "\n".join(lines)

    def create_content(self, width, height):
        self.width = width
        return super().create_content(width, height)


def shortcuts(text):
    return Window(
        ShortcutControl(text),
        height=Dimension(min=1, weight=100),
        wrap_lines=True,
        dont_extend_height=True,
        style="class:hint",
    )


def exit_bindings(bindings, callback):
    for key in ("c-c", "<sigint>"):
        bindings.add(key)(callback)


def language_bindings(bindings):
    @bindings.add("c-o")
    def choose_language(event):
        from .screen import current_screen

        if screen := current_screen():
            screen.open_language()
            return
        app = event.app
        previous_layout, previous_bindings = app.layout, app.key_bindings
        selected = LANGUAGES.index(get_language())
        keys = KeyBindings()

        def restore():
            app.layout = previous_layout
            app.key_bindings = previous_bindings
            app.invalidate()

        @keys.add("up")
        def up(event):
            nonlocal selected
            selected = max(0, selected - 1)

        @keys.add("down")
        def down(event):
            nonlocal selected
            selected = min(len(LANGUAGES) - 1, selected + 1)

        @keys.add("enter")
        def confirm(event):
            set_language(LANGUAGES[selected])
            restore()

        @keys.add("escape")
        def cancel(event):
            restore()

        exit_bindings(keys, lambda event: event.app.exit(exception=KeyboardInterrupt()))

        def rows():
            result = []
            for i, code in enumerate(LANGUAGES):
                if i == selected:
                    result.append(("[SetCursorPosition]", ""))
                result.append(
                    (
                        "class:selected" if i == selected else "",
                        ("❯ " if i == selected else "  ") + language_name(code) + "\n",
                    )
                )
            result[-1] = (result[-1][0], result[-1][1].rstrip("\n"))
            return result

        control = FormattedTextControl(rows, focusable=True, show_cursor=False)
        app.layout = Layout(
            HSplit(
                [
                    Label(t("language.select_title"), style="class:title"),
                    choice_window(control),
                    shortcuts(lambda: t("language.select_hint")),
                ]
            ),
            focused_element=control,
        )
        app.key_bindings = keys
        app.invalidate()


MODES = ("download", "compress", "subtitles", "tasks")


def navigation(mode):
    fragments = []
    for index, key in enumerate(MODES):
        if index:
            fragments.append(("", "   "))
        label = t("mode." + key)
        fragments.append(
            (
                "class:navigation.active" if mode == key else "class:hint",
                f"[{label}]" if mode == key else label,
            )
        )
    return fragments


async def read_subtitle_input(text):
    bindings = KeyBindings()
    language_bindings(bindings)
    fields = [
        TextArea(text=text.get(key, ""), height=1, multiline=False, prompt="❯ ", wrap_lines=False)
        for key in ("video", "captions")
    ]
    for field in fields:
        field.buffer.cursor_position = len(field.text)

    def values():
        return dict(zip(("video", "captions"), (field.text for field in fields)))

    def submit(buffer):
        if all(field.text.strip() for field in fields):
            app.exit(result=("submit", values()))
        return True

    for field in fields:
        field.buffer.accept_handler = submit

    exit_bindings(bindings, lambda event: event.app.exit(exception=KeyboardInterrupt()))
    app = Application(
        layout=Layout(
            HSplit(
                [
                    navigation_label("subtitles"),
                    Frame(fields[0], title=lambda: t("local_subtitles.video")),
                    Frame(fields[1], title=lambda: t("local_subtitles.captions")),
                    shortcuts(lambda: t("local_subtitles.input_hint")),
                ]
            ),
            focused_element=fields[0],
        ),
        key_bindings=bindings,
        style=STYLE,
        full_screen=False,
    )
    return await app.run_async()


async def select_local_subtitles(info, downloads):
    index = 0
    bindings = KeyBindings()
    language_bindings(bindings)

    @bindings.add("up")
    def up(event):
        nonlocal index
        index = max(0, index - 1)

    @bindings.add("down")
    def down(event):
        nonlocal index
        index = min(1, index + 1)

    @bindings.add("enter")
    def submit(event):
        event.app.exit(result=index == 0)

    @bindings.add("escape")
    def back(event):
        event.app.exit(result=False)

    exit_bindings(bindings, lambda event: event.app.exit(exception=KeyboardInterrupt()))

    def details():
        audio = info["audio"]
        audio_text = (
            t("no_audio_no_track_will_be_added")
            if audio is None
            else t(
                "local_subtitles.audio",
                count=info["audio_count"],
                index=audio["index"],
                codec=audio["codec_name"],
            )
        )
        return t(
            "local_subtitles.details",
            count=info["captions"]["count"],
            audio=audio_text,
            directory=downloads,
        )

    def rows():
        result = []
        for i, key in enumerate(("local_subtitles.continue", "local_subtitles.cancel")):
            if i == index:
                result.append(("[SetCursorPosition]", ""))
            result.append(
                (
                    "class:selected" if i == index else "",
                    ("❯ " if i == index else "  ") + t(key) + ("\n" if i == 0 else ""),
                )
            )
        return result

    control = FormattedTextControl(rows, focusable=True, show_cursor=False)
    app = Application(
        layout=Layout(
            HSplit(
                [
                    navigation_label("subtitles"),
                    Label(
                        f"{info['title']} · {info['choice']['width']}×{info['choice']['height']} · "
                        f"{info['choice']['fps']:g}fps · {clock(info['duration'])}"
                    ),
                    Label(details),
                    ConditionalContainer(
                        Label(
                            lambda: t(
                                "local_subtitles.adjusted",
                                milliseconds=info["captions"].get("shortened_ms", 0),
                            )
                        ),
                        filter=Condition(lambda: bool(info["captions"]["adjusted"])),
                    ),
                    Frame(choice_window(control), title=lambda: t("local_subtitles.choose_action")),
                    shortcuts(lambda: t("local_subtitles.confirm_hint")),
                ]
            ),
            focused_element=control,
        ),
        key_bindings=bindings,
        style=STYLE,
        full_screen=False,
    )
    return await app.run_async()


async def read_input(mode="download", text="") -> tuple[str, str]:
    if mode == "subtitles":
        return await read_subtitle_input(text)
    bindings = KeyBindings()
    language_bindings(bindings)
    field = TextArea(text=text, height=1, multiline=False, prompt="❯ ", wrap_lines=False)
    field.buffer.cursor_position = len(text)
    field.buffer.accept_handler = lambda buffer: (
        (app.exit(result=("submit", buffer.text)) if buffer.text.strip() else None) or True
    )
    exit_bindings(bindings, lambda event: event.app.exit(exception=KeyboardInterrupt()))

    if mode == "compress":

        @bindings.add("escape")
        def back(event):
            event.app.exit(result=("back", field.text))

    app = Application(
        layout=Layout(
            HSplit(
                [
                    navigation_label(mode),
                    Frame(
                        field,
                        title=lambda: t(
                            "youtube_url" if mode == "download" else "local_video_file_path"
                        ),
                    ),
                    shortcuts(
                        lambda: (
                            t("enter_continue_tab_switch_mode")
                            + (t("esc_back_to_download") if mode == "compress" else "")
                            + t("ctrl_c_exit_ctrl_o_language")
                        ),
                    ),
                ]
            ),
            focused_element=field,
        ),
        key_bindings=bindings,
        style=STYLE,
        full_screen=False,
    )
    return await app.run_async()


async def read_url() -> str:
    action, text = await read_input()
    return text


async def select_compression(info, downloads):
    from .compression import PROFILES

    index = 0
    options = list(PROFILES)
    bindings = KeyBindings()
    language_bindings(bindings)

    @bindings.add("up")
    def up(event):
        nonlocal index
        index = max(0, index - 1)

    @bindings.add("down")
    def down(event):
        nonlocal index
        index = min(1, index + 1)

    @bindings.add("enter")
    def enter(event):
        event.app.exit(result=options[index])

    @bindings.add("escape")
    def back(event):
        event.app.exit(result=None)

    exit_bindings(bindings, lambda event: event.app.exit(exception=KeyboardInterrupt()))

    def rows():
        descriptions = [
            "stronger_compression_may_lose_visible_detail",
            "preserve_more_detail_slower_potentially_larger_files",
        ]
        result = []
        for i, key in enumerate(options):
            if i == index:
                result.append(("[SetCursorPosition]", ""))
            result.append(
                (
                    "class:selected" if i == index else "",
                    ("❯ " if i == index else "  ")
                    + t(PROFILES[key]["label"])
                    + (t("default") if i == 0 else "")
                    + "\n    "
                    + t(descriptions[i])
                    + "\n",
                )
            )
        result[-1] = (result[-1][0], result[-1][1].rstrip("\n"))
        return result

    def audio_label():
        audio = info["audio"]
        if audio is None:
            return t("no_audio_no_track_will_be_added")
        label = t(
            "audio_track",
            index=audio["index"],
            codec=audio.get("codec_name") or t("unknown_codec"),
            language=safe_text(audio.get("tags", {}).get("language") or t("unknown_language")),
        )
        if info["audio_count"] > 1:
            return t("audio_tracks_keeping_only", count=info["audio_count"], track=label)
        return label

    video = info["video"]
    choice = info["choice"]
    control = FormattedTextControl(rows, focusable=True, show_cursor=False)
    app = Application(
        layout=Layout(
            HSplit(
                [
                    navigation_label("compress"),
                    Label(
                        f"{info['title']} · {choice['width']}×{choice['height']} · "
                        f"{choice['fps']:g}fps · {clock(info['duration'])} · "
                        f"{size(info['input_bytes'])}",
                        style="class:title",
                    ),
                    Label(
                        lambda: t(
                            "main_video",
                            index=video["index"],
                            codec=video["codec_name"],
                            audio=audio_label(),
                        )
                    ),
                    Frame(choice_window(control), title=lambda: t("compression.choose_mode")),
                    Label(
                        lambda: t(
                            "keep_source_resolution_aspect_ratio_and_frame_rate_output_mp4",
                            directory=downloads,
                        )
                    ),
                    shortcuts(lambda: t("select_enter_compress_esc_back_ctrl_c_exit_ctrl_o")),
                ]
            ),
            focused_element=control,
        ),
        key_bindings=bindings,
        style=STYLE,
        full_screen=False,
    )
    return await app.run_async()


async def select(info: dict, downloads, settings=None) -> Choice | None:
    options = [Choice(**c) for c in info["choices"]]
    index = default_choice(options)
    recommended = index
    settings = settings if settings is not None else {}
    if settings.get("resolution"):
        index = next((i for i, c in enumerate(options) if c.key == settings["resolution"]), index)
    if not info.get("caption") or info.get("subtitle_error"):
        settings["subtitles"] = False

    def active():
        current = get_app().layout.current_control
        return next((name for name, control in controls.items() if control is current), None)

    message = ""
    bindings = KeyBindings()
    language_bindings(bindings)

    @bindings.add("up")
    def up(event):
        nonlocal index
        nonlocal message
        if active() == "resolution":
            index = max(0, index - 1)
        elif active() == "subtitles":
            settings["subtitles"] = False
            message = ""

    @bindings.add("down")
    def down(event):
        nonlocal index
        nonlocal message
        if active() == "resolution":
            index = min(len(options) - 1, index + 1)
        elif active() == "subtitles":
            if info.get("subtitle_error"):
                message = t("subtitle.not_ready", reason=info["subtitle_error"])
            else:
                settings["subtitles"] = True
                message = ""

    @bindings.add("enter")
    def enter(event):
        if active() == "confirm":
            settings["resolution"] = options[index].key
            event.app.exit(result=options[index])

    @bindings.add("escape")
    def back(event):
        settings["resolution"] = options[index].key
        event.app.exit(result=None)

    exit_bindings(bindings, lambda event: event.app.exit(exception=KeyboardInterrupt()))

    def rows():
        result = []
        for i, option in enumerate(options):
            if i == index:
                result.append(("[SetCursorPosition]", ""))
            result.append(
                (
                    "class:selected" if i == index else "",
                    ("❯ " if i == index else "  ")
                    + option.label()
                    + (t("recommended") if i == recommended else "")
                    + "\n",
                )
            )
        result[-1] = (result[-1][0], result[-1][1].rstrip("\n"))
        return result

    def frame_title(key, group):
        return t(key)

    def panel(body, *, title, style):
        return HSplit([BaseFrame(body, title=title)], style=style)

    def frame_style(group):
        return "class:resolution" if active() == group else "class:subtitle"

    def subtitle_rows():
        selected = int(settings.get("subtitles", False))
        return [
            (
                "class:selected" if i == selected else "",
                ("❯ " if i == selected else "  ") + t(key) + ("\n" if i == 0 else ""),
            )
            for i, key in enumerate(("subtitle.off", "subtitle.on"))
        ]

    def subtitle_description():
        track = info.get("caption")
        if not track:
            return ""
        return t(
            "subtitle.summary_on" if settings.get("subtitles") else "subtitle.summary_off",
            source=t("subtitle." + track["kind"]),
            language=track["language"],
        )

    control = FormattedTextControl(rows, focusable=True, show_cursor=False)
    subtitle_control = FormattedTextControl(
        subtitle_rows,
        focusable=True,
        show_cursor=False,
        get_cursor_position=lambda: Point(0, int(settings.get("subtitles", False))),
    )
    confirm_control = FormattedTextControl(
        lambda: t("download.confirm"), focusable=True, show_cursor=False
    )
    controls = {"resolution": control, "subtitles": subtitle_control, "confirm": confirm_control}
    app = Application(
        layout=Layout(
            HSplit(
                [
                    navigation_label("download"),
                    Label(f"{info['title']} · {clock(info['duration'])}", style="class:title"),
                    panel(
                        HSplit(
                            [
                                choice_window(control),
                                ConditionalContainer(
                                    Label(lambda: t("transcoding.notice")),
                                    filter=Condition(lambda: options[index].transcode),
                                ),
                            ]
                        ),
                        title=lambda: frame_title("download.resolution_frame", "resolution"),
                        style=lambda: frame_style("resolution"),
                    ),
                    ConditionalContainer(
                        panel(
                            HSplit(
                                [
                                    choice_window(subtitle_control),
                                    Label(subtitle_description),
                                    ConditionalContainer(
                                        Label(lambda: message),
                                        filter=Condition(lambda: bool(message)),
                                    ),
                                ]
                            ),
                            title=lambda: frame_title("subtitle.frame", "subtitles"),
                            style=lambda: frame_style("subtitles"),
                        ),
                        filter=Condition(lambda: bool(info.get("caption"))),
                    ),
                    HSplit(
                        [
                            Label(
                                lambda: t(
                                    "output_format_mp4_h_264_aac_no_audio_added_to",
                                    directory=downloads,
                                ).split("\n", 1)[0]
                            ),
                            Label(
                                lambda: t(
                                    "output_format_mp4_h_264_aac_no_audio_added_to",
                                    directory=downloads,
                                ).split("\n", 1)[1]
                            ),
                        ]
                    ),
                    ConditionalContainer(
                        Label(lambda: t("audio.original_label", language=info["audio_language"])),
                        filter=Condition(lambda: bool(info.get("audio_language"))),
                    ),
                    panel(
                        choice_window(confirm_control),
                        title=lambda: frame_title("download.confirm", "confirm"),
                        style=lambda: frame_style("confirm"),
                    ),
                    shortcuts(
                        lambda: t(
                            "focus.confirm_hint" if active() == "confirm" else "focus.options_hint"
                        )
                    ),
                ]
            ),
            focused_element=control,
        ),
        key_bindings=bindings,
        style=STYLE,
        full_screen=False,
    )
    return await app.run_async()


def status_text(event: dict, tick: int) -> str:
    stage = event.get("stage", "stage.fetching_information")
    total = event.get("total")
    done = event.get("done", 0) or 0
    pct = percent(done, total, event.get("finished", False))
    pieces = [t(stage), pct or "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[tick % 10]]
    if event.get("download"):
        data = size(done)
        if total:
            data += " / " + (t("approx") if event.get("approximate") else "") + size(total)
        pieces.append(data)
        if event.get("speed"):
            pieces.append(size(event["speed"]) + "/s")
        if event.get("eta") is not None:
            pieces.append(t("remaining") + clock(event["eta"]))
    if not event.get("download") and event.get("eta") is not None:
        pieces.append(t("remaining") + clock(event["eta"]))
    return " · ".join(pieces)


async def busy(request, downloads, lock):
    cancel = asyncio.Event()
    state = {
        "stage": "stage.reading_information"
        if request.get("operation")
        in ("inspect_local", "compress", "inspect_subtitles", "local_subtitles")
        else "stage.fetching_information"
    }
    bindings = KeyBindings()
    saved = {}
    notices = []

    def cancel_task(event):
        cancel.set()
        state.clear()
        state["stage"] = "stage.cancelling"
        event.app.invalidate()

    exit_bindings(bindings, cancel_task)

    def update(event):
        from .screen import current_screen

        if screen := current_screen():
            screen.record_event(event)
        if "task_outcome" in event:
            return
        if event.get("notice"):
            notices.append(event["notice"])
            app.invalidate()
            return
        if event.get("saved"):
            saved.update(event["saved"])
            app.invalidate()
            return
        if not cancel.is_set():
            state.clear()
            state.update(event)

    app = Application(
        layout=Layout(
            HSplit(
                [
                    navigation_label(
                        "subtitles"
                        if request.get("operation") in ("inspect_subtitles", "local_subtitles")
                        else "compress"
                        if request.get("operation") in ("inspect_local", "compress")
                        else "download"
                    ),
                    ConditionalContainer(
                        Label(
                            lambda: "\n".join(
                                t("subtitle.saved_" + kind, path=path)
                                for kind, path in saved.items()
                            )
                        ),
                        filter=Condition(lambda: bool(saved)),
                    ),
                    ConditionalContainer(
                        Label(lambda: "\n".join(notices)), filter=Condition(lambda: bool(notices))
                    ),
                    Label(lambda: status_text(state, int(time.monotonic() * 10))),
                    Label(t("ctrl_c_cancel_and_clean_up"), style="class:hint"),
                ]
            )
        ),
        key_bindings=bindings,
        style=STYLE,
        full_screen=False,
        refresh_interval=0.1,
    )

    async def run_job():
        try:
            result = await execute(request, downloads, lock, cancel, update)
        except Exception as exc:
            result = exc
        if app.is_running:
            app.exit(result=result)
        return result

    job = None

    def start():
        nonlocal job
        job = asyncio.create_task(run_job())

    try:
        result = await app.run_async(pre_run=start)
    finally:
        if job and not job.done():
            cancel.set()
            await job
    if isinstance(result, Exception):
        raise result
    return result
