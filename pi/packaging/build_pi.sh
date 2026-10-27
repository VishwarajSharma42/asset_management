#!/usr/bin/env bash
# Build the DSO enrolment app into a standalone Linux program ON THE RASPBERRY PI.
#
#   cd pi && bash packaging/build_pi.sh
#
# PyInstaller cannot cross-compile: build on the Pi (64-bit Raspberry Pi OS, Python 3.11+).
# A Windows .exe cannot run on a Raspberry Pi, so the Pi build is a native executable folder
# (dist/dso-enroller/dso-enroller) started from a desktop shortcut, not an .exe.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PYTHON:-python3}
$PY -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'

sudo apt-get update
sudo apt-get install -y python3-venv python3-tk python3-dev libjpeg-dev zlib1g-dev i2c-tools

$PY -m venv .build-venv
. .build-venv/bin/activate
pip install --upgrade pip wheel
pip install -r requirements.txt pyinstaller

# --onedir (not --onefile): onefile unpacks itself to /tmp on every launch, which is slow on a Pi.
pyinstaller --noconfirm --clean --windowed --name dso-enroller \
  --collect-all customtkinter \
  --collect-all adafruit_blinka \
  --collect-submodules adafruit_pn532 \
  --hidden-import board --hidden-import busio --hidden-import digitalio \
  pi_entry.py

INSTALL_DIR=/opt/dso-enroller
sudo rm -rf "$INSTALL_DIR"
sudo mkdir -p "$INSTALL_DIR"
sudo cp -r dist/dso-enroller/* "$INSTALL_DIR"/
sudo chmod -R go-w "$INSTALL_DIR"

mkdir -p "$HOME/.local/share/applications" "$HOME/Desktop"
sed "s#@INSTALL_DIR@#$INSTALL_DIR#g" packaging/dso-enroller.desktop > "$HOME/.local/share/applications/dso-enroller.desktop"
cp "$HOME/.local/share/applications/dso-enroller.desktop" "$HOME/Desktop/" 2>/dev/null || true
chmod +x "$HOME/Desktop/dso-enroller.desktop" 2>/dev/null || true

# The reader needs the I2C (or SPI) device nodes, which belong to the i2c/spi/gpio groups.
sudo usermod -aG i2c,spi,gpio "$USER" || true

echo
echo "Built and installed to $INSTALL_DIR"
echo "Next: docs/PI_SETUP.md  (enable I2C, create ~/.config/dso-enroller/, set the PIN)."
echo "Log out and back in once so the new group memberships apply."
