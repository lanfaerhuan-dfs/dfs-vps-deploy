#!/bin/bash
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
unset PYTHONPATH PYTHONHOME
root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ ! -x /usr/bin/python3 ]]; then
    printf '%s\n' '缺少 /usr/bin/python3。请先运行 apt-get update && apt-get install -y python3。' >&2
    exit 1
fi
exec /usr/bin/python3 -EsB "$root_dir/dfs_vps_deploy.py" "$@"
