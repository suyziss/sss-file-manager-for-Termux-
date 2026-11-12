#!/usr/bin/env python3
"""SSS File Manager for Termux — standalone terminal file manager."""
from __future__ import annotations

import os
import shutil
import stat
import sys
import termios
import tty
from datetime import datetime
from pathlib import Path

RESET = "\033[0m"
CYAN = "\033[1;36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
DIM = "\033[2m"
REVERSE = "\033[7m"
CLEAR = "\033[2J\033[H"


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if value < 1024 or unit == "ТБ":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} Б"


def read_key() -> str:
    """Read one key, including common terminal arrow-key sequences."""
    first = os.read(sys.stdin.fileno(), 1).decode("utf-8", errors="replace")
    if first != "\x1b":
        return first
    import select
    if not select.select([sys.stdin], [], [], 0.035)[0]:
        return "ESC"
    second = os.read(sys.stdin.fileno(), 1).decode("utf-8", errors="replace")
    if second != "[":
        return "ESC"
    if not select.select([sys.stdin], [], [], 0.035)[0]:
        return "ESC"
    third = os.read(sys.stdin.fileno(), 1).decode("utf-8", errors="replace")
    return {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT",
            "H": "HOME", "F": "END"}.get(third, "ESC")


class FileManager:
    def __init__(self, start: str | None = None) -> None:
        self.current = Path(start or Path.cwd()).expanduser().resolve()
        if not self.current.is_dir():
            self.current = Path.home()
        self.entries: list[Path] = []
        self.index = 0
        self.offset = 0
        self.show_hidden = False
        self.message = "Стрелки — навигация   ? — помощь   Q — выход"
        self.running = True
        self.original_terminal = None

    def scan(self) -> None:
        try:
            entries = list(self.current.iterdir())
            if not self.show_hidden:
                entries = [p for p in entries if not p.name.startswith(".")]
            entries.sort(key=lambda p: (not p.is_dir(), p.name.casefold()))
            self.entries = entries
            self.index = min(self.index, max(0, len(entries) - 1))
        except OSError as exc:
            self.entries = []
            self.message = f"Не удалось прочитать папку: {exc}"

    def dimensions(self) -> tuple[int, int]:
        size = shutil.get_terminal_size((80, 24))
        return max(40, size.columns), max(10, size.lines)

    def draw(self) -> None:
        width, height = self.dimensions()
        visible = max(1, height - 7)
        self.offset = min(self.offset, max(0, len(self.entries) - visible))
        if self.index < self.offset:
            self.offset = self.index
        elif self.index >= self.offset + visible:
            self.offset = self.index - visible + 1

        out = [CLEAR, f"{CYAN} SSS File Manager {RESET}  {DIM}Termux / Android{RESET}\n"]
        out.append(f"{YELLOW} Путь: {self.current}{RESET}\n")
        out.append(f"{DIM}{'─' * width}{RESET}\n")
        if not self.entries:
            out.append("\n  Папка пуста или нет доступа.\n")
        else:
            for row, path in enumerate(self.entries[self.offset:self.offset + visible]):
                real_index = self.offset + row
                try:
                    is_dir = path.is_dir()
                    if path.is_symlink():
                        kind = "Ссылка"
                    elif is_dir:
                        kind = "Папка"
                    else:
                        kind = "Файл"
                    size = "—" if is_dir else human_size(path.stat().st_size)
                    modified = datetime.fromtimestamp(path.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
                except OSError:
                    kind, size, modified = "Недоступно", "—", "—"
                icon = "▸" if is_dir else "·"
                name = path.name + ("/" if is_dir else "")
                limit = max(12, width - 43)
                if len(name) > limit:
                    name = name[:limit - 1] + "…"
                line = f" {icon} {name:<{limit}} {kind:<11} {size:>10}  {modified:>16}"
                if real_index == self.index:
                    out.append(f"{REVERSE}{line[:width]:<{width}}{RESET}\n")
                else:
                    color = GREEN if is_dir else RESET
                    out.append(f"{color}{line[:width]}{RESET}\n")
        out.append(f"{DIM}{'─' * width}{RESET}\n")
        out.append(f" {self.message[:width - 2]}\n")
        out.append(f" {CYAN}↑/↓{RESET} выбор  {CYAN}←{RESET} назад  {CYAN}→/Enter{RESET} открыть  "
                   f"{CYAN}N{RESET} папка  {CYAN}F{RESET} файл\n")
        out.append(f" {CYAN}R{RESET} переименовать  {CYAN}D{RESET} удалить  "
                   f"{CYAN}.{RESET} скрытые  {CYAN}?{RESET} помощь  {CYAN}Q{RESET} выход\n")
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    def prompt(self, label: str, default: str = "") -> str:
        self.restore_terminal()
        try:
            sys.stdout.write(f"\n{label}")
            if default:
                sys.stdout.write(f" [{default}]")
            sys.stdout.write(": ")
            sys.stdout.flush()
            value = input().strip()
            return value or default
        except (EOFError, KeyboardInterrupt):
            return ""
        finally:
            self.enter_terminal()

    def confirm(self, question: str) -> bool:
        answer = self.prompt(question + " [д/Н]").lower()
        return answer in ("д", "да", "y", "yes")

    def restore_terminal(self) -> None:
        if self.original_terminal is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self.original_terminal)
            self.original_terminal = None
            sys.stdout.write("\033[?25h")
            sys.stdout.flush()

    def enter_terminal(self) -> None:
        if not sys.stdin.isatty():
            raise RuntimeError("Нужен интерактивный терминал.")
        if self.original_terminal is None:
            self.original_terminal = termios.tcgetattr(sys.stdin.fileno())
            tty.setraw(sys.stdin.fileno())
            sys.stdout.write("\033[?25l")
            sys.stdout.flush()

    def open_selected(self) -> None:
        if not self.entries:
            return
        path = self.entries[self.index]
        try:
            if path.is_dir():
                self.current = path.resolve()
                self.index = self.offset = 0
                self.scan()
                self.message = f"Открыта папка: {path.name}"
            else:
                self.preview(path)
        except OSError as exc:
            self.message = f"Ошибка: {exc}"

    def preview(self, path: Path) -> None:
        self.restore_terminal()
        print(f"\n--- {path.name} ---")
        try:
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                for number, line in enumerate(stream):
                    if number >= 160:
                        print("\n... показаны первые 160 строк")
                        break
                    print(f"{number + 1:4} | {line.rstrip()}")
            input("\nEnter — назад...")
        except (OSError, UnicodeError) as exc:
            input(f"Не удалось открыть файл: {exc}\nEnter — назад...")
        finally:
            self.enter_terminal()

    def create_item(self, directory: bool) -> None:
        name = self.prompt("Имя новой папки" if directory else "Имя нового файла")
        if not name:
            return
        if name in (".", "..") or "/" in name or "\0" in name:
            self.message = "Недопустимое имя."
            return
        target = self.current / name
        try:
            if target.exists() or target.is_symlink():
                self.message = "Такой объект уже существует."
                return
            target.mkdir() if directory else target.touch(exist_ok=False)
            self.scan()
            self.message = f"Создано: {name}"
        except OSError as exc:
            self.message = f"Не удалось создать: {exc}"

    def rename_selected(self) -> None:
        if not self.entries:
            return
        source = self.entries[self.index]
        new_name = self.prompt("Новое имя", source.name)
        if not new_name or new_name == source.name:
            return
        if new_name in (".", "..") or "/" in new_name or "\0" in new_name:
            self.message = "Недопустимое имя."
            return
        target = source.with_name(new_name)
        try:
            if target.exists() or target.is_symlink():
                self.message = "Объект с таким именем уже существует."
                return
            source.rename(target)
            self.scan()
            self.message = f"Переименовано: {new_name}"
        except OSError as exc:
            self.message = f"Не удалось переименовать: {exc}"

    def delete_selected(self) -> None:
        if not self.entries:
            return
        target = self.entries[self.index]
        try:
            if target.is_dir() and not target.is_symlink():
                if any(target.iterdir()):
                    self.message = "Папка не пуста. Удаление папок с содержимым отключено."
                    return
            if not self.confirm(f"Удалить «{target.name}»?"):
                self.message = "Удаление отменено."
                return
            if target.is_dir() and not target.is_symlink():
                target.rmdir()
            else:
                target.unlink()
            self.index = max(0, self.index - 1)
            self.scan()
            self.message = f"Удалено: {target.name}"
        except OSError as exc:
            self.message = f"Не удалось удалить: {exc}"

    def help_screen(self) -> None:
        self.restore_terminal()
        print("""
 SSS File Manager — справка

 ↑ / ↓       Переместить выделение
 ←           Перейти в родительскую папку
 → / Enter   Открыть папку или просмотр файла
 Backspace   Перейти назад
 N           Создать папку
 F           Создать пустой файл
 R           Переименовать выбранный объект
 D           Удалить файл или пустую папку (с подтверждением)
 .           Показать / скрыть скрытые файлы
 Q           Выход

Файлы открываются в текстовом предпросмотре (до 160 строк).
Непустые папки не удаляются — это защита от случайной потери данных.
""")
        input("Enter — вернуться...")
        self.enter_terminal()

    def run(self) -> int:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            print("Ошибка: запусти файловый менеджер непосредственно в Termux.")
            return 2
        self.scan()
        try:
            self.enter_terminal()
            while self.running:
                self.draw()
                key = read_key()
                if key in ("q", "Q", "\x03"):
                    self.running = False
                elif key == "UP":
                    self.index = max(0, self.index - 1)
                elif key == "DOWN":
                    self.index = min(max(0, len(self.entries) - 1), self.index + 1)
                elif key in ("LEFT", "\x7f", "\b"):
                    parent = self.current.parent
                    if parent != self.current:
                        old = self.current
                        self.current = parent
                        self.scan()
                        try:
                            self.index = next(i for i, p in enumerate(self.entries) if p.resolve() == old)
                        except (StopIteration, OSError):
                            self.index = 0
                        self.message = "Родительская папка"
                elif key in ("RIGHT", "\r", "\n"):
                    self.open_selected()
                elif key in ("n", "N"):
                    self.create_item(True)
                elif key in ("f", "F"):
                    self.create_item(False)
                elif key in ("r", "R"):
                    self.rename_selected()
                elif key in ("d", "D"):
                    self.delete_selected()
                elif key == ".":
                    self.show_hidden = not self.show_hidden
                    self.scan()
                    self.message = "Скрытые файлы показаны" if self.show_hidden else "Скрытые файлы скрыты"
                elif key == "?":
                    self.help_screen()
                elif key == "HOME":
                    self.index = 0
                elif key == "END":
                    self.index = max(0, len(self.entries) - 1)
        finally:
            self.restore_terminal()
            sys.stdout.write(CLEAR)
            sys.stdout.flush()
        print("Файловый менеджер закрыт.")
        return 0


def main() -> int:
    start = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        return FileManager(start).run()
    except (OSError, RuntimeError) as exc:
        print(f"Ошибка запуска: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
