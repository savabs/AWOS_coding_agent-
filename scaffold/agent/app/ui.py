"""
ui.py — terminal output for the app: colours, a bottom status line, printing
above a pending prompt, single-key reads and a pager. Plain text when the
output is not a terminal (or NO_COLOR is set / TERM=dumb).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import threading
from typing import Optional, TextIO

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_STYLES = {
    "bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "blue": "34",
    "magenta": "35", "cyan": "36", "grey": "90",
}

_MAX_HISTORY = 200


class Term:
    def __init__(self, out: Optional[TextIO] = None, inp: Optional[TextIO] = None,
                 force_plain: bool = False):
        self.out = out or sys.stdout
        self.inp = inp or sys.stdin
        self.tty = (not force_plain and _isatty(self.out) and _isatty(self.inp)
                    and os.getenv("TERM", "") != "dumb")
        self.color = self.tty and not os.getenv("NO_COLOR")
        self.lock = threading.RLock()
        self.prompt: Optional[str] = None     # set while a line is being typed
        self._buf: list[str] = []              # the line being typed (we own it: no readline)
        self._cur = 0
        self._typeahead = ""                   # pasted text after a newline
        self.history: list[str] = []
        self._status = ""                      # bottom status line (watch mode)
        self._frame = 0

    # ── styling ──────────────────────────────────────────────────────────
    def s(self, text: str, *styles: str) -> str:
        if not self.color or not styles:
            return text
        codes = ";".join(_STYLES[s] for s in styles if s in _STYLES)
        return f"\x1b[{codes}m{text}\x1b[0m" if codes else text

    def spin(self) -> str:
        self._frame = (self._frame + 1) % len(SPINNER)
        return SPINNER[self._frame] if self.tty else "*"

    def width(self) -> int:
        return shutil.get_terminal_size((100, 24)).columns

    # ── output ───────────────────────────────────────────────────────────
    def _write(self, text: str) -> None:
        try:
            self.out.write(text)
            self.out.flush()
        except (OSError, ValueError):
            pass

    def line(self, text: str = "") -> None:
        """Print a line, keeping a pending prompt or status line intact below it."""
        with self.lock:
            if not self.tty:
                self._write(text + "\n")
                return
            if self.prompt is not None:
                self._write("\r\x1b[2K" + text + "\n")
                self._redraw()
                return
            pre = "\r\x1b[2K" if self._status else ""
            tail = self._fit(self._status) if self._status else ""
            self._write(pre + text + "\n" + tail)

    def status(self, text: str) -> None:
        """Redraw the bottom status line (TTY only; plain mode prints nothing)."""
        with self.lock:
            self._status = text
            if self.tty and self.prompt is None:
                self._write("\r\x1b[2K" + self._fit(text))

    def clear_status(self) -> None:
        with self.lock:
            if self.tty and self._status:
                self._write("\r\x1b[2K")
            self._status = ""

    def _fit(self, text: str) -> str:
        # Keep the status line on one row; count only visible characters.
        from .progress import strip_ansi
        width = self.width() - 1
        if len(strip_ansi(text)) <= width:
            return text
        plain = strip_ansi(text)
        return plain[: max(0, width - 1)] + "…"

    # ── input ────────────────────────────────────────────────────────────
    def set_prompt(self, prompt: str) -> None:
        """Update a prompt that is already waiting (e.g. the "[1 working]" count)."""
        with self.lock:
            if self.tty and self.prompt is not None and prompt != self.prompt:
                self.prompt = prompt
                self._redraw()

    @contextlib.contextmanager
    def quiet(self):
        """No terminal echo between prompts, so keys typed early do not scribble on output."""
        if not self.tty:
            yield
            return
        import termios
        fd = self.inp.fileno()
        try:
            old = termios.tcgetattr(fd)
            new = termios.tcgetattr(fd)
            new[3] &= ~termios.ECHO
            termios.tcsetattr(fd, termios.TCSANOW, new)
        except termios.error:
            yield
            return
        try:
            yield
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    def ask(self, prompt: str) -> str:
        """One line of input; raises EOFError / KeyboardInterrupt."""
        with self.lock:
            self.clear_status()
        if self.tty:
            return self._edit_line(prompt)
        line = self.inp.readline()
        if not line:
            raise EOFError
        line = line.rstrip("\n")
        # Piped input is not echoed: print prompt + line so a transcript reads naturally.
        with self.lock:
            self._write(prompt + line + "\n")
        return line

    # A small line editor instead of readline: macOS ships libedit, whose line
    # buffer cannot be read back reliably while another thread prints, so
    # progress lines would garble what the user is typing.
    def _redraw(self) -> None:
        from .progress import strip_ansi
        prompt = self.prompt or ""
        avail = max(10, self.width() - len(strip_ansi(prompt)) - 1)
        text, cur = "".join(self._buf), self._cur
        start = 0 if len(text) <= avail else max(0, min(cur - avail + 1, len(text) - avail))
        shown = text[start:start + avail]
        back = len(shown) - (cur - start)
        self._write("\r\x1b[2K" + prompt + shown + (f"\x1b[{back}D" if back > 0 else ""))

    def _edit_line(self, prompt: str) -> str:
        import termios
        import tty
        fd = self.inp.fileno()
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd, termios.TCSANOW)   # ISIG stays on: Ctrl-C still raises KeyboardInterrupt
        hist = len(self.history)
        with self.lock:
            self.prompt, self._buf, self._cur = prompt, [], 0
            self._redraw()
        pending = b""
        try:
            while True:
                if self._typeahead:
                    text, self._typeahead = self._typeahead, ""
                else:
                    data = os.read(fd, 4096)
                    if not data:
                        raise EOFError
                    pending += data
                    try:
                        text, pending = pending.decode("utf-8"), b""
                    except UnicodeDecodeError as exc:   # a split multi-byte character
                        text, pending = pending[:exc.start].decode("utf-8", "replace"), pending[exc.start:]
                i = 0
                with self.lock:
                    while i < len(text):
                        ch = text[i]
                        i += 1
                        if ch in "\r\n":
                            line = "".join(self._buf)
                            self._typeahead = text[i:].lstrip("\n")
                            self._cur = len(self._buf)
                            self._redraw()
                            self._write("\n")
                            self.prompt = None
                            if line.strip() and (not self.history or self.history[-1] != line):
                                self.history = (self.history + [line])[-_MAX_HISTORY:]
                            return line
                        if ch == "\x04":                       # Ctrl-D
                            if not self._buf:
                                self._write("\n")
                                raise EOFError
                            if self._cur < len(self._buf):
                                del self._buf[self._cur]
                        elif ch == "\x03":
                            raise KeyboardInterrupt
                        elif ch in ("\x7f", "\x08"):           # backspace
                            if self._cur > 0:
                                del self._buf[self._cur - 1]
                                self._cur -= 1
                        elif ch == "\x15":                     # Ctrl-U
                            del self._buf[:self._cur]
                            self._cur = 0
                        elif ch == "\x0b":                     # Ctrl-K
                            del self._buf[self._cur:]
                        elif ch == "\x17":                     # Ctrl-W
                            j = self._cur
                            while j > 0 and self._buf[j - 1] == " ":
                                j -= 1
                            while j > 0 and self._buf[j - 1] != " ":
                                j -= 1
                            del self._buf[j:self._cur]
                            self._cur = j
                        elif ch == "\x01":                     # Ctrl-A
                            self._cur = 0
                        elif ch == "\x05":                     # Ctrl-E
                            self._cur = len(self._buf)
                        elif ch == "\x1b":                     # arrows, Home/End, Delete
                            seq = ""
                            if i < len(text) and text[i] in "[O":
                                j = i + 1
                                while j < len(text) and not (text[j].isalpha() or text[j] == "~"):
                                    j += 1
                                seq, i = text[i:j + 1], j + 1
                            final = seq[-1:] if seq else ""
                            if final == "D":
                                self._cur = max(0, self._cur - 1)
                            elif final == "C":
                                self._cur = min(len(self._buf), self._cur + 1)
                            elif final in ("H",) or seq == "[1~":
                                self._cur = 0
                            elif final in ("F",) or seq == "[4~":
                                self._cur = len(self._buf)
                            elif seq == "[3~" and self._cur < len(self._buf):
                                del self._buf[self._cur]
                            elif final in ("A", "B") and self.history:
                                hist = max(0, hist - 1) if final == "A" else min(len(self.history), hist + 1)
                                self._buf = list(self.history[hist]) if hist < len(self.history) else []
                                self._cur = len(self._buf)
                        elif ch == "\t":
                            self._buf.insert(self._cur, " ")
                            self._cur += 1
                        elif ch >= " ":
                            self._buf.insert(self._cur, ch)
                            self._cur += 1
                    self._redraw()
        except KeyboardInterrupt:
            with self.lock:
                self._write("\n")
            raise
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            with self.lock:
                self.prompt = None

    def key(self, prompt: str, choices: str) -> str:
        """A single keypress from `choices` (TTY), or the first letter of a line."""
        if not self.tty:
            line = self.inp.readline()
            if not line:
                raise EOFError
            ch = line.strip()[:1].lower()
            self._write(prompt + ch + "\n")
            return ch if ch in choices else ""
        self._write(prompt)
        import termios
        import tty
        fd = self.inp.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd, termios.TCSANOW)   # TCSAFLUSH would drop type-ahead
            while True:
                ch = os.read(fd, 1).decode(errors="ignore").lower()
                if ch == "\x03":
                    raise KeyboardInterrupt
                if ch == "\x04":
                    raise EOFError
                if ch in ("\r", "\n"):
                    ch = ""
                if ch == "" or ch in choices:
                    self._write((ch or "") + "\n")
                    return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    def confirm(self, question: str) -> bool:
        try:
            return self.key(f"{question} [y/N] ", "yn") == "y"
        except (EOFError, KeyboardInterrupt):
            self._write("\n")
            return False

    def page(self, text: str) -> None:
        """Show long text through `less -R` on a terminal, else print it."""
        rows = shutil.get_terminal_size((100, 24)).lines
        if self.tty and text.count("\n") > rows - 3 and shutil.which("less"):
            with self.lock:
                self.clear_status()
                try:
                    subprocess.run(["less", "-R", "-F", "-X"], input=text, text=True)
                    return
                except (OSError, subprocess.SubprocessError):
                    pass
        with self.lock:
            self.clear_status()
            self._write(text if text.endswith("\n") else text + "\n")


def _isatty(stream) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False
