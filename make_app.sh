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
SRC="$PROJECT/sam.png"
# Resize proportionally and pad to square with transparency (no stretching)
"$PROJECT/.venv/bin/python" - "$SRC" "$ICONSET" <<'PYEOF'
import sys
from AppKit import NSImage, NSBitmapImageRep, NSGraphicsContext, NSZeroRect, NSMakeRect, NSCompositingOperationSourceOver

src_path, iconset = sys.argv[1], sys.argv[2]
src = NSImage.alloc().initWithContentsOfFile_(src_path)
orig_w, orig_h = src.size().width, src.size().height

entries = [
    (16,   "icon_16x16.png"),
    (32,   "icon_32x32.png"),
    (64,   "icon_64x64.png"),
    (128,  "icon_128x128.png"),
    (256,  "icon_256x256.png"),
    (512,  "icon_512x512.png"),
    (32,   "icon_16x16@2x.png"),
    (64,   "icon_32x32@2x.png"),
    (128,  "icon_64x64@2x.png"),
    (256,  "icon_128x128@2x.png"),
    (512,  "icon_256x256@2x.png"),
    (1024, "icon_512x512@2x.png"),
]

for size, name in entries:
    scale = min(size / orig_w, size / orig_h)
    draw_w = orig_w * scale
    draw_h = orig_h * scale
    x = (size - draw_w) / 2
    y = (size - draw_h) / 2

    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, size, size, 8, 4, True, False, "NSCalibratedRGBColorSpace", 0, 0)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep))
    src.drawInRect_fromRect_operation_fraction_(NSMakeRect(x, y, draw_w, draw_h), NSZeroRect, NSCompositingOperationSourceOver, 1.0)
    NSGraphicsContext.restoreGraphicsState()

    png = rep.representationUsingType_properties_(4, None)  # NSBitmapImageFileTypePNG
    png.writeToFile_atomically_(f"{iconset}/{name}", True)
PYEOF
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
