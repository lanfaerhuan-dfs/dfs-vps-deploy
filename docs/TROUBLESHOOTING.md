# 故障排查

先运行 `dfs-vps-deploy --check`，查看阶段和失败的检查。部署未安装本地入口时，用 `bash bootstrap.sh --check`。维护日志在 `/var/lib/dfs-vps-deploy/deploy.log`，仅 root 可读。查阅服务日志前请在本地操作，分享输出时删除域名、完整订阅 URL、UUID、密钥和其他敏感字段。

```bash
journalctl -u xray -u caddy -b -n 100 --no-pager
ss -lntp
ufw status numbered
```

| 现象 | 检查和处理 |
| --- | --- |
| 初始化、网络或端口冲突 | `cloud-init status --long`、`ip -4 route show default` 和 `ss -lntp`。需要确认实际 SSH 监听，避免在已有站点上改变防火墙。若安装器发现已有 Xray/Caddy 或外部服务占端口，请使用干净 VPS 或先按该服务自己的维护流程处理。 |
| Xray 权限或探测失败 | 查看 `/var/log/xray/error.log`、`systemctl show -p User -p MainPID xray`。确认有效 SNI 在该 VPS 能建立 TLS 1.3 与 HTTP/2，出站能访问连接探测目标，检查运行用户和配置目录组读权限。受管配置走 `repair`。 |
| 切换 A/AAAA 未通过 | 权威服务器须取得一致 A/AAAA，两个公共解析器须收敛。验证外部 DNS 可用，核对 Token、域名和更新确认。旧 AAAA 需要清除，否则 IPv6 客户端可能继续访问旧机。 |
| Caddy 证书超时 | 看 journal 里的 ACME 错误。核对权威及公共 DNS，云防火墙和 UFW 入站 TCP 80。若返回限流，请按 CA 提示等待，再 `resume`。反复重启不会加速 DNS 或 CA 状态。 |
| VPS 健康检查绿，外网不能导入 | 从独立网络尝试访问 HTTPS 端口和 REALITY 端口。检查云防火墙、安全组、入站转发和运营商限制；本机已验证回环连接，但外部路径取决于实际网络。 |
| 迁移后客户端节点仍旧 | 刷新原订阅，确认配置中域名、端口、UUID 和 PublicKey 是新一份订阅的值。域名未换时注意 DNS 缓存，旧密钥缓存需要更新。 |
| DNS 事务中断 | `dfs-vps-deploy --rollback` 确认后读取原 A/AAAA 并恢复；随后 `resume` 重试。权威 DNS 已被第三方改动时，工具会拒绝覆盖，管理员可核对 `dns-journal.json` 后在 DuckDNS 后台处理。 |
| 中途退出或系统重启 | 重跑安装入口，选维护里的继续部署。若提示未完成本机维护，先 `rollback-local`。 |
| 无法解密备份 | 检查是否为 DFS 备份、完整下载以及原密码。若密码无法找回，只能使用旧 URL 或访问路径重建。 |

DNS 回滚只确认权威记录恢复。公共解析器和客户端按各自 TTL 更新。如果旧 VPS 已失联，恢复原 DNS 也不会使旧机重新可用。回滚期间保留新旧 VPS，并维持现有 SSH 会话。
