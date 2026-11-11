"""Запуск утилиты под панелью.

Панель запускает команды не напрямую, а через этот модуль. Он ничего не
меняет в логике утилиты, только перехватывает два места, где она ждёт
человека:

* input(): перед чтением ответа печатает служебную метку с текстом вопроса,
  чтобы панель показала нормальное поле ввода, а не строку в логе;
* ввод капчи в терминале: картинку сохраняем в файл и отдаём панели,
  вместо печати через kitty/sixel, которые браузер показать не может.

Метка: ESC ] hhp ; <json> BEL. Панель вырезает её из лога.

Секретные аргументы (пароль) приходят через HH_PANEL_SECRET_ARGV и
дописываются в argv, чтобы не попасть в историю задач и в список
процессов.
"""

from __future__ import annotations

import builtins
import json
import os
import sys
import uuid
from pathlib import Path

MARK_START = "\x1b]hhp;"
MARK_END = "\x07"


def emit(payload: dict) -> None:
    sys.stdout.write(MARK_START + json.dumps(payload, ensure_ascii=False) + MARK_END)
    sys.stdout.flush()


CAPTCHA_WAIT = 600  # сек: капчу ждём 10 минут, потом пропускаем вакансию
CAPTCHA_TAG = "#captcha"


def captcha_caption(name: str) -> str:
    return (
        "🧩 hh просит капчу. Ответьте на это сообщение текстом с картинки "
        "или введите его в панели. Через 10 минут вакансия будет пропущена\n"
        f"{CAPTCHA_TAG} {name}"
    )


def _read_answer(timeout: float | None = None) -> str:
    if timeout is not None:
        import select

        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if not ready:
            raise TimeoutError("Капчу не ввели за 10 минут, пропускаю")
    line = sys.stdin.readline()
    if not line:
        raise EOFError
    return line.rstrip("\n")


def panel_input(prompt: object = "") -> str:
    text = str(prompt)
    sys.stdout.write(text)
    sys.stdout.flush()
    low = text.lower()
    # Пароль и одноразовый код не попадают в лог задачи
    secret = "парол" in low or "код" in low
    emit({"kind": "input", "prompt": text.strip(), "secret": secret})
    answer = _read_answer()
    emit({"kind": "answered"})
    return answer


def _patch_captcha() -> None:
    from ..tool import HHApplicantTool

    captcha_dir = Path(os.environ.get("HH_PANEL_CAPTCHA_DIR") or ".")

    def prompt_captcha(self, image_data: bytes) -> str:  # noqa: ARG001
        captcha_dir.mkdir(parents=True, exist_ok=True)
        path = captcha_dir / f"{uuid.uuid4().hex}.png"
        path.write_bytes(image_data)
        emit({"kind": "captcha", "prompt": "Введите текст с картинки", "image": path.name})
        # Ответить можно и в панели, и реплаем в Telegram: бот найдёт задачу по метке
        try:
            from .. import notify

            notify.send_photo(self.config, image_data, captcha_caption(path.stem))
        except Exception:  # noqa: BLE001
            pass
        try:
            answer = _read_answer(CAPTCHA_WAIT)
        finally:
            emit({"kind": "answered"})
            try:
                path.unlink()
            except OSError:
                pass
        return answer.strip()

    HHApplicantTool._prompt_captcha_in_terminal = prompt_captcha  # type: ignore[method-assign]
    HHApplicantTool._can_use_tkinter = lambda self: False  # type: ignore[method-assign]


def main() -> int | None:
    builtins.input = panel_input
    _patch_captcha()
    secret = os.environ.pop("HH_PANEL_SECRET_ARGV", None)
    argv = sys.argv[1:]
    if secret:
        try:
            argv += [str(x) for x in json.loads(secret)]
        except ValueError:
            pass
    from ..main import main as tool_main

    return tool_main(argv)


if __name__ == "__main__":
    sys.exit(main())
