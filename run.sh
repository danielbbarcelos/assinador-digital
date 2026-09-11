#!/usr/bin/env bash
# Sobe o assinador. Cria o ambiente virtual na primeira execução.
#
# O servidor escuta apenas em 127.0.0.1, numa porta livre escolhida na hora,
# e a interface abre numa janela própria (pywebview). Sem janela disponível,
# cai para o navegador padrão.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Criando ambiente virtual…"
  if command -v uv >/dev/null 2>&1; then
    uv venv --system-site-packages .venv
    VIRTUAL_ENV=.venv uv pip install -r requirements.txt
  else
    # --system-site-packages dá acesso ao PyGObject do sistema, que é o que
    # o pywebview usa para abrir a janela no Linux.
    python3 -m venv --system-site-packages .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
  fi
fi

exec .venv/bin/python -m app.desktop "$@"
