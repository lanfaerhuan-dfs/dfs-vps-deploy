#!/bin/bash
set -euo pipefail
root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [[ $# -ne 2 ]]; then
    printf '%s\n' '用法：scripts/package.sh <Git tag 或 commit> <输出目录>' >&2
    exit 2
fi
commit="$(git -C "$root_dir" rev-parse --verify --end-of-options "$1^{commit}")"
mkdir -p -- "$2"
output="$(cd -- "$2" && pwd -P)"
archive="dfs-vps-deploy-${commit:0:12}.tar.gz"
temp="$(mktemp "$output/.dfs-release.XXXXXXXX")"
trap 'rm -f -- "$temp"' EXIT
git -C "$root_dir" archive --format=tar --prefix=dfs-vps-deploy/ "$commit" | gzip -n > "$temp"
chmod 644 "$temp"
mv -f -- "$temp" "$output/$archive"
cd -- "$output"
if command -v sha256sum >/dev/null; then
    sha256sum "$archive" > "$archive.sha256"
else
    shasum -a 256 "$archive" > "$archive.sha256"
fi
cat "$archive.sha256"
