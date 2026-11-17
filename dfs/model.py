"""Portable node identity, credential storage, and configuration rendering."""
import base64
import json
import os
import re
import secrets
import tempfile
import uuid as uuid_module
from dataclasses import asdict, dataclass
from pathlib import Path
from string import Template
from urllib.parse import urlsplit


VERSION = "0.1.0"
DEFAULT_XRAY = "26.2.6"
DEFAULT_FILENAME = "dfs-vless-reality-clash.yaml"
NODE_NAME = "DFS-VLESS-REALITY-VISION"
CONFIG = Path(__file__).resolve().parents[1] / "config"
MAGIC = b"DFS-BACKUP\x01\x00"
LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
SEGMENT = r"[A-Za-z0-9._~-]{1,160}"
BUILTIN_GROUPS = {"DIRECT", "REJECT"}


class DeployError(Exception):
    pass


def domain_name(value):
    value = value.strip().lower().removesuffix(".duckdns.org")
    if not re.fullmatch(LABEL, value):
        raise DeployError("请输入单个 DuckDNS 子域名。")
    return value + ".duckdns.org"


def xray_version(value):
    if not re.fullmatch(r"[0-9]{1,4}\.[0-9]{1,3}\.[0-9]{1,3}", value):
        raise DeployError("Xray 版本必须是三段式发行版版本号。")
    return value


def route_segments(value):
    if not value.startswith("/") or len(value) > 768:
        raise DeployError("订阅路径必须以 / 开头，且长度不得超过 768 字符。")
    segments = value[1:].split("/")
    if any(not re.fullmatch(SEGMENT, part) or part in (".", "..") for part in segments):
        raise DeployError("订阅路径仅支持安全 ASCII 路径段。")
    return segments


def parse_legacy(value, domain):
    value = value.strip()
    if not value:
        raise DeployError("必须提供旧订阅 URL 或访问路径才能保留原 URL。")
    port = 8443
    full_url = value.lower().startswith("https://")
    if full_url:
        try:
            url = urlsplit(value)
            port = url.port or 443
            host_ok = url.netloc.lower() == f"{domain}:{port}" if url.port else url.netloc.lower() == domain
        except ValueError:
            raise DeployError("旧订阅 URL 格式无效。") from None
        if not host_ok or "?" in value or "#" in value:
            raise DeployError("旧 URL 必须使用指定 DuckDNS 域名，且只含路径。")
        value = url.path
    elif not value.startswith("/"):
        value = "/" + value

    if not 1 <= port <= 65535 or port in (80, 443):
        raise DeployError("旧 HTTPS 端口与当前 TCP 80/443 布局冲突，无法保留该 URL。")
    if not full_url:
        value = value.removesuffix("/")
    parts = route_segments(value)
    if not full_url and not parts[-1].lower().endswith((".yaml", ".yml")):
        value += "/" + DEFAULT_FILENAME
    route_segments(value)
    return port, value


def key_string(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def key_bytes(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
        raise ValueError
    raw = base64.urlsafe_b64decode(value + "=")
    if key_string(raw) != value:
        raise ValueError
    return raw


@dataclass(frozen=True)
class Node:
    domain: str
    uuid: str
    public_key: str
    private_key: str
    short_id: str
    route: str
    xray_version: str = DEFAULT_XRAY
    reality_port: int = 443
    https_port: int = 8443
    sni: str = "www.amazon.com"
    template: int = 1

    @property
    def url(self):
        return f"https://{self.domain}:{self.https_port}{self.route}"

    @classmethod
    def generate(cls, domain, version=DEFAULT_XRAY, legacy=None):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

        port, route = legacy or (8443, f"/{secrets.token_hex(32)}/{DEFAULT_FILENAME}")
        key = X25519PrivateKey.generate()
        return cls(
            domain, str(uuid_module.uuid4()),
            key_string(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)),
            key_string(key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                         serialization.NoEncryption())),
            secrets.token_hex(8), route, xray_version(version), https_port=port,
        )

    @classmethod
    def checked(cls, fields):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

        try:
            if set(fields) != set(cls.__dataclass_fields__):
                raise ValueError
            node = cls(**fields)
            if domain_name(node.domain) != node.domain or str(uuid_module.UUID(node.uuid)) != node.uuid:
                raise ValueError
            key = X25519PrivateKey.from_private_bytes(key_bytes(node.private_key))
            if key_string(key.public_key().public_bytes(serialization.Encoding.Raw,
                                                       serialization.PublicFormat.Raw)) != node.public_key:
                raise ValueError
            if not re.fullmatch(r"[0-9a-f]{16}", node.short_id):
                raise ValueError
            xray_version(node.xray_version)
            ports = (80, node.reality_port, node.https_port)
            if len(set(ports)) != 3 or any(type(p) is not int or not 1 <= p <= 65535 for p in ports):
                raise ValueError
            if not re.fullmatch(rf"{LABEL}(?:\.{LABEL})+", node.sni) or len(node.sni) > 253:
                raise ValueError
            route_segments(node.route)
            if type(node.template) is not int or node.template != 1:
                raise ValueError
        except (AttributeError, TypeError, ValueError, DeployError):
            raise DeployError("节点状态字段、模板版本或 REALITY 密钥对无效。") from None
        return node


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_file(path, data, mode=0o600, group=None, owner=None):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".dfs-", delete=False) as temp:
        tmp = Path(temp.name)
        try:
            os.fchmod(temp.fileno(), mode)
            if group is not None or owner is not None:
                os.fchown(temp.fileno(), -1 if owner is None else owner, -1 if group is None else group)
            temp.write(data)
            temp.flush()
            os.fsync(temp.fileno())
            os.replace(tmp, path)
            sync_directory(path.parent)
        finally:
            tmp.unlink(missing_ok=True)


