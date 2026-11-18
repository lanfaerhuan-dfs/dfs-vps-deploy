#!/bin/bash
set -euo pipefail
root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [[ $# -lt 2 || $# -gt 3 ]]; then
    printf '%s\n' '用法：scripts/package.sh <Git tag 或 commit> <输出目录> [OWNER/REPO]' >&2
    exit 2
fi
ref="$1"
release_repo="${3:-}"
if [[ $# -eq 3 ]]; then
    if [[ ! "$ref" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ || ! "$release_repo" =~ ^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$ ]]; then
        printf '%s\n' '生成 install.sh 时请使用 vX.Y.Z 标签和 OWNER/REPO。' >&2
        exit 2
    fi
    ref="refs/tags/$ref"
fi
commit="$(git -C "$root_dir" rev-parse --verify --end-of-options "$ref^{commit}")"
mkdir -p -- "$2"
output="$(cd -- "$2" && pwd -P)"
archive="dfs-vps-deploy-${commit:0:12}.tar.gz"
staging="$(mktemp -d "$output/.dfs-release.XXXXXXXX")"
trap 'rm -rf -- "$staging"' EXIT
git -C "$root_dir" archive --format=tar --prefix=dfs-vps-deploy/ "$commit" | gzip -n > "$staging/$archive"
cd -- "$staging"
if command -v sha256sum >/dev/null; then
    sha256sum "$archive" > "$archive.sha256"
else
    shasum -a 256 "$archive" > "$archive.sha256"
fi
chmod 644 "$archive" "$archive.sha256"

if [[ -n "$release_repo" ]]; then
    read -r checksum _ < "$archive.sha256"
    url="https://github.com/$release_repo/releases/download/$1/$archive"
    git -C "$root_dir" show "$commit:scripts/install.sh.in" |
        sed -e "s|@ARCHIVE_URL@|$url|" -e "s|@ARCHIVE_SHA256@|$checksum|" > install.sh
    bash -n install.sh
    chmod 755 install.sh
fi

mv -f -- "$archive" "$archive.sha256" "$output/"
if [[ -n "$release_repo" ]]; then
    mv -f -- install.sh "$output/"
fi
cat "$output/$archive.sha256"
