#!/bin/bash
# Update the files that snapdelta-web serves itself: htmx, the two fonts and Swagger UI.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$ROOT/packages/snapdelta-web/src/snapdelta_web/static/vendor"
REGISTRY="https://registry.npmjs.org"

usage() {
  cat <<'EOF'
Usage: scripts/update-assets.sh [OPTIONS]

Download htmx, the fonts Bricolage Grotesque and JetBrains Mono, and Swagger UI
from the npm registry, check each package against the hash of the registry, and
copy the files into packages/snapdelta-web/src/snapdelta_web/static/vendor.
The versions in use are written to static/vendor/VERSIONS.

Options:
  --htmx VERSION      htmx.org version (default: latest)
  --swagger VERSION   swagger-ui-dist version (default: latest)
  --fonts VERSION     version of both Fontsource font packages (default: latest of each)
  -h, --help          Show this help

Environment:
  HTMX_VERSION, SWAGGER_VERSION, FONTS_VERSION   Same as the options; the options win.

Examples:
  scripts/update-assets.sh
  scripts/update-assets.sh --htmx 2.0.11
  make assets HTMX_VERSION=2.0.11
EOF
}

step() {
  if [[ -t 1 ]]; then
    printf '\n\033[1;36m%s\033[0m\n' "$*"
  else
    printf '\n%s\n' "$*"
  fi
}

fail() {
  echo "error: $*" >&2
  exit 1
}

htmx_version="${HTMX_VERSION:-}"
swagger_version="${SWAGGER_VERSION:-}"
fonts_version="${FONTS_VERSION:-}"
while (( $# > 0 )); do
  case $1 in
    --htmx) [[ -n ${2:-} ]] || { usage >&2; exit 2; }; htmx_version=$2; shift 2 ;;
    --swagger) [[ -n ${2:-} ]] || { usage >&2; exit 2; }; swagger_version=$2; shift 2 ;;
    --fonts) [[ -n ${2:-} ]] || { usage >&2; exit 2; }; fonts_version=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

for tool in curl tar python3 openssl; do
  command -v "$tool" > /dev/null || fail "$tool is required"
done

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Prints "VERSION TARBALL_URL SRI_INTEGRITY" of a package version; an empty version means the latest.
package_info() {
  local name=$1 version=${2:-latest}
  curl -fsSL "$REGISTRY/$name/$version" | python3 -I -c '
import json, sys
meta = json.load(sys.stdin)
print(meta["version"], meta["dist"]["tarball"], meta["dist"]["integrity"])
'
}

# Downloads a package, checks its integrity, unpacks it in $WORK/<dir> and prints its version.
fetch() {
  local name=$1 version=$2 dir=$3 info url integrity algorithm expected actual
  info="$(package_info "$name" "$version")" || fail "$name@${version:-latest} is not in the npm registry"
  read -r version url integrity <<< "$info"
  curl -fsSL "$url" -o "$WORK/$dir.tgz" || fail "cannot download $url"
  algorithm=${integrity%%-*}
  expected=${integrity#*-}
  actual="$(openssl dgst "-$algorithm" -binary "$WORK/$dir.tgz" | openssl base64 -A)"
  [[ $actual == "$expected" ]] || fail "$name@$version: the tarball does not match the registry hash"
  mkdir -p "$WORK/$dir"
  tar -xzf "$WORK/$dir.tgz" -C "$WORK/$dir" --no-same-owner
  echo "$version"
}

step "Download and check the packages"
htmx_version="$(fetch htmx.org "$htmx_version" htmx)"
swagger_version="$(fetch swagger-ui-dist "$swagger_version" swagger)"
bricolage_version="$(fetch @fontsource-variable/bricolage-grotesque "$fonts_version" bricolage)"
mono_version="$(fetch @fontsource/jetbrains-mono "$fonts_version" mono)"

step "Copy the files to static/vendor"
mkdir -p "$VENDOR/fonts" "$VENDOR/swagger-ui" "$VENDOR/licenses"
cp "$WORK/htmx/package/dist/htmx.min.js" "$VENDOR/htmx.min.js"
cp "$WORK/swagger/package/swagger-ui-bundle.js" "$WORK/swagger/package/swagger-ui.css" "$VENDOR/swagger-ui/"
for subset in latin latin-ext; do
  cp "$WORK/bricolage/package/files/bricolage-grotesque-$subset-opsz-normal.woff2" "$VENDOR/fonts/"
  for weight in 400 500; do
    cp "$WORK/mono/package/files/jetbrains-mono-$subset-$weight-normal.woff2" "$VENDOR/fonts/"
  done
done
cp "$WORK/htmx/package/LICENSE" "$VENDOR/licenses/htmx-LICENSE"
cp "$WORK/swagger/package/LICENSE" "$VENDOR/licenses/swagger-ui-LICENSE"
cp "$WORK/bricolage/package/LICENSE" "$VENDOR/licenses/bricolage-grotesque-OFL.txt"
cp "$WORK/mono/package/LICENSE" "$VENDOR/licenses/jetbrains-mono-OFL.txt"

cat > "$VENDOR/VERSIONS" <<EOF
htmx.org $htmx_version
swagger-ui-dist $swagger_version
@fontsource-variable/bricolage-grotesque $bricolage_version
@fontsource/jetbrains-mono $mono_version
EOF

step "Versions in use"
cat "$VENDOR/VERSIONS"
echo
echo "Run the tests, then check the web UI and /api/v1/docs in a browser."
