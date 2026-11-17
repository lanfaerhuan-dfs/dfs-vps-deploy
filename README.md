# DFS VPS Deploy

在 Debian 12/13 上部署或迁移 Xray VLESS + REALITY，使用 Caddy 提供带 HTTPS 的完整 Mihomo 订阅。终端菜单支持新建、迁移和维护。默认 Xray-core 26.2.6，REALITY TCP 443，ACME HTTP-01 TCP 80，订阅 HTTPS TCP 8443。

## 首次部署

1. 准备一台带公网 IPv4 的 Debian 12/13（amd64 或 arm64）VPS，等待 cloud-init 结束。使用 root SSH 登录并保留当前会话。Debian 云镜像通常已有 `python3`、`iproute2` 和 CA 根证书。极简镜像可用软件源补装这三个包。
2. 新建时在 DuckDNS 网页添加一个子域名即可，安装器会写入 IPv4。迁移时使用原有子域名。准备 DuckDNS Token。若有云防火墙，允许实际 SSH 端口、TCP 80、443、8443。使用旧 URL 的其他可用 HTTPS 端口时，改为放行该端口。
3. 运行已固定版本并验证 SHA-256 的安装命令，选择 **1. 新建节点**。输入域名、设置加密备份密码，核对新旧 DNS 地址后输入 Token 和 `y`。
4. 看到 `DEPLOY SUCCESS` 后，把打印的 HTTPS URL 导入 Clash Verge Rev / Mihomo。从另一个网络验证可下载订阅并尝试经节点访问。把加密备份下载到 VPS 之外，密码分开保存。

### GitHub 一条命令

维护者先按 [发布步骤](docs/RELEASE.md) 创建 GitHub 发布，并从可信渠道提供以下四个值。把 `OWNER/REPO`、`TAG`、`ARCHIVE` 和 `SHA256` 替换为对应的发布值，再在 VPS 的 root 终端执行一行。可信摘要应在执行命令前拿到，并与具体发行件绑定。

```bash
REPO='OWNER/REPO' TAG='v0.1.0' ASSET='ARCHIVE' SHA256='SHA256' bash -c 'set -euo pipefail; umask 077; d=$(mktemp -d); trap '\''rm -rf -- "$d"'\'' EXIT; cd "$d"; [[ "$SHA256" =~ ^[0-9a-f]{64}$ ]]; curl -q --proto "=https" --proto-redir "=https" --connect-timeout 10 --max-time 120 --retry 2 -fsSL "https://github.com/$REPO/releases/download/$TAG/$ASSET" -o source.tar.gz; printf "%s  source.tar.gz\n" "$SHA256" | sha256sum -c -; tar -xzf source.tar.gz; bash dfs-vps-deploy/bootstrap.sh "$@"' dfs-vps-deploy
```

若已验证并上传源码归档到 VPS，则解包后在同目录执行 `bash dfs-vps-deploy/bootstrap.sh`。安装命令末尾可追加参数，例如 `--dry-run`。生产使用前请阅读[验证范围](docs/TEST.md)。

## 迁移

| 旧机条件 | 在新 VPS 的最短操作 |
| --- | --- |
| 有加密备份 | 通过 SCP 上传备份文件。执行同一安装命令，选 **2 → 有加密备份**，输入原域名、备份路径和原密码。设置新机备份密码，确认 DNS 切换。随后刷新原客户端订阅并测试节点。 |
| 旧机失联，有原 URL 或路径 | 执行同一安装命令，选 **2 → 无备份**。优先输入原完整 URL，可保留它的 HTTPS 端口、路径和文件名。确认 DNS 切换后刷新原订阅，客户端会接收新身份参数。缺少原私钥时旧缓存节点无法继续使用。 |
| 缺少备份和路径 | 无法恢复原 URL 或 REALITY 私钥。若接受新 URL，在新机选择新建并在确认时检查该子域名的旧 DNS。随后用新 URL 导入客户端。 |

只输入路径时默认 HTTPS 8443。若输入的是目录路径，默认补上 `dfs-vless-reality-clash.yaml`。当旧 URL 有自定义文件名或端口时，请输入完整 URL。旧 URL 要使用同一个 DuckDNS 域名，不带查询参数、片段、百分号编码、通配符或路径穿越。无备份迁移保留新节点 TCP 443，因此不支持订阅也占用 TCP 443 的旧布局。

## 在已部署 VPS 上维护

重跑安装入口会打开维护菜单。服务和密钥保存在 VPS 上。下面的命令以 root 执行。

| 命令 | 功能 |
| --- | --- |
| `bash bootstrap.sh --dry-run` | 只读检查 Debian、架构、cloud-init、监听端口、路由、SSH 和公网 IPv4。全新机使用默认端口检查。 |
| `bash bootstrap.sh new --no-dns` | 配置本地服务，生成备份，进入 `STAGED`。 |
| `dfs-vps-deploy resume` | 继续暂存或中断的部署。切换前再次核对并确认 DNS。 |
| `dfs-vps-deploy --check` | 检查服务用户、配置、实际监听、REALITY 客户端探测、TLS、规则与 DNS。失败返回非零。 |
| `dfs-vps-deploy --show-subscription` | 显示当前 URL，暂存时显示预定 URL。 |
| `dfs-vps-deploy --backup` | 交互式设置密码并替换 VPS 上的迁移备份。 |
| `bash bootstrap.sh restore` | 无受管部署的新机，从加密备份恢复。GitHub 安装命令末尾也可追加 `restore`。 |
| `dfs-vps-deploy upgrade --xray-version X.Y.Z` | 使用经你验证的更高 Xray 版本。验证发行件与现有配置，失败时恢复上一版本与配置。 |
| `dfs-vps-deploy repair`、`restart` | 修复前保存本机文件快照，或仅重启，再做健康检查。 |
| `dfs-vps-deploy --rollback` | 恢复上次切换前的 A/AAAA。需要 Token 和确认，等待权威 DNS。 |
| `dfs-vps-deploy rollback-local` | 恢复上一份本机维护快照。 |
| `dfs-vps-deploy --version` | 显示工具版本。 |

加密备份在 `/var/lib/dfs-vps-deploy/migration.dfsbackup`。从本地执行以下命令，替换示例地址，并按需要传入 `-P <实际 SSH 端口>`。

```bash
scp root@NEW_VPS_IP:/var/lib/dfs-vps-deploy/migration.dfsbackup .
```

配置保留附件的 Fake-IP、国内 DNS/直连、ACL4SSR 每日更新及服务分流。节点地址始终为 DuckDNS 域名。客户端默认关闭 LAN 访问，DNS 监听 `127.0.0.1:1053`，避免客户端意外向局域网提供代理或 DNS。客户端的 LAN 需求可在本地单独配置。

- [故障排查](docs/TROUBLESHOOTING.md)
- [测试报告](docs/TEST.md)
- [安全审查](docs/SECURITY.md)
- [GitHub 打包与发布](docs/RELEASE.md)
