"""Janela nativa do assinador.

O app é uma página local servida por FastAPI, mas não abre numa aba: sobe o
uvicorn em 127.0.0.1 numa porta livre, numa thread daemon, e aponta uma janela
nativa (pywebview) para ela.

Cada sistema tem o seu motor, e só um deles dá trabalho:

* **macOS** usa o WKWebView do próprio sistema. O pywebview traz o pyobjc
  junto, então não há nada a instalar.
* **Windows** usa o WebView2, que acompanha o Edge desde o Windows 10. O
  pywebview traz o pythonnet junto.
* **Linux** usa o WebKit do sistema via PyGObject, e é aqui que mora a
  verificação abaixo: distribuição com binding antigo abre uma janela que não
  funciona direito, e é melhor cair para o navegador do que entregar isso.

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
from app.platform_support import APP_ID, IS_LINUX

HOST = "127.0.0.1"  # nunca 0.0.0.0: este app não tem autenticação por design
TITLE = "Assinador"



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


def pick_gui() -> str | None:
    """Qual motor de janela usar, ou None para cair no navegador.

    Fora do Linux a resposta é "o do sistema": deixa o pywebview escolher.
    """
    if not IS_LINUX:
        return ""  # vazio: quem escolhe é o pywebview, e ele acerta

    # Linux: GTK, se o binding for novo o bastante; senão Qt, se estiver
    # instalado; senão navegador.
    binding = webkit_binding()
    if binding is not None and binding >= WEBKIT_MIN:
        return "gtk"
    if qt_available():
        return "qt"
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
    return None


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

    gui = pick_gui()
    if gui is None:
        return _fallback_browser(url)

    _set_wm_class()

    try:
        webview.create_window(TITLE, url, width=1280, height=860, min_size=(900, 600))
        webview.start(gui=gui or None)  # bloqueia até a janela fechar
        return 0
    except Exception as exc:
        logger.warning("Sem janela nativa disponível (%s) — abrindo no navegador.", exc)
        return _fallback_browser(url)


def _set_wm_class() -> None:
    """Batiza o processo para o GNOME reconhecer a janela.

    Sem isto a janela entra na barra como "python3", com ícone genérico, em vez
    de casar com `assinador-digital.desktop` (campo `StartupWMClass`). Só faz
    sentido no Linux: macOS e Windows identificam a janela de outro jeito.
    """
    if not IS_LINUX:
        return
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
