"""Debian host checks and service lifecycle."""
import grp
import hashlib
import http.client
import ipaddress
import json
import logging
import os
import platform
import pwd
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .model import CONFIG, VERSION, DeployError, atomic_file, render, sync_directory, validate_mihomo
from .process import download, run


XRAY = Path("/usr/local/bin/xray")
XRAY_CONF = Path("/usr/local/etc/xray/config.json")
XRAY_UNIT = Path("/etc/systemd/system/xray.service")
XRAY_LOG = Path("/var/log/xray")
CADDY_CONF = Path("/etc/caddy/Caddyfile")
WWW = Path("/var/lib/dfs-vps-deploy-web")
SUBSCRIPTION = WWW / "subscription.yaml"
TOOL = Path("/usr/local/lib/dfs-vps-deploy")
LAUNCHER = Path("/usr/local/sbin/dfs-vps-deploy")
UPSTREAM = "e741a4f56d368afbb9e5be3361b40c4552d3710d"
UPSTREAM_SHA = "7f70c95f6b418da8b4f4883343d602964915e28748993870fd554383afdbe555"
ARCH = {"x86_64": "64", "aarch64": "arm64-v8a"}
LOCAL_PATHS = (XRAY, XRAY_CONF, XRAY_UNIT, CADDY_CONF, SUBSCRIPTION)
LOG = logging.getLogger("dfs")


@dataclass(frozen=True)
class Host:
    release: str
    arch: str
    ssh_ports: tuple
    address: str
    provider: str
    region: str = "未知"


def systemd(action, unit=None, *, require=True):
    args = ["systemctl", action] + ([unit] if unit else [])
    return run(args, f"systemd {action} {unit or ''}", timeout=70, require=require)


def systemd_field(unit, field):
    result = run(["systemctl", "show", "-p", field, "--value", unit], f"读取 {unit} {field}", require=False)
    value = result.stdout.decode().strip()
    if result.returncode and not (field == "LoadState" and value == "not-found"):
        raise DeployError(f"无法读取 {unit} 的 {field}。")
    return value


def listeners():
    result = run(["ss", "-H", "-lntp"], "读取 TCP 监听端口").stdout.decode()
    return [(int(line.split()[3].rsplit(":", 1)[1]), line) for line in result.splitlines() if line.strip()]


def ssh_ports(rows):
    detected = {port for port, line in rows if re.search(r'"sshd(?:-session|-listener)?"', line)}
    connection = os.environ.get("SSH_CONNECTION", "").split()
    if len(connection) == 4 and connection[-1].isdigit():
        detected.update(port for port, _ in rows if port == int(connection[-1]))
    if (systemd_field("ssh.socket", "LoadState") != "not-found"
            and systemd_field("ssh.socket", "ActiveState") == "active"):
        bound = systemd_field("ssh.socket", "Listen")
        detected.update(int(port) for port in re.findall(r":([0-9]+) \(Stream\)", bound)
                        if any(row_port == int(port) for row_port, _ in rows))
    if not detected:
        raise DeployError("无法确认实际 SSH 监听端口，停止配置防火墙。")
    return tuple(sorted(detected))


def public_ipv4():
    for endpoint in ("api.ipify.org", "ipv4.icanhazip.com"):
        connection = http.client.HTTPSConnection(endpoint, timeout=6)
        try:
            connection.request("GET", "/")
            reply = connection.getresponse()
            addr = ipaddress.IPv4Address(reply.read(128).decode().strip())
            if reply.status == 200 and addr.is_global and not addr.is_multicast:
                return str(addr)
        except (OSError, ValueError, http.client.HTTPException):
            pass
        finally:
            connection.close()
    raise DeployError("无法从 HTTPS 服务确认公网 IPv4，请检查 DNS 和出站连接。")


def read_dmi(name):
    try:
        return (Path("/sys/class/dmi/id") / name).read_text().strip()
    except OSError:
        return ""


