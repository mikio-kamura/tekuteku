#!/bin/bash
# Builds てくてく.app in /Volumes/ssd_pyoi/9_Application/
# Run this after updating white-star.png or changing the app name/version.

set -e

PROJECT="$(cd "$(dirname "$0")" && pwd)"
APP="/Volumes/ssd_pyoi/9_Application/てくてく.app"
ICONSET="/tmp/tekuteku_build.iconset"

echo "==> Building $APP"

# --- Icon ---
echo "  icon..."
rm -rf "$ICONSET"
mkdir "$ICONSET"
SRC="$PROJECT/white-star.png"
for size in 16 32 64 128 256 512; do
  sips -z $size $size "$SRC" --out "$ICONSET/icon_${size}x${size}.png"        > /dev/null
  sips -z $((size*2)) $((size*2)) "$SRC" --out "$ICONSET/icon_${size}x${size}@2x.png" > /dev/null
done
iconutil -c icns "$ICONSET" -o /tmp/tekuteku.icns

# --- Bundle skeleton ---
echo "  bundle..."
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
mkdir -p "$APP/Contents/Resources"

cp /tmp/tekuteku.icns "$APP/Contents/Resources/tekuteku.icns"

# --- Info.plist ---
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>tekuteku</string>
    <key>CFBundleIdentifier</key>
    <string>com.mikio.tekuteku</string>
    <key>CFBundleName</key>
    <string>てくてく</string>
    <key>CFBundleDisplayName</key>
    <string>てくてく</string>
    <key>CFBundleIconFile</key>
    <string>tekuteku</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
PLIST

# --- Launcher ---
cat > "$APP/Contents/MacOS/tekuteku" <<LAUNCHER
#!/bin/bash
PROJECT="$PROJECT"
exec "\$PROJECT/.venv/bin/python" "\$PROJECT/app.py"
LAUNCHER
chmod +x "$APP/Contents/MacOS/tekuteku"

# Notify Spotlight of the new bundle
touch "$APP"

echo "==> Done: $APP"
