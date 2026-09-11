"""O que muda de um sistema operacional para outro.

O app é o mesmo nos três: um servidor local em 127.0.0.1 e uma janela apontada
para ele. O que difere é onde ficam as coisas e como se pede ao sistema que
abra um arquivo. Tudo isso mora aqui, para o resto do código não precisar
saber em que máquina está rodando.

Sobre permissões: em Linux e macOS o cofre é protegido por modo `600`, que o
sistema respeita. No Windows não existe equivalente direto, e um `chmod` do
Python não faz nada de útil. O README diz isso em voz alta em vez de fingir
que protege.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

APP_ID = "assinador-digital"

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = not IS_WINDOWS and not IS_MAC


def config_dir() -> Path:
    """Onde o cofre de certificados vive, no lugar que cada sistema espera."""
    if IS_WINDOWS:
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return base / "Assinador"
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / "Assinador"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / APP_ID


def downloads_dir() -> Path:
    """A pasta de downloads do usuário, que é onde o navegador salva."""
    if IS_LINUX:
        try:
            saida = subprocess.run(
                ["xdg-user-dir", "DOWNLOAD"], capture_output=True, text=True, timeout=3
            )
            caminho = Path(saida.stdout.strip())
            if caminho.is_dir():
                return caminho
        except Exception:  # pragma: no cover - xdg-user-dir pode não existir
            pass
    if IS_WINDOWS:
        # o usuário pode ter movido a pasta; o registro é quem sabe onde ela está
        try:  # pragma: no cover - só roda no Windows
            import winreg

            chave = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, chave) as k:
                valor, _ = winreg.QueryValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}")
            caminho = Path(os.path.expandvars(valor))
            if caminho.is_dir():
                return caminho
        except Exception:
            pass
    return Path.home() / "Downloads"


def open_path(caminho: Path) -> None:
    """Abre um arquivo ou pasta no ambiente gráfico do usuário."""
    if IS_WINDOWS:  # pragma: no cover - só roda no Windows
        os.startfile(str(caminho))  # noqa: S606
        return
    comando = ["open"] if IS_MAC else ["xdg-open"]
    subprocess.Popen(
        [*comando, str(caminho)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def protect(caminho: Path, *, directory: bool = False) -> None:
    """Restringe o acesso ao arquivo ou diretório, onde isso significa algo.

    No Windows um `chmod` não muda a ACL, então nem tentamos: o arquivo herda
    as permissões da pasta do perfil do usuário, e é isso que o README promete.
    """
    if IS_WINDOWS:
        return
    try:
        caminho.chmod(0o700 if directory else 0o600)
    except OSError:  # pragma: no cover - sistema de arquivos sem permissões
        pass


#: Fontes candidatas ao carimbo, por sistema.
#:
#: O critério não é estético: o pyHanko escreve os avanços de glifo assumindo
#: 1000 unidades por em, e fonte com `unitsPerEm` 2048, que é quase toda
#: TrueType, sai com as letras espaçadas. As OpenType de contorno CFF usam
#: 1000, e é por isso que a lista é dominada por `.otf`. Nenhuma serve? O
#: carimbo cai para Courier, que é uma das 14 fontes-padrão do PDF, existe em
#: qualquer leitor e não depende de nada instalado.
FONT_CANDIDATES: tuple[Path, ...] = (
    # Linux: pacote fonts-urw-base35, que acompanha o ghostscript
    Path("/usr/share/fonts/opentype/urw-base35/NimbusSans-Regular.otf"),
    Path("/usr/share/fonts/type1/urw-base35/NimbusSans-Regular.t1"),
    # macOS
    Path("/System/Library/Fonts/Helvetica.ttc"),
    Path("/Library/Fonts/Arial Unicode.ttf"),
    # Windows
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("C:/Windows/Fonts/segoeui.ttf"),
)


def stamp_font() -> Path | None:
    """A primeira fonte instalada que o pyHanko desenha sem espaçar as letras.

    Confere o `unitsPerEm` de verdade em vez de confiar no nome do arquivo.
    """
    for caminho in FONT_CANDIDATES:
        if not caminho.exists():
            continue
        try:
            from fontTools.ttLib import TTFont

            fonte = TTFont(str(caminho), lazy=True, fontNumber=0)
            if fonte["head"].unitsPerEm == 1000:
                return caminho
        except Exception:  # pragma: no cover - fonte ilegível é fonte descartada
            continue
    return None