def json_bytes(data):
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()


def read_small(path, limit=1024 * 1024):
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise DeployError("输入文件超过大小限制。")
    return data


def aead(password, salt):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    kdf = Scrypt(salt=salt, length=32, n=2**16, r=8, p=1)
    return AESGCM(kdf.derive(password.encode("utf-8")))


def make_backup(path, node, password):
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    data = aead(password, salt).encrypt(nonce, json_bytes(asdict(node)), MAGIC)
    atomic_file(path, MAGIC + salt + nonce + data)


def restore_backup(path, password):
    from cryptography.exceptions import InvalidTag

    data = read_small(path, 64 * 1024)
    if not data.startswith(MAGIC) or len(data) < len(MAGIC) + 44:
        raise DeployError("无法识别此 DFS 备份格式。")
    body = data[len(MAGIC):]
    try:
        fields = json.loads(aead(password, body[:16]).decrypt(body[16:28], body[28:], MAGIC))
    except (InvalidTag, ValueError):
        raise DeployError("无法解密备份。请检查密码和文件完整性。") from None
    return Node.checked(fields)


def render(node, name, **extra):
    template = Template((CONFIG / name).read_text(encoding="utf-8"))
    return template.substitute(asdict(node), **extra).encode()


def validate_mihomo(data, node):
    import yaml

    class StrictLoader(yaml.SafeLoader):
        def construct_mapping(self, mapping, deep=False):
            pairs = self.construct_pairs(mapping, deep=deep)
            output = dict(pairs)
            if len(pairs) != len(output):
                raise ValueError
            return output

    try:
        config = yaml.load(data, Loader=StrictLoader)
        proxies = config["proxies"]
        expected = {
            "name": NODE_NAME, "type": "vless", "server": node.domain, "port": node.reality_port,
            "uuid": node.uuid, "network": "tcp", "tls": True, "udp": True, "flow": "xtls-rprx-vision",
            "servername": node.sni, "client-fingerprint": "chrome",
            "reality-opts": {"public-key": node.public_key, "short-id": node.short_id},
        }
        if proxies != [expected] or node.private_key.encode() in data or config["dns"]["enhanced-mode"] != "fake-ip":
            raise ValueError
        providers = config["rule-providers"]
        provider_paths = set()
        for provider in providers.values():
            if (
                (provider["type"], provider["behavior"], provider["format"], provider["interval"])
                != ("http", "classical", "yaml", 86400)
                or not re.fullmatch(r"\./rule_provider/(?:Ruleset/)?[A-Za-z0-9_-]+\.yaml", provider["path"])
                or not re.fullmatch(
                    r"https://raw\.githubusercontent\.com/ACL4SSR/ACL4SSR/master/Clash/Providers/"
                    r"(?:Ruleset/)?[A-Za-z0-9_-]+\.yaml", provider["url"]
                )
                or provider["path"] in provider_paths
            ):
                raise ValueError
            provider_paths.add(provider["path"])
        groups = {group["name"]: group["proxies"] for group in config["proxy-groups"]}
        if len(groups) != len(config["proxy-groups"]) or groups.keys() & (BUILTIN_GROUPS | {NODE_NAME}):
            raise ValueError

        def visit(group, visiting):
            if group in visiting:
                raise ValueError
            for target in groups[group]:
                if target in groups:
                    visit(target, visiting | {group})
                elif target not in BUILTIN_GROUPS | {NODE_NAME}:
                    raise ValueError

        for group in groups:
            visit(group, set())

        rules = config["rules"]
        ordered_providers = []
        for rule in rules:
            fields = rule.split(",")
            kind, target = fields[0], fields[-1]
            if target not in groups.keys() | BUILTIN_GROUPS:
                raise ValueError
            if kind == "RULE-SET" and len(fields) == 3 and fields[1] in providers:
                ordered_providers.append(fields[1])
            elif rule not in ("MATCH,漏网之鱼", "GEOIP,CN,DIRECT"):
                raise ValueError
        if (rules[-2:] != ["GEOIP,CN,DIRECT", "MATCH,漏网之鱼"] or
                len(ordered_providers) != len(providers) or set(ordered_providers) != set(providers)):
            raise ValueError
        for earlier, later in (
            ("YouTube", "Google"), ("ProxyMedia", "ChinaCompanyIp"), ("ProxyMedia", "ChinaIp"),
            ("ProxyMedia", "ChinaIpV6"), ("ProxyGFWlist", "ChinaCompanyIp"),
            ("ProxyGFWlist", "ChinaIp"), ("ProxyGFWlist", "ChinaIpV6"),
        ):
            if ordered_providers.index(earlier) >= ordered_providers.index(later):
                raise ValueError
    except (KeyError, TypeError, AttributeError, ValueError, yaml.YAMLError, RecursionError):
        raise DeployError("Mihomo YAML 或规则/策略组引用无效。") from None
