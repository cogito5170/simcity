#!/bin/sh
# Build OpenTTD 15.3 (with the 'shotat' screenshot patch), SDL2 headers, and the OpenGFX base set from source.
# Everything comes through git; no package registry is needed. Results:
#   $ROOT/openttd-src/build/openttd   (+ baseset/opengfx, game/llm_bridge, ai/idle next to it)
set -e
ROOT=${ROOT:-/home/user}
HERE=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
[ -d openttd-src ] || git clone --depth 1 --branch 15.3 https://github.com/OpenTTD/OpenTTD.git openttd-src
[ -d sdl2-src ] || git clone --depth 1 --branch release-2.30.x https://github.com/libsdl-org/SDL.git sdl2-src
[ -d opengfx-src ] || git clone --depth 1 https://github.com/OpenTTD/OpenGFX.git opengfx-src
[ -d nml-src ] || git clone --depth 1 https://github.com/OpenTTD/nml.git nml-src
[ -d ply-src ] || git clone --depth 1 https://github.com/dabeaz/ply.git ply-src
[ -d grfcodec-src ] || git clone --depth 1 https://github.com/OpenTTD/grfcodec.git grfcodec-src
# SDL2 (only headers are missing on the image; build it to a private prefix)
[ -f local/lib/libSDL2.so ] || (mkdir -p sdl2-src/build && cd sdl2-src/build && cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$ROOT/local" -DSDL_TEST=OFF && make -j4 && make install)
# OpenGFX needs nmlc (Python, with ply) and grfid
mkdir -p local/bin
printf '#!/bin/sh\nPYTHONPATH=%s/ply-src/src:%s/nml-src exec python3 %s/nml-src/nmlc "$@"\n' "$ROOT" "$ROOT" "$ROOT" > local/bin/nmlc
chmod +x local/bin/nmlc
[ -f grfcodec-src/build/grfid ] || (mkdir -p grfcodec-src/build && cd grfcodec-src/build && cmake .. -DCMAKE_BUILD_TYPE=Release && make -j2 grfid)
ln -sf "$ROOT/grfcodec-src/build/grfid" local/bin/grfid
[ -f opengfx-src/opengfx.obg ] || (cd opengfx-src && PATH="$ROOT/local/bin:$PATH" make -j2 GIMP=)
# OpenTTD with the patch
cd openttd-src
git apply --check "$HERE/tools/openttd_patches/shotat.patch" 2>/dev/null && git apply "$HERE/tools/openttd_patches/shotat.patch"
mkdir -p build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_PREFIX_PATH="$ROOT/local"
make -j4 openttd
mkdir -p baseset/opengfx game ai
cp "$ROOT"/opengfx-src/*.grf "$ROOT"/opengfx-src/opengfx.obg baseset/opengfx/
ln -sfn "$HERE/openttd_scripts/game/llm_bridge" game/llm_bridge
ln -sfn "$HERE/openttd_scripts/ai/idle" ai/idle
echo "OpenTTD ready: $ROOT/openttd-src/build/openttd"
