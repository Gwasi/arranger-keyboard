#!/bin/zsh
set -euo pipefail
cd "$(dirname "$0")"

if ! python3 -c "import PyInstaller" >/dev/null 2>&1; then
    python3 -m pip install --user --disable-pip-version-check pyinstaller
fi

rm -rf "dist/Arranger Keyboard.app" "dist/Arranger Keyboard" build/pyinstaller
ICON="$PWD/assets/AppIcon.icns"

python3 -m PyInstaller \
    --noconfirm \
    --clean \
    --windowed \
    --name "Arranger Keyboard" \
    --icon "$ICON" \
    --osx-bundle-identifier "com.local.ArrangerKeyboardDesktop" \
    --paths "arranger_keyboard" \
    --hidden-import "pygame.midi" \
    --hidden-import "AVFoundation" \
    --hidden-import "Foundation" \
    --hidden-import "objc" \
    --exclude-module "tkinter" \
    --exclude-module "matplotlib" \
    --exclude-module "PyQt5" \
    --exclude-module "PySide6" \
    --exclude-module "torch" \
    --exclude-module "torchvision" \
    --exclude-module "torchaudio" \
    --exclude-module "yt_dlp" \
    --exclude-module "PIL" \
    --distpath "dist" \
    --workpath "build/pyinstaller" \
    --specpath "build" \
    "desktop_launcher.py"

codesign --force --deep --sign - "dist/Arranger Keyboard.app"
echo "Built: $(pwd)/dist/Arranger Keyboard.app"
