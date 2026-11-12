#!/usr/bin/env python3
"""SSS File Manager — minimal keyboard-driven file manager for Termux."""
from __future__ import annotations

import argparse
import os
import select
import shlex
import shutil
import sys
import termios
import tty
from pathlib import Path

VERSION = "1.1.0"
RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
CYAN = "\033[36m"
REVERSE = "\033[7m"
CLEAR = "\033[2J\033[H"


def read_key() -> str:
    first = os.read(sys.stdin.fileno(), 1).decode("utf-8", "replace")
    if first != "\x1b":
        return first
    if not select.select([sys.stdin], [], [], 0.04)[0]:
        return "ESC"
    second = os.read(sys.stdin.fileno(), 1).decode("utf-8", "replace")
    if second not in ("[", "O"):
        return "ESC"
    if not select.select([sys.stdin], [], [], 0.04)[0]:
        return "ESC"
    third = os.read(sys.stdin.fileno(), 1).decode("utf-8", "replace")
    return {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT",
            "H": "HOME", "F": "END"}.get(third, "ESC")


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "K", "M", "G", "T"):
        if value < 1024 or unit == "T":
            return f"{value:4.0f}{unit}" if unit == "B" else f"{value:4.1f}{unit}"
        value /= 1024
    return str(size)


class FileManager:
    def __init__(self, start: str | None = None):
        target = Path(start or Path.cwd()).expanduser()
        try:
            self.current = target.resolve(strict=True)
            if not self.current.is_dir():
                raise NotADirectoryError(str(target))
        except (OSError, RuntimeError):
            print(f"sss: нет такой папки: {target}", file=sys.stderr)
            raise
        self.entries: list[Path] = []
        self.index = 0
        self.offset = 0
        self.hidden = False
        self.status = ""
        self.original_terminal = None

    def scan(self):
        try:
            entries = list(self.current.iterdir())
            if not self.hidden:
                entries = [p for p in entries if not p.name.startswith(".")]
            entries.sort(key=lambda p: (not p.is_dir(), p.name.casefold()))
            self.entries = entries
            self.index = min(self.index, max(0, len(entries) - 1))
        except OSError as exc:
            self.entries = []
            self.status = str(exc)

    def terminal_size(self):
        size = shutil.get_terminal_size((80, 24))
        return max(20, size.columns), max(8, size.lines)

    def draw(self):
        width, height = self.terminal_size()
        visible = max(1, height - 4)
        self.offset = min(self.offset, max(0, len(self.entries) - visible))
        if self.index < self.offset:
            self.offset = self.index
        elif self.index >= self.offset + visible:
            self.offset = self.index - visible + 1

        title = f" {self.current}"
        if len(title) > width:
            title = " …" + title[-(width - 3):]
        out = [CLEAR, f"{BOLD}sss{RESET} {DIM}{title.strip()}{RESET}\n"]
        out.append(f"{DIM}{'─' * width}{RESET}\n")
        if not self.entries:
            out.append(f"{DIM}  (пусто){RESET}\n")
        else:
            name_width = max(8, width - 13)
            for row, path in enumerate(self.entries[self.offset:self.offset + visible]):
                idx = self.offset + row
                try:
                    is_dir = path.is_dir()
                    is_link = path.is_symlink()
                    size = " dir" if is_dir else human_size(path.stat().st_size)
                    marker = "/" if is_dir else "@" if is_link else ""
                except OSError:
                    size, marker = "  ?", ""
                name = path.name + marker
                if len(name) > name_width:
                    name = name[:max(1, name_width - 1)] + "…"
                line = f"  {name:<{name_width}} {size:>5}"
                line = line[:width].ljust(width)
                if idx == self.index:
                    out.append(f"{REVERSE}{line}{RESET}\n")
                else:
                    out.append(line + "\n")
        out.append(f"{DIM}{'─' * width}{RESET}\n")
        hint = "↑↓ move  ← parent  →/↵ open  h hidden  ? help  q quit"
        if width < len(hint):
            hint = "↑↓ ←→  ↵ open  h hidden  ? help  q quit"
        out.append(f"{DIM}{hint[:width]}{RESET}\n")
        if self.status:
            out.append(f"{CYAN}{self.status[:width]}{RESET}\n")
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    def restore_terminal(self):
        if self.original_terminal is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.original_terminal)
            self.original_terminal = None
            sys.stdout.write("\033[?25h")
            sys.stdout.flush()

    def enter_terminal(self):
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise RuntimeError("нужен интерактивный терминал")
        if self.original_terminal is None:
            self.original_terminal = termios.tcgetattr(sys.stdin.fileno())
            tty.setraw(sys.stdin.fileno())
            sys.stdout.write("\033[?25l")
            sys.stdout.flush()

    def prompt(self, label: str, default: str = "") -> str:
        self.restore_terminal()
        try:
            suffix = f" [{default}]" if default else ""
            value = input(f"\n{label}{suffix}: ").strip()
            return value or default
        except (EOFError, KeyboardInterrupt):
            return ""
        finally:
            self.enter_terminal()

    def selected(self):
        return self.entries[self.index] if self.entries else None

    def open_selected(self):
        path = self.selected()
        if path is None:
            return
        if path.is_dir():
            try:
                self.current = path.resolve()
                self.index = self.offset = 0
                self.scan()
                self.status = ""
            except OSError as exc:
                self.status = str(exc)
        else:
            self.preview(path)

    def preview(self, path: Path):
        self.restore_terminal()
        print(f"\n{BOLD}{path.name}{RESET}\n")
        try:
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                for number, line in enumerate(stream):
                    if number >= 160:
                        print("…")
                        break
                    print(line.rstrip())
            input("\nEnter — назад")
        except OSError as exc:
            input(f"\nsss: {exc}\nEnter — назад")
        finally:
            self.enter_terminal()

    def create(self, directory: bool):
        name = self.prompt("Папка" if directory else "Файл")
        if not name:
            return
        if name in (".", "..") or "/" in name or "\0" in name:
            self.status = "Недопустимое имя"
            return
        target = self.current / name
        try:
            if target.exists() or target.is_symlink():
                self.status = "Уже существует"
                return
            target.mkdir() if directory else target.touch(exist_ok=False)
            self.scan()
            self.status = "Создано"
        except OSError as exc:
            self.status = str(exc)

    def rename(self):
        source = self.selected()
        if source is None:
            return
        name = self.prompt("Новое имя", source.name)
        if not name or name == source.name:
            return
        if name in (".", "..") or "/" in name or "\0" in name:
            self.status = "Недопустимое имя"
            return
        target = source.with_name(name)
        try:
            if target.exists() or target.is_symlink():
                self.status = "Уже существует"
                return
            source.rename(target)
            self.scan()
            self.status = "Переименовано"
        except OSError as exc:
            self.status = str(exc)

    def delete(self):
        target = self.selected()
        if target is None:
            return
        try:
            if target.is_dir() and not target.is_symlink() and any(target.iterdir()):
                self.status = "Непустые папки не удаляются"
                return
        except OSError as exc:
            self.status = str(exc)
            return
        answer = self.prompt(f"Удалить {target.name}? д/Н").lower()
        if answer not in ("д", "да", "y", "yes"):
            self.status = "Отменено"
            return
        try:
            target.rmdir() if target.is_dir() and not target.is_symlink() else target.unlink()
            self.index = max(0, self.index - 1)
            self.scan()
            self.status = "Удалено"
        except OSError as exc:
            self.status = str(exc)

    def help(self):
        self.restore_terminal()
        print("""
sss — клавиши

↑ / ↓       перемещение
←           родительская папка
→ / Enter   открыть папку / файл
Backspace   назад
h           скрытые файлы
n           новая папка
f           новый пустой файл
r           переименовать
d           удалить файл / пустую папку
?           эта справка
q           выход
""")
        input("Enter — назад")
        self.enter_terminal()

    def run(self) -> int:
        self.scan()
        try:
            self.enter_terminal()
            while True:
                self.draw()
                key = read_key()
                self.status = ""
                if key in ("q", "Q", "\x03"):
                    break
                if key == "UP":
                    self.index = max(0, self.index - 1)
                elif key == "DOWN":
                    self.index = min(max(0, len(self.entries) - 1), self.index + 1)
                elif key in ("LEFT", "\x7f", "\b"):
                    parent = self.current.parent
                    if parent != self.current:
                        child = self.current
                        self.current = parent
                        self.scan()
                        try:
                            self.index = next(i for i, p in enumerate(self.entries) if p.resolve() == child)
                        except (StopIteration, OSError):
                            self.index = 0
                elif key in ("RIGHT", "\r", "\n"):
                    self.open_selected()
                elif key in ("h", "H", "."):
                    self.hidden = not self.hidden
                    self.scan()
                    self.status = "Скрытые файлы: " + ("вкл." if self.hidden else "выкл.")
                elif key in ("n", "N"):
                    self.create(True)
                elif key in ("f", "F"):
                    self.create(False)
                elif key in ("r", "R"):
                    self.rename()
                elif key in ("d", "D"):
                    self.delete()
                elif key == "?":
                    self.help()
                elif key == "HOME":
                    self.index = 0
                elif key == "END":
                    self.index = max(0, len(self.entries) - 1)
        finally:
            self.restore_terminal()
            sys.stdout.write(CLEAR)
            sys.stdout.flush()
        return 0