def inspect(ports, *, managed=False, read_only=False):
    if os.geteuid() != 0:
        raise DeployError("请使用 root 运行。")
    try:
        os_info = platform.freedesktop_os_release()
    except OSError:
        raise DeployError("无法读取系统版本。") from None
    if os_info.get("ID") != "debian" or os_info.get("VERSION_ID") not in ("12", "13"):
        raise DeployError("需要 Debian 12 或 Debian 13。")
    arch = platform.machine()
    if arch not in ARCH:
        raise DeployError("仅支持 Debian amd64 和 arm64。")
    if not Path("/run/systemd/system").is_dir():
        raise DeployError("需要以 systemd 启动的 Debian VPS。")
    has_cloud_init = shutil.which("cloud-init")
    if has_cloud_init:
        args = ["cloud-init", "status"] + ([] if read_only else ["--wait"])
        check = run(args, "检查 cloud-init", timeout=160, require=False)
        result = check.stdout.decode(errors="replace")
        if check.returncode or not re.search(r"(?m)^status:\s*(done|disabled)\s*$", result):
            raise DeployError("cloud-init 尚未完成或存在错误，请先检查 cloud-init status --long。")

    rows = listeners()
    for port in ports:
        expected = "xray.service" if len(ports) > 1 and port == ports[1] else "caddy.service"
        pid = systemd_field(expected, "MainPID") if managed else "0"
        for bound_port, detail in rows:
            if bound_port == port and (pid == "0" or f"pid={pid}," not in detail):
                raise DeployError(f"TCP {port} 已被其他进程占用，请检查 ss -lntp。")
    if not managed and (
        any(os.path.lexists(path) for path in (XRAY, XRAY_CONF.parent, XRAY_UNIT, CADDY_CONF.parent,
                                              XRAY_LOG, WWW, TOOL, LAUNCHER))
        or any(systemd_field(unit, "LoadState") != "not-found" for unit in ("xray.service", "caddy.service"))
    ):
        raise DeployError("检测到未由本工具管理的 Xray、Caddy 或 DFS 文件，部署已终止。")

    routes = run(["ip", "-4", "route", "show", "default"], "检查 IPv4 默认路由").stdout
    if not routes.strip():
        raise DeployError("缺少默认 IPv4 路由。")
    ports_ssh = ssh_ports(rows)
    address = public_ipv4()
    provider = "未知"
    vendor = read_dmi("sys_vendor").lower()
    if vendor in {"vultr", "vultr, inc.", "vultr holdings corporation"}:
        provider = "Vultr"
    region = "未知"
    if has_cloud_init:
        cloud = run(["cloud-id"], "读取云厂商", timeout=8, require=False).stdout.decode().strip()
        vendors = {"vultr": "Vultr", "aws": "AWS", "gce": "Google Cloud", "azure": "Azure",
                   "digitalocean": "DigitalOcean", "hetzner": "Hetzner"}
        provider = vendors.get(cloud, provider)
        cloud_region = run(["cloud-init", "query", "v1.region"], "读取机房区域",
                           timeout=8, require=False).stdout.decode().strip()
        if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", cloud_region) and cloud_region.lower() not in ("none", "null"):
            region = cloud_region
    return Host(os_info.get("PRETTY_NAME", f"Debian {os_info['VERSION_ID']}"), arch, ports_ssh, address, provider, region)


def print_host(host, node=None):
    print(f"系统 {host.release} / {host.arch}")
    print(f"公网 IPv4 {host.address}，实际 SSH TCP {', '.join(map(str, host.ssh_ports))}")
    print(f"云厂商 {host.provider}，地区 {host.region}")
    if node:
        print(f"请核对云防火墙（如 Vultr Cloud Firewall）入站 TCP 80 / {node.reality_port} / {node.https_port}。")


def install_packages(*, python_only=False, refresh=True):
    options = ["-o", "Dpkg::Use-Pty=0", "-o", "DPkg::Lock::Timeout=60", "-o",
               "Acquire::Retries=2", "-o", "Acquire::http::Timeout=15", "-o", "Acquire::https::Timeout=15"]
    base = ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", *options]
    if refresh:
        run(base + ["update"], "更新 APT 索引", timeout=240)
    packages = ["python3-yaml", "python3-cryptography"]
    if not python_only:
        packages += ["curl", "jq", "openssl", "ca-certificates", "ufw", "bind9-dnsutils", "iproute2", "unzip", "caddy"]
    run(base + ["-y", "install", "--no-install-recommends", *packages], "安装 Debian 依赖", timeout=420)


