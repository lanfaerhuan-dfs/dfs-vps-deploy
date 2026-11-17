# 代码安全审查

| 审查点 | 实现和边界 |
| --- | --- |
| 下载与执行 | GitHub 安装先验证可信 SHA-256，再解包执行固定发行件。Xray 官方安装脚本固定完整提交和 SHA-256，脚本会核对 Xray 发行件的 SHA-256。升级包校验值来自同一个官方发布渠道，上游或发布渠道被攻破仍会影响信任。Caddy 与 Python 依赖使用 Debian APT 信任链。 |
| 凭证处理 | DuckDNS Token 与密码使用隐藏输入，保存在当前进程内存。DuckDNS 按其规范在 HTTPS 请求查询中传递 Token，HTTP 客户端不执行重定向。Token 不进入 argv、环境、备份或日志。订阅 URL 属于持有者凭证，通过终端按需显示，健康探测通过标准输入传给 curl。 |
| 订阅文件 | Caddy 仅在 HTTPS 站点为指定路由提供文件，HTTP 80 用于 ACME 与拒绝明文下载。应用配置与 YAML 为 root 所有、专用组可读，状态、备份和本机快照仅限 root。订阅必须包含 UUID、REALITY 公钥和 Short ID。掌握 URL 的人可取得这些参数，转发或公开 URL 相当于共享节点。 |
| 备份 | 随机盐和 nonce，scrypt（N=65536，r=8，p=1）与 AES-256-GCM。解密对头部版本和内容进行认证，随后检查字段、路径和 X25519 密钥对。备份不收录 DuckDNS Token 或 TLS 私钥。服务器状态和本机维护快照仍含明文私钥，依赖 VPS 的 root 权限和磁盘保护。 |
| 服务权限 | Xray 以 xray 用户运行，仅保留低端口能力，systemd 限制文件系统写入。Caddy 使用 Debian 包的受限用户。模板含常见私网和链路本地目的地址拒绝规则，应让服务远离敏感网络和元数据。主机之外的网络策略与安全组由云平台管理。 |
| 幂等与回滚 | root 管理操作互斥，关键文件原子替换。安装中断保留节点身份与阶段。DNS journal 先落盘并记录旧 A/AAAA；DuckDNS 的清除与更新是两次请求，可能存在短暂无记录窗口，API 缺少原子比较更新能力。避免在切换时同时通过其他程序管理同一域名。 |
| 客户端上游 | ACL4SSR 的规则以 HTTPS 每天更新，沿用附件的可变规则 URL。客户端信任上游规则内容，规则可在部署后改变。客户端默认只在本机开放代理和 DNS。Caddy 访问日志默认关闭，管理 API 关闭。 |

根用户可读取或修改本机任何状态。此工具默认无需 Vultr API 凭证，无法认证云防火墙状态。真实 ACME、外部端口和云防火墙验证仍需在你控制的一次性测试资源完成。完整生产安全验证范围见 [测试报告](TEST.md)。

上游接口和配置参考。[DuckDNS 官方 API 规范](https://www.duckdns.org/spec.jsp) · [固定 Xray 官方安装器提交](https://github.com/XTLS/Xray-install/commit/e741a4f56d368afbb9e5be3361b40c4552d3710d) · [Caddy TLS 指令](https://caddyserver.com/docs/caddyfile/directives/tls) · [Mihomo Rule Providers](https://wiki.metacubex.one/en/config/rule-providers/)
