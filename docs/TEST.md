# 测试报告

v0.1.2 于 2026-11-19 通过发布前测试。macOS 上 44 项全部通过。Debian 12 和 13 离线容器各通过 42 项，各跳过 2 项需要 Mihomo 二进制的测试。这两项已在 macOS 上运行，覆盖完整配置解析和两份 IPv4 规则集的实际加载。维护测试覆盖从旧版启动器更新、重复执行和复制中断后重试。

## 首版验证记录

以下为 2026-11-17 的结果。测试使用合成节点、一次性容器与回环监听。

| 环境 | 结果 |
| --- | --- |
| macOS ARM64，Python 3.14，Xray 26.2.6、Caddy 2.6.2、Mihomo 1.19.19 | 32 项全部通过。真实 Xray 进行 VLESS + REALITY + Vision 握手并拒绝错误 UUID。Caddy 使用离线测试证书验证 TLS、订阅路径、错误路径及 HTTP 404。Mihomo 核心通过完整生成配置的 `-t` 检查。 |
| Debian 12 ARM64 离线容器 | 31 项通过，1 项 Mihomo 二进制测试因容器内未提供可执行文件而跳过。Xray 26.2.6 与 Debian Caddy 2.6.2 实际运行。 |
| Debian 13 ARM64 离线容器 | 31 项通过，1 项 Mihomo 二进制测试因容器内未提供可执行文件而跳过。Xray 26.2.6 与 Debian Caddy 2.6.2 实际运行。 |
| 一次性 Debian 容器补充检查 | Debian 12 使用真实 `xray`、`caddy` 用户验证配置，确认普通用户无法读取订阅和私钥配置，破坏文件组后 Xray 配置测试失败。Debian 13 使用 systemd 模拟层运行固定的官方 Xray 安装脚本并验证实际安装版本。 |
| ACL4SSR | 27 个模板规则 URL 在验证时均返回 HTTP 200。 |
| 安装和发布入口 | 两个 Bash 入口通过语法检查，归档可解包，启动器可执行 `--version`，发布命令在本地模拟下载时核对 SHA-256。 |

## 场景覆盖

| 场景 | 验证方式 |
| --- | --- |
| Debian 12/13 全新部署 | 容器内依赖和真实二进制，主机预检查、APT/服务编排与新建流程使用模拟层。 |
| 新建订阅 | 新建入口、随机密钥和路径、安全权限、YAML 解析、真实 Caddy 路由。 |
| 有备份迁移 | 真实加密备份经迁移入口恢复，字节对比生成配置，保留 URL 与密钥对。 |
| 无备份保留 URL | 自定义路径、文件名与非冲突 HTTPS 端口，重新生成身份。 |
| 无备份无路径 | 报错退出，状态与备份文件不存在。 |
| 重复执行 | 进入维护，原身份不变；已有部署拒绝显式新建。 |
| 非 22 SSH | 模拟 TCP 22022 与 socket 激活，验证先放行实际 SSH 再启用 UFW。 |
| Xray 权限错误 | 模拟非预期 UID，容器内实测组权限错误。 |
| 443 已被占用 | 模拟陌生监听进程，预检查拒绝。 |
| Caddy 签发失败 | 模拟证书超时、可信 TLS 验证错误；Caddy 实际解析 HTTP-01 配置。 |
| DuckDNS 更新失败 | 模拟 KO，验证 Token 审查、禁止重定向、清除后失败的事务恢复。 |
| AAAA 残留 | 模拟权威和公共解析器残留 AAAA、A/AAAA 回滚与仅 AAAA 回滚。 |
| Mihomo YAML 无效 | 损坏语法、重复键、规则/组引用错误、循环引用、规则次序错误。 |
| DNS 后健康失败 | 模拟 ACME 检查超时，回滚到旧 A/AAAA 并回到暂存，不输出部署成功。 |
| 备份与凭证保护 | 正确和错误密码、篡改检测、随机盐和 nonce、0600 文件检查、敏感值不进入 argv。 |

真实二进制测试发现并修正了两项实际兼容问题。YAML 校验器现接受附件中 `Ruleset/` 子目录，Xray 配置移除了 26.2.6 不接受的 IPv4 映射 IPv6 网段写法。

## 复跑

```bash
# Debian 测试容器。DEBIAN 可用 12 或 13。
docker build -f tests/Dockerfile --build-arg DEBIAN=13 -t dfs-vps-test tests
docker run --rm --network none -v "$PWD:/repo:ro" dfs-vps-test

# 在已有 Python 3.11+、PyYAML、cryptography 的环境运行测试。
python3 -B -m unittest discover -s tests -v
```

可设置 `TEST_XRAY`、`TEST_CADDY`、`TEST_MIHOMO` 和 `TEST_COUNTRY_MMDB` 的绝对文件路径来启用二进制测试。二进制请从上游官方固定版本发布获取并核对摘要。MMDB 用于 Mihomo 测试时在离线环境加载 GEOIP。

## 真实 VPS 验收待办

请使用临时 Debian 12/13 VPS、一次性 DuckDNS 子域名和第二个外网网络。容器结果覆盖编排与二进制行为。真实 VPS 的 PID 1/systemd 生命周期、UFW、云防火墙、DuckDNS 写入、Let's Encrypt 签发/续期和公网访问仍需验收。amd64 的实际二进制运行也有待实测。

1. 测试新机 `--dry-run` 和 `--no-dns`。记录云防火墙设置及第二条 SSH 会话能否持续访问。
2. `resume` 并确认 DNS 切换。外网验证 HTTP 80 明文请求不返回 YAML，HTTPS 订阅 HTTP 200 且证书可信，从 Clash Verge Rev 或 Mihomo 通过节点发起代理请求。
3. 下载备份，在第二台临时 VPS 恢复同 URL。核对原 VLESS 身份、DNS A/AAAA 和客户端刷新。另开隔离轮次测无备份迁移。
4. 用临时资源模拟 ACME 不通并检查 DNS 回滚，验证 TTL 缓存行为。检查故障日志不含 Token 或私钥。观察一次证书续期周期或用独立环境模拟相关条件。