def tool_launcher(version):
    return ("#!/bin/sh\nexport PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
            f'exec /usr/bin/python3 -EsB "{TOOL / version}/dfs_vps_deploy.py" "$@"\n').encode()


def install_tool():
    root = CONFIG.parent
    release = TOOL / VERSION
    launcher = tool_launcher(VERSION)
    current = None
    if os.path.lexists(LAUNCHER):
        if LAUNCHER.is_symlink() or not LAUNCHER.is_file() or LAUNCHER.stat().st_size > 4096:
            raise DeployError("维护命令不是本工具生成的入口，请检查其路径。")
        current = LAUNCHER.read_bytes()
        match = re.search(rb'/(\d+\.\d+\.\d+)/dfs_vps_deploy\.py"', current)
        if not match or current != tool_launcher(match[1].decode("ascii")):
            raise DeployError("维护命令不是本工具生成的入口，请检查其内容。")
        previous = tuple(map(int, match[1].split(b".")))
        if previous > tuple(map(int, VERSION.split("."))):
            raise DeployError("本机维护工具版本更高，请使用已安装的 dfs-vps-deploy。")

    if root != release and not release.exists():
        TOOL.mkdir(mode=0o755, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".dfs-install-", dir=TOOL) as tmp:
            staged = Path(tmp) / VERSION
            staged.mkdir(mode=0o755)
            for directory in ("config", "dfs"):
                shutil.copytree(root / directory, staged / directory, ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copyfile(root / "dfs_vps_deploy.py", staged / "dfs_vps_deploy.py")
            staged.rename(release)
            sync_directory(TOOL)
    if current != launcher:
        atomic_file(LAUNCHER, launcher, 0o755)


def hard_directory(path, owner, group, mode):
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise DeployError("受管目录是符号链接，停止部署。")
    os.chown(path, owner, group)
    path.chmod(mode)


def identity(user):
    try:
        account = pwd.getpwnam(user)
        group = grp.getgrnam(user)
    except KeyError:
        raise DeployError(f"缺少系统账号或组 {user}。") from None
    if account.pw_uid == 0 or group.gr_gid == 0 or account.pw_gid != group.gr_gid:
        raise DeployError(f"系统账号 {user} 权限异常。")
    return account.pw_uid, group.gr_gid


def ensure_xray_account():
    try:
        pwd.getpwnam("xray")
    except KeyError:
        run(["useradd", "--system", "--user-group", "--no-create-home", "--home-dir", "/nonexistent",
             "--shell", "/usr/sbin/nologin", "xray"], "创建 Xray 系统账号")
    return identity("xray")


def xray_installed_version(binary=XRAY):
    reply = run([str(binary), "version"], "读取 Xray 版本").stdout.decode(errors="replace")
    match = re.search(r"(?m)^Xray ([0-9]+\.[0-9]+\.[0-9]+)\s", reply)
    if not match:
        raise DeployError("无法识别 Xray 发行版本。")
    return match[1]


def install_xray(version):
    xuid, xgid = ensure_xray_account()
    hard_directory(XRAY_CONF.parent, 0, xgid, 0o750)
    hard_directory(XRAY_LOG, xuid, xgid, 0o750)
    atomic_file(XRAY_UNIT, (CONFIG / "xray.service").read_bytes(), 0o644)
    systemd("daemon-reload")
    if XRAY.exists() and xray_installed_version() == version:
        return
    if systemd_field("xray.service", "ActiveState") in ("active", "activating"):
        raise DeployError("检测到运行中的 Xray，版本变更请使用维护升级。")
    with tempfile.TemporaryDirectory(prefix="dfs-official-") as tmp:
        installer = Path(tmp) / "install-release.sh"
        download(f"https://raw.githubusercontent.com/XTLS/Xray-install/{UPSTREAM}/install-release.sh", installer)
        if hashlib.sha256(installer.read_bytes()).hexdigest() != UPSTREAM_SHA:
            raise DeployError("Xray 官方安装脚本 SHA-256 校验失败。")
        # The pinned installer uses curl internally. Bound each download and forbid
        # redirects to non-HTTPS endpoints without editing the upstream script.
        curl_wrapper = Path(tmp) / "curl"
        atomic_file(curl_wrapper, b"""#!/bin/sh
exec /usr/bin/curl -q --proto '=https' --proto-redir '=https' --connect-timeout 8 --max-time 65 --retry 2 --retry-max-time 90 "$@"
""", 0o700)
        run(["env", "-i", f"PATH={tmp}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
             "HOME=/root", "LANG=C", "/bin/bash", str(installer), "install", "--version", f"v{version}",
             "-u", "xray", "--no-update-service", "--without-geodata", "--without-logfiles"],
            "执行固定版本的 Xray 官方安装器", timeout=240, require=False)
    if xray_installed_version() != version:
        raise DeployError("Xray 官方安装未产出所请求版本。")


def configure_firewall(ssh, ports):
    for port in (*ssh, *ports):
        run(["ufw", "prepend", "allow", f"{port}/tcp"], f"放行 UFW TCP {port}")
    run(["ufw", "--force", "enable"], "启用 UFW", timeout=60)
    check_firewall(ssh, ports)


def check_firewall(ssh, ports):
    rules = run(["env", "LC_ALL=C", "ufw", "status", "verbose"], "检查 UFW").stdout.decode()
    if not rules.startswith("Status: active"):
        raise DeployError("UFW 当前未启用。")
    for port in set(ssh) | set(ports):
        allow = re.search(rf"(?m)^{port}/tcp\s+ALLOW(?: IN)?\s+Anywhere\s*$", rules)
        if not allow:
            raise DeployError(f"UFW 缺少 TCP {port} 的 IPv4 放行规则。")
        for line in rules[:allow.start()].splitlines():
            match = re.search(r"^(.*?)\s+(?:DENY|REJECT)(?: IN)?\s+(.+)$", line)
            if match and "(v6)" not in line:
                destination = match[1]
                simple_port = re.fullmatch(r"([0-9]+)(?:/(tcp|udp))?", destination)
                if not simple_port or (simple_port[2] != "udp" and int(simple_port[1]) == port):
                    raise DeployError(f"UFW 中较早的拒绝规则可能覆盖 TCP {port}，请检查 ufw status numbered。")


def restricted(path, user, mode):
    _, gid = identity(user)
    info = path.stat(follow_symlinks=False)
    if path.is_symlink() or info.st_uid != 0 or info.st_gid != gid or info.st_mode & 0o777 != mode:
        raise DeployError(f"{path} 所有者或权限异常。")


def caddy_bytes(node, live):
    if live:
        return render(node, "Caddyfile.tpl", www_root=WWW)
    return (CONFIG / "Caddyfile.staged").read_bytes()


def service_validation(xray_conf, caddy_conf, binary=XRAY):
    run(["runuser", "-u", "xray", "--", str(binary), "run", "-test", "-config", str(xray_conf)],
        "以 xray 用户验证 Xray 配置")
    run(["runuser", "-u", "caddy", "--", "caddy", "validate", "--adapter", "caddyfile", "--config",
         str(caddy_conf)], "以 caddy 用户验证 Caddy 配置")


def write_configs(node, live, *, binary=XRAY):
    _, xgid = identity("xray")
    _, cgid = identity("caddy")
    yaml = render(node, f"mihomo-v{node.template}.yaml.tpl")
    validate_mihomo(yaml, node)
    xray = render(node, "xray.json.tpl")
    production = caddy_bytes(node, True)
    # Both staged candidates can be read by their respective service group only.
    hard_directory(XRAY_CONF.parent, 0, xgid, 0o750)
    hard_directory(WWW, 0, cgid, 0o750)
    with tempfile.TemporaryDirectory(dir=XRAY_CONF.parent, prefix=".dfs-validate-") as xtmp:
        with tempfile.TemporaryDirectory(dir=CADDY_CONF.parent, prefix=".dfs-validate-") as ctmp:
            xcandidate, ccandidate = Path(xtmp) / "config.json", Path(ctmp) / "Caddyfile"
            hard_directory(xcandidate.parent, 0, xgid, 0o750)
            hard_directory(ccandidate.parent, 0, cgid, 0o750)
            atomic_file(xcandidate, xray, 0o640, group=xgid)
            atomic_file(ccandidate, production, 0o640, group=cgid)
            service_validation(xcandidate, ccandidate, binary)
    atomic_file(XRAY_CONF, xray, 0o640, group=xgid)
    atomic_file(SUBSCRIPTION, yaml, 0o640, group=cgid)
    atomic_file(CADDY_CONF, production if live else caddy_bytes(node, False), 0o640, group=cgid)


def set_caddy(node, live, original=None):
    _, cgid = identity("caddy")
    atomic_file(CADDY_CONF, caddy_bytes(node, live) if original is None else original, 0o640, group=cgid)
    systemd("restart", "caddy.service")
    systemd("is-active", "caddy.service")


def restart_services():
    systemd("daemon-reload")
    for unit in ("xray.service", "caddy.service"):
        systemd("enable", unit)
        systemd("restart", unit)


def verify_listener(port, unit):
    systemd("is-active", unit)
    pid = systemd_field(unit, "MainPID")
    if pid == "0" or not any(value == port and f"pid={pid}," in detail for value, detail in listeners()):
        raise DeployError(f"{unit} 未监听预期的 TCP {port}。")


def test_target(sni):
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.set_alpn_protocols(["h2"])
    try:
        with socket.create_connection((sni, 443), timeout=8) as tcp:
            with context.wrap_socket(tcp, server_hostname=sni) as tls:
                if tls.selected_alpn_protocol() == "h2":
                    return
    except OSError:
        pass
    raise DeployError("REALITY 目标无法建立受信任的 TLS 1.3 / HTTP/2 连接。")


def client_config(node, address, socks_port):
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [{"protocol": "socks", "listen": "127.0.0.1", "port": socks_port}],
        "outbounds": [{
            "protocol": "vless",
            "settings": {"vnext": [{"address": address, "port": node.reality_port,
                                   "users": [{"id": node.uuid, "flow": "xtls-rprx-vision", "encryption": "none"}]}]},
            "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {
                "serverName": node.sni, "fingerprint": "chrome", "publicKey": node.public_key,
                "shortId": node.short_id, "spiderX": "/",
            }},
        }],
    }