def install_command() -> int:
    prefix = Path(os.environ.get("PREFIX", str(Path.home() / ".local")))
    bindir = prefix / "bin"
    launcher = bindir / "sss"
    script = Path(__file__).resolve()
    python = Path(sys.executable).resolve()
    content = (
        "#!/system/bin/sh\n"
        "# SSS File Manager launcher\n"
        f"exec {shlex.quote(str(python))} {shlex.quote(str(script))} \"$@\"\n"
    )
    try:
        bindir.mkdir(parents=True, exist_ok=True)
        if launcher.exists():
            existing = launcher.read_text(errors="replace")
            if "SSS File Manager launcher" not in existing:
                print(f"sss: команда уже занята: {launcher}", file=sys.stderr)
                return 2
        launcher.write_text(content, encoding="utf-8")
        launcher.chmod(0o755)
    except OSError as exc:
        print(f"sss: установка не удалась: {exc}", file=sys.stderr)
        return 1
    print(f"Установлено: {launcher}")
    print("Запуск: sss")
    return 0


def uninstall_command() -> int:
    prefix = Path(os.environ.get("PREFIX", str(Path.home() / ".local")))
    launcher = prefix / "bin" / "sss"
    try:
        if not launcher.is_file():
            print("Команда sss не установлена.")
            return 0
        if "SSS File Manager launcher" not in launcher.read_text(errors="replace"):
            print("sss: отказ — файл команды не принадлежит SSS File Manager.", file=sys.stderr)
            return 2
        launcher.unlink()
        print("Команда sss удалена. Файлы проекта не затронуты.")
        return 0
    except OSError as exc:
        print(f"sss: удаление не удалось: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="sss", description="Минималистичный файловый менеджер для Termux")
    parser.add_argument("path", nargs="?", help="папка для открытия")
    parser.add_argument("--version", action="version", version=f"sss {VERSION}")
    parser.add_argument("--install", action="store_true", help="установить команду sss")
    parser.add_argument("--uninstall", action="store_true", help="удалить команду sss")
    args = parser.parse_args()
    if args.install and args.uninstall:
        parser.error("--install и --uninstall нельзя использовать вместе")
    if args.install:
        return install_command()
    if args.uninstall:
        return uninstall_command()
    try:
        return FileManager(args.path).run()
    except (OSError, RuntimeError) as exc:
        print(f"sss: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
