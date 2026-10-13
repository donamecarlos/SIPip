#!/usr/bin/env bash
# =============================================================================
# SIPip: provisionado de Ubuntu + compilacion del APK con buildozer/p4a.
# Se ejecuta dentro de WSL2 como root (lo lanza wsl_install.ps1). Tambien puede
# invocarse a mano:  bash /mnt/d/SIPip/android/ubuntu_build.sh
# Produce /mnt/d/SIPip/dist/android/SIPip-*.apk
# =============================================================================
set -euo pipefail

SRC="${1:-/mnt/d/SIPip}"
DEST="${2:-$HOME/sipip-build}"
export DEBIAN_FRONTEND=noninteractive
export PIP_BREAK_SYSTEM_PACKAGES=1

echo "==[1/5] Dependencias del sistema (apt)..."
dpkg --configure -a 2>/dev/null || true
apt-get update -y
apt-get install -f -y 2>/dev/null || true
apt-get install -y \
    build-essential ccache git zip unzip pkg-config patch autoconf automake libtool \
    openjdk-17-jdk-headless \
    python3 python3-pip python3-venv python3-setuptools \
    libffi-dev libssl-dev libsqlite3-dev libbz2-dev libncurses-dev zlib1g-dev \
    libcurl4-openssl-dev
export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-17-openjdk-amd64}"

echo "==[2/5] Host Python 3.12 + buildozer (pip --user con Python de uv)..."
if [ ! -x /root/.local/bin/uv ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="/root/.local/bin:$PATH"
UV=/root/.local/bin/uv
"$UV" python install -q 3.12
PY312="$("$UV" python find 3.12)"
"$PY312" -m pip install -q --upgrade pip
if ! command -v buildozer >/dev/null 2>&1; then
    "$PY312" -m pip install -q --user buildozer cython
fi
BUILDOZER=/root/.local/bin/buildozer
"$BUILDOZER" --version

echo "==[3/5] Copiando el proyecto a $DEST (conservando cache .buildozer)..."
mkdir -p "$DEST"
for d in sipclient sipdroid; do
    mkdir -p "$DEST/$d"
    cp -r "$SRC/$d/." "$DEST/$d/"
done
cp "$SRC/main.py" "$SRC/buildozer.spec" "$SRC/icon.png" "$DEST/"
if [ -d "$SRC/tonos" ]; then
    mkdir -p "$DEST/tonos"
    cp -r "$SRC/tonos/." "$DEST/tonos/"
fi
find "$DEST" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "==[4/5] Compilando APK (buildozer android debug)..."
cd "$DEST"
yes | "$BUILDOZER" -v android debug

echo "==[5/5] Copiando APK a $SRC/dist/android..."
mkdir -p "$SRC/dist/android"
cp -f "$DEST"/bin/*.apk "$SRC/dist/android/"
ls -lh "$SRC/dist/android/"
echo "LISTO"