def reality_probe(node, *, binary=XRAY, endpoint="https://www.gstatic.com/generate_204", address="127.0.0.1"):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="dfs-reality-") as tmp:
        config = Path(tmp) / "client.json"
        from .model import json_bytes
        atomic_file(config, json_bytes(client_config(node, address, port)))
        with subprocess.Popen([str(binary), "run", "-config", str(config)], stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as client:
            try:
                for _ in range(30):
                    if client.poll() is not None:
                        break
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                            break
                    except OSError:
                        time.sleep(0.1)
                if client.poll() is None:
                    response = run(["curl", "-q", "--noproxy", "", "--proxy", f"socks5h://127.0.0.1:{port}",
                                    "--proto", "=http,https", "--connect-timeout", "8", "--max-time", "15",
                                    "-sS", "-o", "/dev/null", "-w", "%{http_code}", endpoint],
                                   "REALITY 客户端流量探测", timeout=18, require=False)
                    if response.returncode == 0 and response.stdout == b"204":
                        return
            finally:
                if client.poll() is None:
                    client.kill()
    raise DeployError("REALITY/VLESS 客户端探测失败，请检查 Xray 日志与出站连接。")


def service_health(node, *, live, verify_caddy_tls=True):
    if xray_installed_version() != node.xray_version:
        raise DeployError("Xray 运行文件版本与部署状态不符。")
    for unit, user in (("xray.service", "xray"), ("caddy.service", "caddy")):
        uid, _ = identity(user)
        pid = systemd_field(unit, "MainPID")
        if systemd_field(unit, "User") != user or not pid.isdigit() or int(pid) <= 0:
            raise DeployError(f"{unit} 未以预期用户运行。")
        try:
            proc_status = Path(f"/proc/{pid}/status").read_text()
        except OSError:
            raise DeployError(f"{unit} 进程已退出。") from None
        uid_line = re.search(r"(?m)^Uid:\s+([0-9]+)\s+([0-9]+)", proc_status)
        if not uid_line or any(int(value) != uid for value in uid_line.groups()):
            raise DeployError(f"{unit} 主进程 UID 异常。")
        if user == "xray" and not Path(f"/proc/{pid}/exe").samefile(XRAY):
            raise DeployError("运行中的 Xray 与磁盘上的受管二进制不同。")
    for path, user, mode in ((XRAY_CONF.parent, "xray", 0o750), (XRAY_CONF, "xray", 0o640),
                             (CADDY_CONF, "caddy", 0o640), (WWW, "caddy", 0o750),
                             (SUBSCRIPTION, "caddy", 0o640)):
        restricted(path, user, mode)
    service_validation(XRAY_CONF, CADDY_CONF)
    if XRAY_CONF.read_bytes() != render(node, "xray.json.tpl") or CADDY_CONF.read_bytes() != caddy_bytes(node, live):
        raise DeployError("服务配置与当前部署状态不符，可使用维护修复。")
    validate_mihomo(SUBSCRIPTION.read_bytes(), node, allow_legacy=True)
    verify_listener(node.reality_port, "xray.service")
    verify_listener(80, "caddy.service")
    if live:
        verify_listener(node.https_port, "caddy.service")
        if verify_caddy_tls:
            https_once(node)
    if curl_status(node.domain, 80, "http", node.route) != (0, b"404"):
        raise DeployError("本机 HTTP 80 未正确拒绝明文订阅访问。")
    reality_probe(node)


def curl_status(domain, port, protocol, route, output="/dev/null", connect="127.0.0.1"):
    # Route is a credential. Use curl's standard input, never the process argv.
    url = f'url = "{protocol}://{domain}:{port}{route}"\n'.encode()
    response = run(["curl", "-q", "--noproxy", "*", "--proto", f"={protocol}", "--resolve",
                    f"{domain}:{port}:{connect}", "--connect-timeout", "5", "--max-time", "10",
                    "--max-filesize", str(1024 * 1024), "-sS", "-o", str(output), "-w", "%{http_code}",
                    "--config", "-"], "订阅 HTTP/TLS 探测", data=url, timeout=13, require=False)
    return response.returncode, response.stdout


def https_once(node):
    with tempfile.TemporaryDirectory(prefix="dfs-https-") as tmp:
        body = Path(tmp) / "received.yaml"
        if curl_status(node.domain, node.https_port, "https", node.route, body) != (0, b"200"):
            raise DeployError("未获得可信 TLS 下的 HTTP 200。请检查 Caddy、ACME 和端口。")
        if body.read_bytes() != SUBSCRIPTION.read_bytes():
            raise DeployError("HTTPS 返回的 YAML 与本机受管订阅不一致。")


def wait_https(node, seconds=220):
    deadline = time.monotonic() + seconds
    while True:
        try:
            https_once(node)
            return
        except DeployError:
            if time.monotonic() >= deadline:
                raise DeployError("HTTPS 订阅验证超时。查看 Caddy journal，检查 DNS 缓存、入站 TCP 80 与 ACME 频率限制。") from None
        time.sleep(5)


def stage_upgrade(version, tmp, arch):
    file = f"Xray-linux-{ARCH[arch]}.zip"
    archive, digest, binary = (tmp / file, tmp / (file + ".dgst"), tmp / "xray")
    url = f"https://github.com/XTLS/Xray-core/releases/download/v{version}/{file}"
    download(url, archive)
    download(url + ".dgst", digest)
    match = re.search(rb"(?im)^SHA2?-256=\s*([0-9a-f]{64})\s*$", digest.read_bytes())
    if not match or hashlib.sha256(archive.read_bytes()).hexdigest().encode() != match[1].lower():
        raise DeployError("Xray 升级发行件 SHA-256 校验失败。")
    with zipfile.ZipFile(archive) as release:
        if release.getinfo("xray").file_size > 150 * 1024 * 1024:
            raise DeployError("Xray 发行件体积异常。")
        atomic_file(binary, release.read("xray"), 0o755)
    if xray_installed_version(binary) != version:
        raise DeployError("Xray 升级发行件版本不符。")
    return binary
