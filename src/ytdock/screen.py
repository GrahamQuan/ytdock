"""One alternate-screen Application with retained pages and an in-memory task tab."""

import asyncio
from contextvars import ContextVar

from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import fragment_list_to_text
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.containers import WindowAlign
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.utils import get_cwidth

from .history import Record
from .i18n import LANGUAGES, get_language, language_name, set_language, t

_CURRENT = ContextVar("ytdock_screen", default=None)
_MODE = ContextVar("ytdock_mode", default="download")


def current_screen():
    return _CURRENT.get()


class Page:
    """A layout and completion future; not a terminal Application."""

    def __init__(self, screen, layout, key_bindings=None, **kwargs):
        self.screen, self.layout, self.key_bindings = screen, layout, key_bindings or KeyBindings()
        self.mode = _MODE.get()
        self.future = None

    @property
    def is_running(self):
        return bool(self.future and not self.future.done() and self.screen.app.is_running)

    def exit(self, result=None, exception=None):
        if self.future and not self.future.done():
            if exception:
                self.future.set_exception(exception)
            else:
                self.future.set_result(result)

    def invalidate(self):
        self.screen.app.invalidate()

    async def run_async(self, pre_run=None):
        self.future = asyncio.get_running_loop().create_future()
        self.screen.restore_input(self)
        self.screen.pages[self.mode] = self
        if pre_run:
            if self.screen.processing or self.mode != self.screen.active:
                raise RuntimeError("A hidden or parallel task cannot start")
            self.screen.processing = True
        try:
            if self.mode == self.screen.active:
                self.screen.show()
            if pre_run:
                pre_run()
            return await self.future
        finally:
            self.screen.save_input(self)
            if pre_run:
                self.screen.processing = False


class TerminalApplication(Application):
    def __init__(self, owner, **kwargs):
        self.owner = owner
        super().__init__(**kwargs)

    def exit(self, result=None, exception=None, style=""):
        if isinstance(exception, (KeyboardInterrupt, EOFError)):
            return self.terminate(result=130)
        page = self.owner.pages.get(self.owner.active)
        if page:
            self.owner.capture_focus()
            page.exit(result, exception)

    def terminate(self, result=None, exception=None):
        if self.is_running:
            super().exit(result=result, exception=exception)


class DetailControl(FormattedTextControl):
    def __init__(self, record):
        self.record, self.row, self.count = record, 0, 1
        super().__init__(
            "", focusable=True, show_cursor=False, get_cursor_position=lambda: Point(0, self.row)
        )

    def create_content(self, width, height):
        lines = []
        for line in self.record.details().splitlines():
            chunk, cells = "", 0
            for char in line:
                size = get_cwidth(char)
                if cells + size > max(1, width) and chunk:
                    lines.append(chunk)
                    chunk, cells = "", 0
                chunk += char
                cells += size
            lines.append(chunk)
        self.count = len(lines)
        self.row = min(self.row, max(0, self.count - 1))
        self.text = "\n".join(lines)
        return super().create_content(width, height)


