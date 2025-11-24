#!/bin/bash
set -e

# Configuration
LIBTORRENT_VER="1.2.20"
BOOST_VER="1.84.0"
BOOST_VER_UNDERSCORE="1_84_0"

# Urls
LT_URL="https://github.com/arvidn/libtorrent/releases/download/v${LIBTORRENT_VER}/libtorrent-rasterbar-${LIBTORRENT_VER}.tar.gz"
BOOST_URL="https://archives.boost.io/release/${BOOST_VER}/source/boost_${BOOST_VER_UNDERSCORE}.tar.gz"

# Paths
WORK_DIR="$(pwd)/libtorrent_build_work"
INSTALL_PREFIX="$(pwd)/libtorrent_dist"
VENV_PYTHON="$(pwd)/venv/bin/python3"

echo "=== libtorrent $LIBTORRENT_VER (with Boost $BOOST_VER) Installer ==="

# 1. Clean and Prepare
echo "[+] Cleaning work directory..."
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"
cd "$WORK_DIR"

# 2. Download Sources
echo "[+] Downloading Boost $BOOST_VER..."
curl -L -o "boost.tar.gz" "$BOOST_URL"
echo "[+] Extracting Boost..."
tar -xzf "boost.tar.gz"
mv "boost_${BOOST_VER_UNDERSCORE}" "boost"

echo "[+] Downloading libtorrent $LIBTORRENT_VER..."
curl -L -o "libtorrent.tar.gz" "$LT_URL"
echo "[+] Extracting libtorrent..."
tar -xzf "libtorrent.tar.gz"
mv "libtorrent-rasterbar-${LIBTORRENT_VER}" "libtorrent"

# 3. Setup Boost.Build (b2)
echo "[+] Bootstrapping Boost Build..."
cd boost
./bootstrap.sh
cd ..

# 4. Configure Build
# We need to tell b2 about our python version.
# We create a user-config.jam file.
echo "[+] Configuring Python for Build..."

# Get Python paths
PY_INC=$("$VENV_PYTHON" -c "import sysconfig; print(sysconfig.get_path('include'))")
PY_LIB=$("$VENV_PYTHON" -c "import sysconfig; print(sysconfig.get_config_var('LIBDIR'))")

echo "    Python Exec: $VENV_PYTHON"
echo "    Python Inc:  $PY_INC"
echo "    Python Lib:  $PY_LIB"

# Check if we are on macOS and need specific include/lib paths
# Usually sysconfig gives the right ones.

cat > user-config.jam <<EOF
using python : 3.13
    : "$VENV_PYTHON"
    : "$PY_INC"
    : "$PY_LIB"
    ;
using darwin ;
EOF

# 5. Build libtorrent
echo "[+] Building libtorrent..."
cd libtorrent

# We point BOOST_ROOT to our boost source
export BOOST_ROOT="$WORK_DIR/boost"
export BOOST_BUILD_PATH="$WORK_DIR/boost/tools/build"

# Copy b2 from boost to here for convenience, or call it directly
B2="$WORK_DIR/boost/b2"

# Build command explanation:
# cxxstd=17       : Use C++17
# release         : Release build
# python=3.13     : Build for Python 3.13 (matches our user-config)
# link=shared     : Build shared library (required for python extension)
# boost-link=static : Link boost statically into the extension (easier portability)
# toolset=darwin  : Use macOS toolset
# optimization=speed
# --user-config=../user-config.jam

"$B2" -j$(sysctl -n hw.ncpu) \
    --user-config=../user-config.jam \
    toolset=darwin \
    cxxstd=17 \
    release \
    python=3.13 \
    link=shared \
    boost-link=static \
    crypto=openssl \
    bindings/python//libtorrent

# 6. Install/Copy Artifacts
echo "[+] Installing..."

# The build output is deep in bindings/python/bin/...
# We search for the .so file
SO_FILE=$(find bindings/python/bin -name "libtorrent.so" | head -n 1)

if [ -z "$SO_FILE" ]; then
    echo "[-] Error: Build failed? No .so file found."
    exit 1
fi

echo "    Found module: $SO_FILE"

# Destination in venv
SITE_PACKAGES=$(cd ../../venv/lib/python3.13/site-packages && pwd)
cp "$SO_FILE" "$SITE_PACKAGES/libtorrent.so"

echo ""
echo "=== Success! ==="
echo "Installed libtorrent.so to $SITE_PACKAGES"
echo "You can now run your python script."
echo ""
