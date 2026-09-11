#!/usr/bin/env bash
# Instala o Assinador no ambiente de desktop do usuário, sem sudo.
#
# Linux:
#   * ícone em ~/.local/share/icons/hicolor/<tamanho>/apps/
#   * lançador em ~/.local/share/applications/ (aparece no menu)
#   * comando `assinador-digital` em ~/bin/
#
# macOS:
#   * Assinador.app em ~/Applications, que aparece no Launchpad e no Spotlight
#   * comando `assinador-digital` em ~/bin/
#
# No Windows, use install.ps1.
#
# Nada é copiado para fora daqui: o lançador aponta para o run.sh deste
# diretório, então `git pull` já atualiza o app instalado.
set -euo pipefail

cd "$(dirname "$0")"
RAIZ="$(pwd)"

APP_ID="assinador-digital"
ICONE="app/static/icon.png"
TAMANHOS=(16 24 32 48 64 128 256 512)

SISTEMA="$(uname -s)"
echo "Instalando a partir de $RAIZ ($SISTEMA)"

# ─────────────────────────── macOS ───────────────────────────
if [ "$SISTEMA" = "Darwin" ]; then
  APP="$HOME/Applications/Assinador.app"
  echo "→ $APP"
  mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

  cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Assinador</string>
  <key>CFBundleDisplayName</key><string>Assinador</string>
  <key>CFBundleIdentifier</key><string>com.assinador.digital</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>assinador</string>
  <key>CFBundleIconFile</key><string>assinador.icns</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

  cat > "$APP/Contents/MacOS/assinador" <<LAUNCHER
#!/usr/bin/env bash
exec "$RAIZ/run.sh"
LAUNCHER
  chmod +x "$APP/Contents/MacOS/assinador"

  # ícone: .icns quando houver o conversor do sistema, senão o PNG mesmo
  if command -v sips >/dev/null 2>&1 && command -v iconutil >/dev/null 2>&1; then
    TEMP="$(mktemp -d)/assinador.iconset"
    mkdir -p "$TEMP"
    for T in 16 32 64 128 256 512; do
      sips -z $T $T "$ICONE" --out "$TEMP/icon_${T}x${T}.png" >/dev/null 2>&1 || true
    done
    iconutil -c icns "$TEMP" -o "$APP/Contents/Resources/assinador.icns" 2>/dev/null || \
      cp "$ICONE" "$APP/Contents/Resources/assinador.png"
  else
    cp "$ICONE" "$APP/Contents/Resources/assinador.png"
  fi

  mkdir -p "$HOME/bin"
  cat > "$HOME/bin/$APP_ID" <<EOF
#!/usr/bin/env bash
exec "$RAIZ/run.sh" "\$@"
EOF
  chmod +x "$HOME/bin/$APP_ID"

  echo
  echo "Pronto. O Assinador está no Launchpad, e o comando é: $APP_ID"
  exit 0
fi

# ─────────────────────────── Linux ───────────────────────────

# --- ícone ------------------------------------------------------------------
if [ ! -f "$ICONE" ]; then
  echo "! $ICONE não existe" >&2
  exit 1
fi

echo "→ ícone"
# Pillow: usa o do venv se já existe, senão o do sistema (python3-pil).
PY_BIN="python3"
[ -x .venv/bin/python ] && PY_BIN=".venv/bin/python"

"$PY_BIN" - "$ICONE" "$APP_ID" "${TAMANHOS[@]}" <<'PY'
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit(
        "Pillow não encontrado. Instale com `sudo apt install python3-pil` "
        "ou rode ./run.sh uma vez para criar o ambiente virtual."
    )

origem, app_id, *tamanhos = sys.argv[1:]
fonte = Image.open(origem).convert("RGBA")
base = Path.home() / ".local/share/icons/hicolor"
for t in (int(x) for x in tamanhos):
    destino = base / f"{t}x{t}/apps"
    destino.mkdir(parents=True, exist_ok=True)
    fonte.resize((t, t), Image.LANCZOS).save(destino / f"{app_id}.png")
PY

# --- lançador ---------------------------------------------------------------
echo "→ lançador"
APPS="$HOME/.local/share/applications"
mkdir -p "$APPS"
# o app já se chamou sign-manager; deixar as duas entradas no menu confunde
rm -f "$APPS/sign-manager.desktop" "$HOME/bin/sign-manager"
rm -f "$HOME/.local/share/icons/hicolor/"*/apps/sign-manager.png
sed "s|@RUN_SH@|$RAIZ/run.sh|" packaging/$APP_ID.desktop > "$APPS/$APP_ID.desktop"
chmod +x "$APPS/$APP_ID.desktop"

# --- comando de terminal ----------------------------------------------------
echo "→ comando $APP_ID"
mkdir -p "$HOME/bin"
cat > "$HOME/bin/$APP_ID" <<EOF
#!/usr/bin/env bash
# Abre o Assinador (app local de assinatura de PDF).
exec "$RAIZ/run.sh" "\$@"
EOF
chmod +x "$HOME/bin/$APP_ID"

# --- caches -----------------------------------------------------------------
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null && \
  gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true

echo
echo "Pronto. O Assinador está no menu de aplicativos, e o comando é: $APP_ID"

# --- o que ainda depende de sudo -------------------------------------------
if ! python3 -c "
import gi, sys
v = gi.Repository.get_default().enumerate_versions('WebKit2')
sys.exit(0 if v and max(v) >= '4.1' else 1)
" 2>/dev/null; then
  echo
  echo "Falta uma coisa, e ela precisa de sudo:"
  echo "    sudo apt install gir1.2-webkit2-4.1"
  echo "Sem isso o app abre numa aba do navegador em vez de janela própria."
fi
