#!/usr/bin/env bash
# Sobe o Assinador. Cria o ambiente virtual na primeira execução.
#
# Serve para Linux e macOS. No Windows, use run.ps1.
#
# O servidor escuta apenas em 127.0.0.1, numa porta livre escolhida na hora, e
# a interface abre numa janela própria.
set -euo pipefail

cd "$(dirname "$0")"

# No Linux a janela usa o PyGObject do sistema, e o ambiente virtual precisa
# enxergá-lo. No macOS a janela é o WKWebView, que vem pelo pip com o pywebview.
VENV_FLAGS=()
if [ "$(uname -s)" = "Linux" ]; then
  VENV_FLAGS=(--system-site-packages)
fi

if [ ! -d .venv ]; then
  echo "Criando ambiente virtual…"
  if command -v uv >/dev/null 2>&1; then
    uv venv "${VENV_FLAGS[@]}" .venv
    VIRTUAL_ENV=.venv uv pip install -r requirements.txt
  else
    python3 -m venv "${VENV_FLAGS[@]}" .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
  fi
fi

exec .venv/bin/python -m app.desktop "$@"
