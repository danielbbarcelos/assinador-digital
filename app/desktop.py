"""Janela nativa do assinador.

O app é uma página local servida por FastAPI, mas não abre numa aba: sobe o
uvicorn em 127.0.0.1 numa porta livre, numa thread daemon, e aponta uma janela
WebKit (pywebview) para ela.

Por que uma janela e não o browser:

* arrastar o PDF para dentro e posicionar o carimbo é gesto de aplicativo;
* o certificado e a senha ficam fora do histórico, das extensões e do gerenciador
  de senhas do navegador;
* e é a casca em que o suporte a token A3 (PKCS#11) vai caber depois — a leitura
  do token acontece neste processo Python, não no JavaScript.

Sem janela disponível (servidor, SSH sem X), cai para o browser padrão.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
import urllib.error
import urllib.request

import uvicorn

from app.main import app

HOST = "127.0.0.1"  # nunca 0.0.0.0: este app não tem autenticação por design
TITLE = "Assinador"

#: Precisa bater com `StartupWMClass` em sign-manager.desktop.
APP_ID = "assinador-digital"

logger = logging.getLogger("assinador")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def serve(port: int) -> threading.Thread:
    config = uvicorn.Config(
        app,
        host=HOST,
        port=port,
        log_level="warning",
        access_log=False,  # o caminho da URL não interessa e o corpo nunca é logado
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="uvicorn")
    thread.start()
    return thread


def wait_until_up(port: int, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    url = f"http://{HOST}:{port}/api/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.1)
    return False


#: Versão mínima do binding WebKit2 que o pywebview consegue dirigir. A 4.0
#: que o Ubuntu ainda carrega é de 2022 e não tem `evaluate_javascript`: a
#: janela até abre, mas a ponte entre Python e a página fica quebrada.
WEBKIT_MIN = "4.1"

APT_HINT = "sudo apt install gir1.2-webkit2-4.1"
PIP_HINT = "pip install pywebview[qt] qtpy PyQt6-WebEngine"


def qt_available() -> bool:
    """O backend Qt do pywebview vem inteiro pelo pip, sem pacote de sistema.

    Custa ~500 MB (PyQt6-WebEngine embute um Chromium), mas é a saída para quem
    não pode ou não quer instalar nada com apt.
    """
    try:
        import qtpy  # noqa: F401
        from qtpy import QtWebEngineWidgets  # noqa: F401
    except Exception:
        return False
    return True


def webkit_binding() -> str | None:
    """Maior versão de binding WebKit2 disponível para introspecção, ou None."""
    try:
        import gi

        versions = gi.Repository.get_default().enumerate_versions("WebKit2")
    except Exception:  # pragma: no cover - sem PyGObject
        return None
    return max(versions, default=None)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    port = free_port()
    serve(port)
    url = f"http://{HOST}:{port}/"

    if not wait_until_up(port):
        logger.error("O servidor local não subiu a tempo.")
        return 1

    try:
        import webview
    except ImportError:
        webview = None

    if webview is None:
        logger.warning("pywebview não instalado — abrindo no navegador padrão.")
        return _fallback_browser(url)

    # Ordem de preferência: GTK (leve, usa o WebKit do sistema) → Qt (pesado,
    # mas vem todo pelo pip) → navegador (sempre funciona).
    binding = webkit_binding()
    gui = None
    if binding is not None and binding >= WEBKIT_MIN:
        gui = "gtk"
    elif qt_available():
        gui = "qt"
    else:
        logger.warning(
            "Sem um motor de janela utilizável (WebKit2 %s). Para ganhar a "
            "janela nativa, escolha um:\n"
            "    %s          (leve, precisa de sudo)\n"
            "    %s   (~500 MB, sem sudo)\n"
            "Abrindo no navegador por enquanto.",
            binding or "ausente",
            APT_HINT,
            PIP_HINT,
        )
        return _fallback_browser(url)

    _set_wm_class()

    try:
        webview.create_window(TITLE, url, width=1280, height=860, min_size=(900, 600))
        webview.start(gui=gui)  # bloqueia até a janela fechar
        return 0
    except Exception as exc:
        logger.warning("Sem janela nativa disponível (%s) — abrindo no navegador.", exc)
        return _fallback_browser(url)


def _set_wm_class() -> None:
    """Batiza o processo para o GNOME reconhecer a janela.

    Sem isto a janela entra na barra como "python3", com ícone genérico, em vez
    de casar com `sign-manager.desktop` (campo `StartupWMClass`).
    """
    try:
        import gi

        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk, GLib

        GLib.set_prgname(APP_ID)
        GLib.set_application_name(TITLE)
        Gdk.set_program_class(APP_ID)
    except Exception as exc:  # pragma: no cover - só afeta a aparência
        logger.debug("Não consegui definir o WM_CLASS: %s", exc)


def _fallback_browser(url: str) -> int:
    import webbrowser

    webbrowser.open(url)
    print(f"Assinador em {url} — Ctrl+C encerra.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
