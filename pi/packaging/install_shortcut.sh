#!/usr/bin/env bash
# Puts an "Asset Register" icon on the Raspberry Pi desktop for a venv install (app + .venv in one folder).
#
#   bash install_shortcut.sh [APP_DIR]        APP_DIR defaults to ~/assets
#
# Double-tapping the icon starts the enrolment app; if it is already running nothing happens.
set -euo pipefail

APP_DIR=$(cd "${1:-$HOME/assets}" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
ICON="$HOME/.local/share/icons/asset-register.png"
ENTRY="$HOME/.local/share/applications/asset-register.desktop"

[ -x "$APP_DIR/.venv/bin/python" ] || { echo "no .venv in $APP_DIR" >&2; exit 1; }

cat > "$APP_DIR/launch.sh" <<EOF
#!/bin/bash
cd "\$(dirname "\$0")" || exit 1
export DISPLAY="\${DISPLAY:-:0}"
pgrep -f "[d]so_enroller" >/dev/null && exit 0
exec .venv/bin/python -m dso_enroller >> app.log 2>&1
EOF
chmod +x "$APP_DIR/launch.sh"

mkdir -p "$(dirname "$ICON")" "$(dirname "$ENTRY")" "$HOME/Desktop"
"$APP_DIR/.venv/bin/python" "$HERE/make_icon.py" "$ICON"

cat > "$ENTRY" <<EOF
[Desktop Entry]
Type=Application
Name=Asset Register
Comment=Write NFC tags for the asset register
Exec=$APP_DIR/launch.sh
Icon=$ICON
Terminal=false
Categories=Utility;
StartupNotify=false
EOF

cp "$ENTRY" "$HOME/Desktop/asset-register.desktop"
chmod +x "$HOME/Desktop/asset-register.desktop"
gio set "$HOME/Desktop/asset-register.desktop" metadata::trusted true 2>/dev/null || true

# launch on double-tap without the "Execute File?" question
CONF="$HOME/.config/libfm/libfm.conf"
if [ -f "$CONF" ]; then
  sed -i 's/^quick_exec=0/quick_exec=1/' "$CONF"
fi
echo "shortcut installed: ~/Desktop/asset-register.desktop"
