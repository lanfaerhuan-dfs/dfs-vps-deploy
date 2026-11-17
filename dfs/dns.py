"""DuckDNS updates with a durable, single-domain rollback record."""
import http.client
import ipaddress
import re
import time
from dataclasses import asdict, dataclass
from urllib.parse import urlencode

from .model import DeployError, domain_name, json_bytes, atomic_file
from .process import run


PUBLIC_RESOLVERS = ("1.1.1.1", "8.8.8.8")


@dataclass(frozen=True)
class Records:
    a: str = ""
    aaaa: str = ""

    def describe(self):
        return f"A={self.a or '空'}，AAAA={self.aaaa or '空'}"


EMPTY = Records()


def dig(server, name, rtype, authoritative=False):
    args = ["dig", "-r", f"@{server}", name, rtype, "+time=2", "+tries=1",
            "+noall", "+comments", "+answer"]
    if authoritative:
        args.append("+norecurse")
    for transport in ("", "+tcp"):
        reply = run(args + ([transport] if transport else []), "查询 DNS", timeout=6, require=False)
        text = reply.stdout.decode("ascii", errors="replace")
        if reply.returncode == 0 and re.search(r"status: (NOERROR|NXDOMAIN),", text):
            if authoritative and not re.search(r";; flags: [^;]*\baa\b", text):
                continue
            answers = set()
            for line in text.splitlines():
                fields = line.split()
                if len(fields) == 5 and fields[0].rstrip(".").lower() == name and fields[2] == "IN":
                    if fields[3] == "CNAME":
                        raise DeployError("DuckDNS 记录包含 CNAME，无法安全切换。")
                    if fields[3] == rtype:
                        answers.add(fields[4].rstrip("."))
            return answers
    raise DeployError(f"DNS 查询失败（{server} {rtype}）。")


def read_records(server, domain, authoritative=False):
    values = []
    for rtype, address in (("A", ipaddress.IPv4Address), ("AAAA", ipaddress.IPv6Address)):
        records = dig(server, domain, rtype, authoritative)
        if len(records) > 1:
            raise DeployError("发现多个同类型 DNS 记录，无法使用 DuckDNS 单地址接口安全恢复。")
        values.append(str(address(next(iter(records)))) if records else "")
    return Records(*values)


class DNS:
    def __init__(self, domain):
        if domain_name(domain) != domain:
            raise DeployError("DuckDNS 域名无效。")
        self.domain = domain
        self.nameservers = tuple(sorted(dig(PUBLIC_RESOLVERS[0], "duckdns.org", "NS")))
        if not self.nameservers or len(self.nameservers) > 12:
            raise DeployError("无法确定 DuckDNS 权威服务器。")

    def authoritative(self):
        # Require two agreeing authorities. Fail closed on a conflicting answer.
        results = []
        for server in self.nameservers:
            try:
                results.append(read_records(server, self.domain, True))
            except DeployError as exc:
                if not str(exc).startswith("DNS 查询失败"):
                    raise
        if len(results) < 2 or len(set(results)) != 1:
            raise DeployError("DuckDNS 权威服务器不足两台可达或记录仍未一致。")
        return results[0]

    def public(self):
        return {server: read_records(server, self.domain) for server in PUBLIC_RESOLVERS}

    def wait(self, expected, *, public, seconds):
        deadline = time.monotonic() + seconds
        while True:
            try:
                if self.authoritative() == expected and (
                    not public or all(record == expected for record in self.public().values())
                ):
                    return
            except DeployError:
                pass
            if time.monotonic() >= deadline:
                source = "权威 DNS 和公共解析器" if public else "权威 DNS"
                raise DeployError(f"{source}尚未收敛至预期 A/AAAA。")
            time.sleep(4)

    def update(self, token, **params):
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", token):
            raise DeployError("DuckDNS Token 格式无效。")
        query = urlencode({"domains": self.domain.split(".")[0], "token": token, **params})
        for attempt in range(3):
            connection = http.client.HTTPSConnection("www.duckdns.org", timeout=8)
            try:
                connection.request("GET", "/update?" + query, headers={"Connection": "close"})
                response = connection.getresponse()
                status, body = response.status, response.read(256).strip()
            except (OSError, http.client.HTTPException):
                status, body = 503, b""
            finally:
                connection.close()
            if status == 200 and body == b"OK":
                return
            if status == 200 or status < 500:
                raise DeployError("DuckDNS API 拒绝更新。请核对域名及 Token。")
            if attempt < 2:
                time.sleep(2**attempt)
        raise DeployError("DuckDNS API 网络请求失败或返回服务端错误。")

    def replace(self, previous, desired, token):
        if previous == desired:
            return
        if (previous.a and not desired.a) or (previous.aaaa and not desired.aaaa):
            self.update(token, clear="true")
        if desired.a:
            params = {"ip": desired.a}
            if desired.aaaa:
                params["ipv6"] = desired.aaaa
            self.update(token, **params)
        elif desired.aaaa:
            # DuckDNS supports putting an IPv6 address into 'ip'.
            self.update(token, ip=desired.aaaa)


class DNSTransaction:
    def __init__(self, path, resolver):
        self.path = path
        self.resolver = resolver

    def read(self):
        import json

        if not self.path.exists():
            return None
        journal = json.loads(self.path.read_bytes())
        if journal["domain"] != self.resolver.domain:
            raise DeployError("DNS 回滚记录域名与当前部署不符。")
        return journal

    def save(self, journal):
        atomic_file(self.path, json_bytes(journal))

    def begin(self, before, after, token):
        prior = self.read()
        if prior and prior["status"] != "rolled_back":
            raise DeployError("已有 DNS 切换记录，请先完成恢复。")
        if self.resolver.authoritative() != before:
            raise DeployError("确认后 DNS 已变化，取消写入。")
        journal = {"domain": self.resolver.domain, "status": "switching",
                   "before": asdict(before), "after": asdict(after),
                   "may_clear": bool(before.aaaa and not after.aaaa)}
        self.save(journal)
        self.resolver.replace(before, after, token)

    def commit(self):
        journal = self.read()
        journal["status"] = "applied"
        self.save(journal)

    def rollback(self, token):
        journal = self.read()
        if not journal or journal["status"] == "rolled_back":
            return
        before, after = Records(**journal["before"]), Records(**journal["after"])
        current = self.resolver.authoritative()
        allowed = {before, after}
        if journal["may_clear"] or journal["status"] == "rolling_back":
            allowed.add(EMPTY)
        if current not in allowed:
            raise DeployError("权威 DNS 已被其他操作改动，保留回滚记录供人工恢复。")
        journal["status"] = "rolling_back"
        self.save(journal)
        self.resolver.replace(current, before, token)
        self.resolver.wait(before, public=False, seconds=90)
        journal["status"] = "rolled_back"
        self.save(journal)
