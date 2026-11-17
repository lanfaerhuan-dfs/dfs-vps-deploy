"""Interactive entry points and durable deployment workflows."""
import argparse
import fcntl
import getpass
import importlib.util
import json
import logging
import os
import platform
import shutil
import signal
import sys
import tempfile
import warnings
import zipfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from . import linux
from .dns import DNS, DNSTransaction, Records
from .model import (DEFAULT_XRAY, MAGIC, VERSION, DeployError, Node, atomic_file, domain_name, json_bytes,
                    make_backup, parse_legacy, restore_backup, sync_directory, xray_version)


STATE_DIR = Path("/var/lib/dfs-vps-deploy")
LOG = logging.getLogger("dfs")
BANNER = "=====================================\n          DFS VPS DEPLOY\n====================================="
OP_ERRORS = (DeployError, OSError, ValueError, KeyboardInterrupt, zipfile.BadZipFile)


@dataclass(frozen=True)
class Deployment:
    node: Node
    mode: str
    phase: str

    @property
    def ports(self):
        return 80, self.node.reality_port, self.node.https_port


class Store:
    def __init__(self, directory=STATE_DIR):
        self.directory = directory
        self.file = directory / "state.json"
        self.backup = directory / "migration.dfsbackup"
        self.journal = directory / "dns-journal.json"
        self.previous = directory / "local-previous"
        self.pending = directory / "maintenance-pending"

    def check_directory(self):
        info = self.directory.lstat()
        if self.directory.is_symlink() or info.st_uid != os.geteuid() or info.st_mode & 0o777 != 0o700:
            raise DeployError("部署状态目录必须由当前用户所有，权限为 0700。")

    def load(self):
        if not os.path.lexists(self.directory):
            return None
        self.check_directory()
        if not self.file.exists():
            return None
        data = json.loads(self.file.read_bytes())
        if (data.get("schema") != 1 or data.get("mode") not in ("NEW", "MIGRATE")
                or data.get("phase") not in ("provisioning", "staged", "ready")):
            raise DeployError("部署状态格式或版本无效。")
        return Deployment(Node.checked(data["node"]), data["mode"], data["phase"])

    def save(self, deployment):
        atomic_file(self.file, json_bytes({"schema": 1, "mode": deployment.mode, "phase": deployment.phase,
                                          "node": asdict(deployment.node)}))

    @contextmanager
    def lock(self):
        self.directory.mkdir(mode=0o700, exist_ok=True)
        self.check_directory()
        fd = os.open(self.directory / "lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "rb") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise DeployError("已有 DFS 管理操作正在运行。") from None
            handler = logging.FileHandler(self.directory / "deploy.log", encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            LOG.addHandler(handler)
            LOG.setLevel(logging.INFO)
            try:
                yield
            except OP_ERRORS as exc:
                LOG.error("操作失败 [%s] %s", type(exc).__name__, str(exc) if isinstance(exc, DeployError) else "")
                raise
            finally:
                LOG.removeHandler(handler)
                handler.close()

    def transaction(self, node):
        return DNSTransaction(self.journal, DNS(node.domain))

    def snapshot(self):
        with tempfile.TemporaryDirectory(prefix=".dfs-snapshot-", dir=self.directory) as tmp:
            candidate = Path(tmp) / "files"
            candidate.mkdir(mode=0o700)
            for index, source in enumerate((*linux.LOCAL_PATHS, self.file, self.backup)):
                if source.exists():
                    atomic_file(candidate / str(index), source.read_bytes())
            # No service/config changes are allowed before the new snapshot exists.
            old = self.directory / ".local-previous-old"
            if self.previous.exists():
                if old.exists():
                    shutil.rmtree(old)
                self.previous.rename(old)
            candidate.rename(self.previous)
            sync_directory(self.directory)
        atomic_file(self.pending, b"pending\n")
        if old.exists():
            shutil.rmtree(old)

    def restore_local(self, *, check=True):
        if not self.previous.exists():
            raise DeployError("没有可恢复的本机配置快照。")
        _, xgid = linux.identity("xray")
        _, cgid = linux.identity("caddy")
        modes = ((0o755, 0), (0o640, xgid), (0o644, 0), (0o640, cgid), (0o640, cgid), (0o600, 0), (0o600, 0))
        for index, (path, (mode, group)) in enumerate(zip((*linux.LOCAL_PATHS, self.file, self.backup), modes)):
            source = self.previous / str(index)
            if source.exists():
                atomic_file(path, source.read_bytes(), mode, group=group)
            else:
                path.unlink(missing_ok=True)
                sync_directory(path.parent)
        linux.restart_services()
        if check:
            state = self.load()
            linux.service_health(state.node, live=state.phase == "ready")
        self.pending.unlink(missing_ok=True)
        sync_directory(self.directory)


def note(message):
    print(message, flush=True)
    LOG.info(message)


def hidden(label):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            value = getpass.getpass(label)
    except getpass.GetPassWarning:
        raise DeployError("隐藏输入需要交互式终端，请通过 SSH 分配 TTY。") from None
    if len(value) > 1024:
        raise DeployError("输入超过长度限制。")
    return value


def password_new():
    first = hidden("设置加密备份密码（至少 12 个字符）：")
    if len(first) < 12 or first != hidden("再次输入备份密码："):
        raise DeployError("备份密码过短或两次输入不一致。")
    return first


def confirm(question):
    if input(f"{question} [y/N] ").strip().lower() not in ("y", "yes"):
        raise DeployError("操作已取消。")


def prerequisites():
    missing = [name for name in ("yaml", "cryptography") if importlib.util.find_spec(name) is None]
    if missing:
        linux.install_packages(python_only=True)
    return bool(missing)


def select_mode():
    print(BANNER)
    print("\n1. 新建节点（New）\n2. 迁移节点（Migrate）\n3. 维护节点（Maintain）\n")
    action = {"1": "new", "2": "migrate", "3": "maintain"}.get(input("请输入选项：").strip())
    if not action:
        raise DeployError("请选择 1、2 或 3。")
    return action


def prepare(store, action, args):
    mode = "NEW" if action == "new" else "MIGRATE"
    print(f"模式 {mode}")
    domain = domain_name(input("DuckDNS 子域名："))
    legacy, source, recovery_password = None, None, None
    if mode == "MIGRATE":
        choice = "1" if action == "restore" else input("1. 有加密备份，完整恢复  2. 无备份，重建节点：").strip()
        if choice == "1":
            if args.xray_version is not None:
                raise DeployError("完整恢复会保留备份的 Xray 版本，请部署后使用维护升级。")
            source = Path(input("加密备份文件路径：").strip()).expanduser()
            recovery_password = hidden("原备份密码：")
        elif choice == "2":
            print("没有备份时无法恢复原 REALITY 私钥。客户端需要刷新原订阅以获取新参数。")
            legacy = parse_legacy(hidden("旧订阅 URL 或访问路径："), domain)
        else:
            raise DeployError("请选择迁移方式。")
    version = xray_version(args.xray_version or DEFAULT_XRAY)
    required = (80, 443, legacy[0] if legacy else 8443) if source is None else (80,)
    host = linux.inspect(required)
    linux.print_host(host)
    print(f"部署域名 {domain}。UFW 将优先放行检测到的 SSH 端口，并放行所需服务端口。")
    confirm("确认准备部署")
    updated = prerequisites()
    if source:
        node = restore_backup(source, recovery_password)
        if node.domain != domain:
            raise DeployError("备份和输入的 DuckDNS 域名不一致。")
        host = linux.inspect((80, node.reality_port, node.https_port))
    else:
        node = Node.generate(domain, version, legacy)
    if legacy and len(node.route.split("/")[1]) < 24:
        print("保留短旧路径会增加订阅被猜中的风险，请核对其保密性。")
    password = password_new()
    state = Deployment(node, mode, "provisioning")
    # Write the backup before the state commit. If interrupted here, no service has changed.
    make_backup(store.backup, node, password)
    store.save(state)
    return provision(store, state, host, refresh=not updated)


def provision(store, state, host, refresh=True):
    node = state.node
    note("安装依赖与受管服务。")
    linux.install_packages(refresh=refresh)
    linux.install_tool()
    linux.configure_firewall(host.ssh_ports, state.ports)
    linux.install_xray(node.xray_version)
    linux.test_target(node.sni)
    linux.write_configs(node, live=False)
    linux.restart_services()
    linux.service_health(node, live=False)
    state = replace(state, phase="staged")
    store.save(state)
    note("Xray 配置、REALITY 客户端握手、Caddy HTTP 与 Mihomo YAML 检查通过。")
    return state


def final_checks(state):
    linux.wait_https(state.node)
    linux.service_health(state.node, live=True, verify_caddy_tls=False)


def print_success(state, host, backup):
    node = state.node
    print(f"\n{BANNER}\n")
    print(f"Mode          {state.mode}\nSystem        {host.release}\nXray          {node.xray_version} RUNNING")
    print(f"Caddy         RUNNING\nREALITY TCP   {node.reality_port} OK\nHTTPS TCP     {node.https_port} OK")
    print("DNS           OK\nCertificate   VALID\nMihomo YAML   VALID")
    print(f"\nDomain        {node.domain}\nServer IPv4   {host.address}\n\nSubscription\n{node.url}")
    print(f"\nBackup        {backup}\nStatus        DEPLOY SUCCESS")
    print("\n请立即从 VPS 下载加密备份，并在安全位置异地保存。迁移后请从外网刷新和验证订阅。")


def finish(store, state, host, no_dns):
    node = state.node
    if no_dns:
        note("STAGED：本地预检查通过，DNS 与 ACME 留待 resume。")
        print(f"加密迁移备份 {store.backup}\n继续部署请运行 dfs-vps-deploy resume。")
        return
    linux.print_host(host, node)
    linux.service_health(node, live=False)
    linux.check_firewall(host.ssh_ports, state.ports)
    check_backup(store)
    tx = store.transaction(node)
    before = tx.resolver.authoritative()
    after = Records(host.address)
    print(f"即将操作 {node.domain}\n原 DNS {before.describe()}\n新 DNS {after.describe()}")
    token = hidden("DuckDNS Token：")
    confirm("确认切换 DNS，并在此次切换检查失败时自动恢复原记录")
    note(f"开始切换 {node.domain} 的 DNS。")
    try:
        tx.begin(before, after, token)
        tx.resolver.wait(after, public=True, seconds=240)
        note("权威 DNS 与公共解析器 A/AAAA 检查通过，开始等待 ACME。")
        linux.set_caddy(node, live=True)
        final_checks(state)
        tx.resolver.wait(after, public=True, seconds=30)
        tx.commit()
        state = replace(state, phase="ready")
        store.save(state)
    except OP_ERRORS:
        journal = tx.read()
        if journal and journal["status"] in ("switching", "applied"):
            try:
                tx.rollback(token)
                note("已恢复原权威 DNS，公共解析器可能保留缓存。")
                linux.set_caddy(node, live=False)
            except OP_ERRORS:
                note("自动恢复未完成，请检查日志后运行 dfs-vps-deploy --rollback。")
        raise
    print_success(state, host, store.backup)


def check(store, state):
    host = linux.inspect(state.ports, managed=True, read_only=True)
    linux.print_host(host, state.node)
    print(f"工具 {VERSION} / Xray {state.node.xray_version} / 状态 {state.phase}")
    failures = []
    checks = [
        ("服务、REALITY 客户端、HTTPS 及 YAML", lambda: linux.service_health(state.node, live=state.phase == "ready")),
        ("UFW", lambda: linux.check_firewall(host.ssh_ports, state.ports)),
        ("DuckDNS A/AAAA", lambda: check_dns(state, host)),
        ("加密备份", lambda: check_backup(store)),
    ]
    for name, operation in checks:
        try:
            operation()
            print(f"{name} OK")
        except (DeployError, OSError) as exc:
            failures.append(name)
            print(f"{name}：{exc if isinstance(exc, DeployError) else '读取失败'}")
    if failures or state.phase != "ready" or store.pending.exists():
        raise DeployError("检查发现故障或部署尚未完成。")


def check_dns(state, host):
    dns = DNS(state.node.domain)
    records = dns.authoritative()
    print(f"权威记录 {records.describe()}")
    public = dns.public()
    for resolver, result in public.items():
        print(f"{resolver} {result.describe()}")
    if state.phase == "ready" and (records != Records(host.address) or any(r != records for r in public.values())):
        raise DeployError("解析结果尚未一致指向本机 IPv4，或仍存在 AAAA。")


def check_backup(store):
    if (not store.backup.is_file() or store.backup.is_symlink()
            or store.backup.stat().st_uid != os.geteuid() or store.backup.stat().st_mode & 0o777 != 0o600):
        raise DeployError("加密备份缺失或权限异常。请运行 backup。")
    with store.backup.open("rb") as stream:
        if stream.read(len(MAGIC)) != MAGIC:
            raise DeployError("加密备份格式无效。请运行 backup。")


def recover_dns(store, state):
    tx = store.transaction(state.node)
    journal = tx.read()
    if not journal:
        raise DeployError("没有可恢复的 DNS 切换。")
    if journal["status"] != "rolled_back":
        print(f"{state.node.domain} 将恢复至 {Records(**journal['before']).describe()}。")
        token = hidden("DuckDNS Token：")
        confirm("确认恢复 DNS")
        tx.rollback(token)
    else:
        confirm("确认恢复暂存配置并重启 Caddy")
    # State will require resume even if resetting Caddy is interrupted.
    store.save(replace(state, phase="staged"))
    linux.set_caddy(state.node, live=False)
    note("权威 DNS 已恢复。公共解析器可能仍缓存切换后的记录。")


def resume(store, state, args):
    if state.phase == "ready":
        return check(store, state)
    host = linux.inspect(state.ports, managed=True)
    if state.phase == "provisioning":
        state = provision(store, state, host)
    if not args.no_dns and store.journal.exists():
        tx = store.transaction(state.node)
        journal = tx.read()
        status = journal["status"]
        if status in ("switching", "rolling_back"):
            raise DeployError("发现未完成的 DNS 切换。请先运行 --rollback。")
        if status == "applied":
            tx.resolver.wait(Records(host.address), public=True, seconds=30)
            final_checks(state)
            state = replace(state, phase="ready")
            store.save(state)
            return print_success(state, host, store.backup)
        # An interruption after DNS rollback can leave the live Caddyfile on disk.
        linux.set_caddy(state.node, live=False)
    finish(store, state, host, args.no_dns)


def local_operation(store, state, action, version=None):
    if store.pending.exists():
        raise DeployError("上次维护未完结。请运行 rollback-local 恢复。")
    if state.phase != "ready":
        raise DeployError("请先完成部署再进行配置修复或升级。")
    node = state.node
    if action == "upgrade":
        current, desired = (tuple(map(int, v.split("."))) for v in (node.xray_version, version))
        if desired <= current:
            raise DeployError("升级版本必须高于当前版本。")
        linux.service_health(node, live=True)
        password = password_new()
    store.snapshot()
    try:
        if action == "repair":
            atomic_file(linux.XRAY_UNIT, (linux.CONFIG / "xray.service").read_bytes(), 0o644)
            linux.write_configs(node, live=True)
        else:
            with tempfile.TemporaryDirectory(prefix="dfs-upgrade-") as tmp:
                binary = linux.stage_upgrade(version, Path(tmp), platform.machine())
                linux.run([str(binary), "run", "-test", "-config", str(linux.XRAY_CONF)], "验证新版本与现有配置")
                atomic_file(linux.XRAY, binary.read_bytes(), 0o755)
            node = replace(node, xray_version=version)
        linux.restart_services()
        final_checks(replace(state, node=node))
        if action == "upgrade":
            make_backup(store.backup, node, password)
            store.save(replace(state, node=node))
    except OP_ERRORS:
        try:
            store.restore_local()
            note("维护失败，已恢复操作前的本机文件。")
        except OP_ERRORS:
            note("维护失败，恢复后的健康检查未通过。请检查 journal 或运行 rollback-local。")
        raise
    store.pending.unlink()
    sync_directory(store.directory)
    note("维护完成，服务及订阅检查通过。")


def maintain():
    print(BANNER)
    print("\n维护节点\n1. 健康检查\n2. 显示订阅 URL\n3. 生成加密备份\n4. 安全升级 Xray\n"
          "5. 备份并修复配置\n6. 重启并检查服务\n7. 继续部署\n8. 回滚 DNS\n9. 恢复本机快照")
    return {"1": "check", "2": "show-subscription", "3": "backup", "4": "upgrade", "5": "repair",
            "6": "restart", "7": "resume", "8": "rollback", "9": "rollback-local"}.get(
                input("请输入选项：").strip())


ACTIONS = ("new", "migrate", "restore", "maintain", "check", "show-subscription", "backup",
           "rollback", "rollback-local", "resume", "upgrade", "repair", "restart")


def arguments(argv):
    parser = argparse.ArgumentParser(prog="dfs-vps-deploy", description="Debian VLESS + REALITY 自动部署")
    parser.add_argument("action", choices=ACTIONS, nargs="?")
    aliases = parser.add_mutually_exclusive_group()
    for name in ("check", "show-subscription", "backup", "rollback"):
        aliases.add_argument("--" + name, dest="alias", action="store_const", const=name)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-dns", action="store_true")
    parser.add_argument("--xray-version", metavar="VERSION")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args(argv)
    if args.action and args.alias:
        parser.error("请使用一个子命令或操作参数。")
    args.action = args.action or args.alias
    if args.no_dns and args.action not in (None, "new", "migrate", "restore", "resume", "maintain"):
        parser.error("--no-dns 仅用于部署或续跑。")
    if args.xray_version and args.action not in (None, "new", "migrate", "upgrade"):
        parser.error("--xray-version 仅用于新建、无备份迁移或升级。")
    if args.dry_run and args.action not in (None, "new", "migrate", "restore", "resume", "check"):
        parser.error("--dry-run 只运行只读环境检查。")
    return args


def dispatch(store, args):
    state = store.load()
    action = args.action
    if state:
        if action is None and args.no_dns and state.phase != "ready":
            action = "resume"
        if action in (None, "maintain"):
            action = maintain()
        if store.pending.exists() and action not in ("rollback-local", "check", "show-subscription"):
            raise DeployError("检测到未完成的本机维护，请先运行 rollback-local。")
        if action in ("new", "migrate", "restore"):
            raise DeployError("已有部署状态，请使用 maintain 或 resume。")
        if action == "show-subscription":
            print(state.node.url)
        elif action == "check":
            check(store, state)
        elif action == "resume":
            resume(store, state, args)
        elif action == "backup":
            make_backup(store.backup, state.node, password_new())
            note(f"已生成加密备份 {store.backup}。请下载并异地保存。")
        elif action == "rollback":
            recover_dns(store, state)
        elif action == "rollback-local":
            if state.phase != "ready" and not store.pending.exists():
                raise DeployError("当前处于部署暂存阶段，请先 resume。")
            confirm("确认使用本机上一份快照恢复配置及二进制")
            store.restore_local()
            note("本机快照已恢复，服务检查通过。")
        elif action in ("repair", "upgrade"):
            version = None
            if action == "upgrade":
                version = xray_version(args.xray_version or input("经验证的 Xray 版本号：").strip())
            confirm("确认维护并重启服务")
            local_operation(store, state, action, version)
        elif action == "restart":
            confirm("确认重启 Xray 和 Caddy")
            linux.restart_services()
            check(store, state)
        else:
            raise DeployError("请选择维护菜单中的操作。")
    else:
        action = action or select_mode()
        if action not in ("new", "migrate", "restore"):
            raise DeployError("没有由本工具管理的部署。")
        state = prepare(store, action, args)
        host = linux.inspect(state.ports, managed=True)
        finish(store, state, host, args.no_dns)


def interrupt(_signum, _frame):
    raise KeyboardInterrupt


def main(argv=None):
    args = arguments(argv)
    os.umask(0o077)
    os.environ["LC_ALL"] = "C"
    if os.geteuid() != 0:
        print("错误：请通过 root SSH 会话运行。", file=sys.stderr)
        return 1
    for sig in (signal.SIGHUP, signal.SIGTERM):
        signal.signal(sig, interrupt)
    try:
        store = Store(STATE_DIR)
        if args.dry_run:
            state = store.load()
            host = linux.inspect(state.ports if state else (80, 443, 8443),
                                 managed=state is not None, read_only=True)
            linux.print_host(host)
            print("DRY-RUN：只读环境检查通过。")
        elif args.action in ("check", "show-subscription"):
            state = store.load()
            if not state:
                raise DeployError("没有由本工具管理的部署。")
            if args.action == "check":
                check(store, state)
            else:
                print(state.node.url)
        else:
            with store.lock():
                dispatch(store, args)
        return 0
    except (EOFError, ImportError, KeyError, TypeError, *OP_ERRORS) as exc:
        problem = str(exc) if isinstance(exc, DeployError) else f"操作中断或失败（{type(exc).__name__}）。"
        print(f"错误：{problem}", file=sys.stderr)
        return 1