def required_height(container, width, height):
    """Reserve text/footer height while allowing focused lists to scroll."""
    width = max(1, width)
    if isinstance(container, ConditionalContainer):
        child = container.content if container.filter() else container.alternative_content
        return required_height(child, width, height) if child else 0
    if isinstance(container, Window):
        if container.content.is_focusable():
            if isinstance(container.content, (BufferControl, DetailControl)):
                return 1
            content = container.content.create_content(width, height)
            line = fragment_list_to_text(content.get_line(content.cursor_position.y))
            return max(1, (get_cwidth(line) + width - 1) // width) if container.wrap_lines() else 1
        return container.preferred_height(width, height).preferred
    children = container.get_children()
    if isinstance(container, VSplit):
        return max(
            (required_height(child, max(1, width - 2), height) for child in children), default=0
        )
    if isinstance(container, HSplit):
        return sum(required_height(child, width, height) for child in children)
    return max((required_height(child, width, height) for child in children), default=0)


class Screen:
    def __init__(self, paths, downloads, lock):
        from .ui import STYLE

        self.paths, self.downloads, self.lock = paths, downloads, lock
        self.active, self.processing = "download", False
        self.pages, self.records, self.current_records = {}, [], {}
        self.notices, self.input_snapshots = {}, {}
        self.modal_stack, self.modal = [], None
        self.task_index = 0
        self.task_view = None
        self.tasks = []
        self.keys = KeyBindings()

        can_switch = Condition(lambda: not self.processing and self.modal is None)

        def switch(direction):
            from .ui import MODES

            self.activate(MODES[(MODES.index(self.active) + direction) % len(MODES)])

        @self.keys.add("tab", eager=True, filter=can_switch)
        def next_tab(event):
            switch(1)

        @self.keys.add("s-tab", eager=True, filter=can_switch)
        def previous_tab(event):
            switch(-1)

        @self.keys.add("c-o", eager=True, filter=Condition(lambda: not self.processing))
        def language(event):
            self.open_language()

        for key in ("c-c", "<sigint>"):
            self.keys.add(key, eager=True, filter=Condition(lambda: not self.processing))(
                lambda event: self.app.terminate(result=130)
            )
        self.app = TerminalApplication(
            self,
            layout=Layout(Window()),
            key_bindings=self.keys,
            style=STYLE,
            full_screen=True,
            refresh_interval=0.1,
        )

    def page(self, **kwargs):
        return Page(self, **kwargs)

    def begin(self, mode, inputs):
        record = Record(mode, inputs)
        record.stage = (
            "stage.fetching_information" if mode == "download" else "stage.reading_information"
        )
        record.title = next(iter(inputs.values()), "")
        self.records.insert(0, record)
        self.current_records[mode] = record
        self.notices.pop(mode, None)
        return record

    def record_event(self, event):
        if record := self.current_records.get(_MODE.get()):
            record.update(event)

    def save_input(self, page):
        controls = [c for c in page.layout.find_all_controls() if isinstance(c, BufferControl)]
        if controls:
            focused = page.layout.current_control
            self.input_snapshots[page.mode] = (
                [c.buffer.document for c in controls],
                controls.index(focused) if focused in controls else 0,
            )

    def restore_input(self, page):
        controls = [c for c in page.layout.find_all_controls() if isinstance(c, BufferControl)]
        if controls and page.mode in self.input_snapshots:
            documents, focused = self.input_snapshots[page.mode]
            for control, document in zip(controls, documents):
                if control.buffer.text == document.text:
                    control.buffer.document = document
            if focused < len(controls) and all(
                c.buffer.text == d.text for c, d in zip(controls, documents)
            ):
                page.layout.focus(controls[focused])

    def capture_focus(self):
        if self.modal is None and (page := self.pages.get(self.active)):
            focused = self.app.layout.current_control
            if focused.is_focusable() and focused in page.layout.find_all_controls():
                page.layout.focus(focused)

    def activate(self, mode):
        if self.processing:
            return
        self.capture_focus()
        self.active = mode
        self.modal, self.modal_stack = None, []
        self.show()

    def notification(self):
        value = self.notices.get(self.active)
        return value.notification() if isinstance(value, Record) else value or ""

    def show(self):
        from .ui import Label, navigation

        if self.modal:
            layout, keys = self.modal
        elif self.active == "tasks":
            if self.task_view is None:
                self.task_view = self.task_list()
            layout, keys = self.task_view
        elif page := self.pages.get(self.active):
            layout, keys = page.layout, page.key_bindings
        else:
            return
        children = (
            list(layout.container.children)
            if isinstance(layout.container, HSplit)
            else [layout.container]
        )
        footer = children.pop() if len(children) > 1 else Window(height=0)
        body = HSplit(children + [Window(height=Dimension(min=0))])
        notice = ConditionalContainer(
            Label(self.notification), filter=Condition(lambda: bool(self.notification()))
        )
        content = HSplit([body, notice, footer])

        def header():
            size = self.app.output.get_size()
            roomy = (
                size.columns >= 70
                and size.rows >= 24
                and required_height(content, size.columns, size.rows) + 8 <= size.rows
            )
            title = (
                "\n█▄█ ▀█▀ █▀▄ █▀█ █▀▀ █▄▀\n █   █  █ █ █ █ █   █▀▄\n ▀   ▀  ▀▀  ▀▀▀ ▀▀▀ ▀ ▀\n\n"
                if roomy
                else "YTDock\n"
            )
            return (
                [("class:title", title)] + navigation(self.active) + ([("", "\n")] if roomy else [])
            )

        normal = HSplit(
            [
                Window(
                    FormattedTextControl(header),
                    align=WindowAlign.CENTER,
                    height=lambda: Dimension.exact(fragment_list_to_text(header()).count("\n") + 1),
                    wrap_lines=False,
                ),
                content,
            ]
        )

        def too_small():
            size = self.app.output.get_size()
            return (
                size.columns < 52
                or size.rows < 20
                or required_height(normal, size.columns, size.rows) > size.rows
            )

        root = ConditionalContainer(
            normal,
            filter=Condition(lambda: not too_small()),
            alternative_content=Window(
                FormattedTextControl(lambda: t("screen.too_small")), wrap_lines=True
            ),
        )
        focus = layout.current_control
        self.app.layout = Layout(root)
        if focus.is_focusable() and focus in self.app.layout.find_all_controls():
            self.app.layout.focus(focus)
        self.app.key_bindings = merge_key_bindings([keys, self.keys])
        self.app.invalidate()

    def open_modal(self, layout, keys):
        self.capture_focus()
        self.modal_stack.append(self.modal)
        self.modal = (layout, keys)
        self.show()

    def close_modal(self):
        self.modal = self.modal_stack.pop() if self.modal_stack else None
        self.show()

    def open_language(self):
        if self.modal and getattr(self.modal[0], "language_picker", False):
            return
        from .ui import Label, choice_window, shortcuts

        selected = LANGUAGES.index(get_language())
        keys = KeyBindings()

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
            self.close_modal()

        @keys.add("escape")
        def cancel(event):
            self.close_modal()

        def rows():
            result = []
            for index, code in enumerate(LANGUAGES):
                if index == selected:
                    result.append(("[SetCursorPosition]", ""))
                result.append(
                    (
                        "class:selected" if index == selected else "",
                        ("❯ " if index == selected else "  ")
                        + language_name(code)
                        + ("\n" if index + 1 < len(LANGUAGES) else ""),
                    )
                )
            return result

        control = FormattedTextControl(rows, focusable=True, show_cursor=False)
        layout = Layout(
            HSplit(
                [
                    Label(lambda: t("language.select_title")),
                    choice_window(control),
                    shortcuts(lambda: t("language.select_hint")),
                ]
            ),
            focused_element=control,
        )
        layout.language_picker = True
        self.open_modal(layout, keys)

    def task_list(self):
        from .core import safe_text
        from .ui import Label, shortcuts

        keys = KeyBindings()

        @keys.add("up")
        def up(event):
            self.task_index = max(0, self.task_index - 1)

        @keys.add("down")
        def down(event):
            self.task_index = min(max(0, len(self.records) - 1), self.task_index + 1)

        @keys.add("enter")
        def details(event):
            if self.records:
                self.task_view = self.task_detail(self.records[self.task_index])
                self.show()

        def rows():
            if not self.records:
                return t("task.empty")
            result = []
            for index, record in enumerate(self.records):
                if index == self.task_index:
                    result.append(("[SetCursorPosition]", ""))
                result.append(
                    (
                        "class:selected" if index == self.task_index else "",
                        ("❯ " if index == self.task_index else "  ")
                        + record.summary()
                        + " · "
                        + safe_text(record.title)
                        + ("\n" if index + 1 < len(self.records) else ""),
                    )
                )
            return result

        control = FormattedTextControl(rows, focusable=True, show_cursor=False)
        return Layout(
            HSplit(
                [
                    Label(lambda: t("mode.tasks")),
                    Window(control, wrap_lines=True),
                    shortcuts(lambda: t("task.list_hint")),
                ]
            ),
            focused_element=control,
        ), keys

    def task_detail(self, record):
        from .ui import shortcuts

        control, keys = DetailControl(record), KeyBindings()

        @keys.add("up")
        def up(event):
            control.row = max(0, control.row - 1)

        @keys.add("down")
        def down(event):
            control.row = min(control.count - 1, control.row + 1)

        @keys.add("pageup")
        def pageup(event):
            control.row = max(0, control.row - max(1, self.app.output.get_size().rows - 4))

        @keys.add("pagedown")
        def pagedown(event):
            control.row = min(
                control.count - 1, control.row + max(1, self.app.output.get_size().rows - 4)
            )

        @keys.add("escape")
        def back(event):
            self.task_view = self.task_list()
            self.show()

        return Layout(
            HSplit([Window(control, wrap_lines=True), shortcuts(lambda: t("task.detail_hint"))]),
            focused_element=control,
        ), keys

    async def run(self):
        from .workflow import run_mode

        token = _CURRENT.set(self)
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()

        def handle_exception(loop, context):
            self.app.terminate(
                exception=context.get("exception") or RuntimeError("UI callback failed")
            )

        loop.set_exception_handler(handle_exception)

        async def feature(mode):
            _MODE.set(mode)
            try:
                await run_mode(self, mode)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                self.app.terminate(exception=exc)

        def start():
            self.tasks = [
                asyncio.create_task(feature(mode)) for mode in ("download", "compress", "subtitles")
            ]

        try:
            return await self.app.run_async(pre_run=start, set_exception_handler=False)
        finally:
            for task in self.tasks:
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            loop.set_exception_handler(previous_handler)
            _CURRENT.reset(token)


async def run(paths, downloads, lock):
    return await Screen(paths, downloads, lock).run()
