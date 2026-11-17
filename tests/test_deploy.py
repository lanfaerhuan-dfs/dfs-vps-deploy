import contextlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import call, patch

from dfs import cli, dns, linux
from dfs.model import (DEFAULT_FILENAME, MAGIC, DeployError, Node, domain_name, make_backup,
                       parse_legacy, render, restore_backup, validate_mihomo)


DOMAIN = "dfs-test.duckdns.org"  # Synthetic identity. Tests never query it.
ADDRESS = "192.0.2.40"
HOST = linux.Host("Debian test", "aarch64", (22022,), ADDRESS, "未知")


class FakeDNS(dns.DNS):
    def __init__(self, records=dns.EMPTY):
        self.domain = DOMAIN
        self.records = records
        self.requests = []
        self.fail_at = None

    def authoritative(self):
        return self.records

    def public(self):
        return {"mock": self.records}

    def update(self, token, **params):
        self.requests.append(params)
        if len(self.requests) == self.fail_at:
            raise DeployError("Mock DuckDNS KO")
        if params.get("clear"):
            self.records = dns.EMPTY
        elif ":" in params["ip"]:
            self.records = dns.Records(self.records.a, params["ip"])
        else:
            self.records = dns.Records(params["ip"], params.get("ipv6", self.records.aaaa))


class DirectoryTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(directory)
        self.node = Node.generate(DOMAIN)
        self.store = cli.Store(self.path)


class IdentityTests(DirectoryTest):
    def test_new_distinct_secure_subscription_and_full_rules(self):
        other = Node.generate(DOMAIN)
        for field in ("uuid", "private_key", "public_key", "short_id", "route"):
            self.assertNotEqual(getattr(other, field), getattr(self.node, field))
        self.assertRegex(self.node.url, r":8443/[0-9a-f]{64}/" + DEFAULT_FILENAME)
        yaml = render(self.node, "mihomo-v1.yaml.tpl")
        validate_mihomo(yaml, self.node)
        self.assertIn(b"port: 443", yaml)
        self.assertIn(DOMAIN.encode(), yaml)
        self.assertNotIn(self.node.private_key.encode(), yaml)
        self.assertIn(b"disable_tlsalpn_challenge", linux.caddy_bytes(self.node, True))

    def test_encrypt_restore_exact_identity_and_reject_wrong_password_or_corruption(self):
        password = "Synthetic test passphrase for round-trip"
        backup = self.path / "test.dfsbackup"
        make_backup(backup, self.node, password)
        data1 = backup.read_bytes()
        self.assertTrue(data1.startswith(MAGIC))
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        for secret in (self.node.uuid, self.node.private_key, self.node.route, password):
            self.assertNotIn(secret.encode(), data1)
        recovered = restore_backup(backup, password)
        self.assertEqual(recovered, self.node)
        self.assertEqual(render(recovered, "xray.json.tpl"), render(self.node, "xray.json.tpl"))
        self.assertEqual(render(recovered, "mihomo-v1.yaml.tpl"), render(self.node, "mihomo-v1.yaml.tpl"))
        make_backup(backup, self.node, password)
        self.assertNotEqual(backup.read_bytes(), data1)
        with self.assertRaisesRegex(DeployError, "无法解密"):
            restore_backup(backup, "wrong password")
        backup.write_bytes(data1[:-1] + bytes([data1[-1] ^ 1]))
        with self.assertRaisesRegex(DeployError, "无法解密"):
            restore_backup(backup, password)

    def test_legacy_route_and_filename_preserved(self):
        old = f"https://{DOMAIN}:8443/secret/base/custom.yml"
        migrated = Node.generate(DOMAIN, legacy=parse_legacy(old, DOMAIN))
        self.assertEqual(migrated.url, old)
        self.assertNotEqual(migrated.uuid, self.node.uuid)
        self.assertEqual(parse_legacy("folder/keep-me/", DOMAIN), (8443, f"/folder/keep-me/{DEFAULT_FILENAME}"))
        self.assertEqual(parse_legacy(f"https://{DOMAIN}:9443/old", DOMAIN), (9443, "/old"))

    def test_reject_missing_legacy_route_and_unsafe_inputs(self):
        for value in ("", f"https://{DOMAIN}:8443/", f"https://{DOMAIN}/old.yml",
                      f"https://{DOMAIN}:8443/x.yml?k=secret", "other.duckdns.org:8443/x.yaml",
                      f"https://example.invalid:8443/x.yml", "/../route", "/%2e%2e/route", "/foo/*"):
            with self.subTest(value=value), self.assertRaises(DeployError):
                parse_legacy(value, DOMAIN)
        with self.assertRaises(DeployError):
            domain_name("demo,production")

    def test_invalid_yaml_and_unknown_or_cyclic_rule_groups(self):
        good = render(self.node, "mihomo-v1.yaml.tpl")
        broken = (
            b"proxies: [",
            good.replace(b"RULE-SET,YouTube,", b"RULE-SET,Wrong,"),
            good.replace(b"- DFS-VLESS-REALITY-VISION", b"- Missing"),
            good.replace(b"  enable: true", b"  enable: true\n  enable: false"),
            good.replace("      - 节点选择".encode(), "      - 漏网之鱼".encode()),
            good.replace(b"RULE-SET,YouTube", b"RULE-SET,Google").replace(
                "RULE-SET,Google,谷歌服务".encode(), "RULE-SET,YouTube,谷歌服务".encode()),
        )
        for data in broken:
            with self.subTest(), self.assertRaisesRegex(DeployError, "YAML"):
                validate_mihomo(data, self.node)


class DNSTests(DirectoryTest):
    def test_clear_both_then_update_a_and_recover_old_a_aaaa(self):
        before = dns.Records("192.0.2.5", "2001:db8::5")
        after = dns.Records(ADDRESS)
        resolver = FakeDNS(before)
        tx = dns.DNSTransaction(self.store.journal, resolver)
        tx.begin(before, after, "fake-token")
        self.assertEqual(resolver.requests, [{"clear": "true"}, {"ip": ADDRESS}])
        self.assertEqual(resolver.authoritative(), after)
        tx.commit()
        tx.rollback("fake-token")
        self.assertEqual(resolver.records, before)
        self.assertEqual(resolver.requests[-1], {"ip": before.a, "ipv6": before.aaaa})
        self.assertEqual(tx.read()["status"], "rolled_back")
        self.assertNotIn(b"fake-token", self.store.journal.read_bytes())

    def test_rollback_after_partial_clear_or_api_ko(self):
        before = dns.Records("192.0.2.5", "2001:db8::5")
        for failure in (1, 2):
            resolver = FakeDNS(before)
            resolver.fail_at = failure
            self.store.journal.unlink(missing_ok=True)
            tx = dns.DNSTransaction(self.store.journal, resolver)
            with self.assertRaisesRegex(DeployError, "KO"):
                tx.begin(before, dns.Records(ADDRESS), "fake-token")
            tx.rollback("fake-token")
            self.assertEqual(resolver.records, before)
            self.assertEqual(tx.read()["status"], "rolled_back")

    def test_rollback_restores_aaaa_only(self):
        before = dns.Records(aaaa="2001:db8::a")
        resolver = FakeDNS(before)
        tx = dns.DNSTransaction(self.store.journal, resolver)
        tx.begin(before, dns.Records(ADDRESS), "fake-token")
        tx.rollback("fake-token")
        self.assertEqual(resolver.records, before)

    def test_concurrent_dns_change_refuses_overwrite(self):
        resolver = FakeDNS()
        tx = dns.DNSTransaction(self.store.journal, resolver)
        with self.assertRaisesRegex(DeployError, "已变化"):
            tx.begin(dns.Records("192.0.2.1"), dns.Records(ADDRESS), "fake-token")
        self.assertFalse(self.store.journal.exists())
        tx.begin(dns.EMPTY, dns.Records(ADDRESS), "fake-token")
        resolver.records = dns.Records("192.0.2.2")
        with self.assertRaisesRegex(DeployError, "其他操作"):
            tx.rollback("fake-token")
        self.assertEqual(resolver.records.a, "192.0.2.2")

    def test_dns_api_filters_response_does_not_leak_token_or_redirect(self):
        resolver = object.__new__(dns.DNS)
        resolver.domain = DOMAIN
        with patch("dfs.dns.http.client.HTTPSConnection") as https:
            connection = https.return_value
            response = connection.getresponse.return_value
            response.status, response.read.return_value = 200, b"KO\n"
            with self.assertRaises(DeployError) as error:
                resolver.update("test-token-000000", ip=ADDRESS)
            self.assertNotIn("test-token", str(error.exception))
            request = connection.request.call_args
            self.assertEqual(request.args[0], "GET")
            self.assertIn("domains=dfs-test&", request.args[1])
            self.assertNotIn(",", request.args[1])
            response.status = 302
            with self.assertRaises(DeployError):
                resolver.update("test-token-000000", ip=ADDRESS)
            self.assertEqual(https.call_count, 2)

    def test_authority_and_public_must_both_remove_aaaa(self):
        resolver = FakeDNS(dns.Records(ADDRESS, "2001:db8::5"))
        with self.assertRaisesRegex(DeployError, "未收敛"):
            resolver.wait(dns.Records(ADDRESS), public=True, seconds=0)
        resolver.records = dns.Records(ADDRESS)
        with patch.object(resolver, "public", return_value={"mock": dns.Records(ADDRESS, "2001:db8::5")}):
            with self.assertRaisesRegex(DeployError, "未收敛"):
                resolver.wait(dns.Records(ADDRESS), public=True, seconds=0)
        resolver.wait(dns.Records(ADDRESS), public=True, seconds=0)


class HostTests(unittest.TestCase):
    def mocked_host(self, release, rows):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(linux.os, "geteuid", return_value=0))
        stack.enter_context(patch.object(linux.Path, "is_dir", return_value=True))
        stack.enter_context(patch.object(linux.os.path, "lexists", return_value=False))
        stack.enter_context(patch.object(linux.shutil, "which", return_value=None))
        stack.enter_context(patch.object(linux.platform, "freedesktop_os_release", return_value={
            "ID": "debian", "VERSION_ID": release, "PRETTY_NAME": "Debian " + release,
        }))
        stack.enter_context(patch.object(linux.platform, "machine", return_value="aarch64"))
        stack.enter_context(patch.object(linux, "listeners", return_value=rows))
        stack.enter_context(patch.object(linux, "systemd_field", return_value="not-found"))
        stack.enter_context(patch.object(linux, "run", return_value=subprocess.CompletedProcess([], 0, b"default via X\n", b"")))
        stack.enter_context(patch.object(linux, "public_ipv4", return_value=ADDRESS))
        stack.enter_context(patch.object(linux, "read_dmi", return_value="unverified"))
        return stack

    def test_debian_12_and_13_new_environment(self):
        for release in ("12", "13"):
            with self.subTest(release=release), self.mocked_host(release, [(22022, '"sshd"')]):
                host = linux.inspect((80, 443, 8443))
                self.assertEqual(host.release, "Debian " + release)
                self.assertEqual(host.ssh_ports, (22022,))
                self.assertEqual(host.provider, "未知")

    def test_conflict_443_stops_before_configuration(self):
        with self.mocked_host("13", [(22022, '"sshd"'), (443, '"foreign"')]), self.assertRaisesRegex(DeployError, "TCP 443"):
            linux.inspect((80, 443, 8443))

    def test_firewall_orders_actual_ssh_before_enable(self):
        with patch.object(linux, "run") as command, patch.object(linux, "check_firewall"):
            linux.configure_firewall((22022,), (80, 443, 8443))
            self.assertEqual(command.call_args_list[0].args[0], ["ufw", "prepend", "allow", "22022/tcp"])
            self.assertEqual(command.call_args_list[-1].args[0], ["ufw", "--force", "enable"])

    def test_ssh_socket_activation(self):
        with patch.object(linux, "systemd_field", side_effect=["loaded", "active", "[::]:2244 (Stream)"]):
            self.assertEqual(linux.ssh_ports([(2244, 'systemd')]), (2244,))

    def test_ufw_earlier_conflict_is_a_failure(self):
        rules = b"Status: active\n\n443/tcp DENY IN Anywhere\n443/tcp ALLOW IN Anywhere\n"
        with patch.object(linux, "run", return_value=subprocess.CompletedProcess([], 0, rules, b"")):
            with self.assertRaisesRegex(DeployError, "较早的拒绝"):
                linux.check_firewall((), (443,))

    def test_invalid_xray_uid_or_config_cannot_pass(self):
        with patch.object(linux.pwd, "getpwnam") as user, patch.object(linux.grp, "getgrnam") as group:
            user.return_value.pw_uid = 0
            group.return_value.gr_gid = 100
            with self.assertRaisesRegex(DeployError, "权限异常"):
                linux.identity("xray")
        error = DeployError("以 xray 用户验证 Xray 配置失败")
        with patch.object(linux, "run", side_effect=error) as command:
            with self.assertRaisesRegex(DeployError, "xray"):
                linux.service_validation(Path("/xray"), Path("/caddy"))
            self.assertEqual(command.call_args.args[0][:4], ["runuser", "-u", "xray", "--"])

    def test_caddy_certificate_failure_does_not_skip_tls(self):
        node = Node.generate(DOMAIN)
        reply = subprocess.CompletedProcess([], 60, b"000", b"certificate verify failed")
        with patch.object(linux, "run", return_value=reply) as command:
            with self.assertRaisesRegex(DeployError, "可信 TLS"):
                linux.https_once(node)
            args = command.call_args.args[0]
            self.assertNotIn("-k", args)
            self.assertNotIn("--insecure", args)
            self.assertFalse(any(node.route in arg for arg in args))
            self.assertIn(node.route.encode(), command.call_args.kwargs["data"])


class WorkflowTests(DirectoryTest):
    def setUp(self):
        super().setUp()
        self.state = cli.Deployment(self.node, "MIGRATE", "staged")
        self.store.save(self.state)
        self.store.backup.write_bytes(MAGIC + b"synthetic sentinel")
        self.store.backup.chmod(0o600)

    def test_fresh_provision_reaches_staged_in_order(self):
        expected = ["install_packages", "install_tool", "configure_firewall", "install_xray",
                    "test_target", "write_configs", "restart_services", "service_health"]
        order = []
        with contextlib.ExitStack() as stack:
            for name in expected:
                stack.enter_context(patch.object(linux, name, side_effect=lambda *a, name=name, **k: order.append(name)))
            started = cli.provision(self.store, cli.replace(self.state, phase="provisioning"), HOST)
        self.assertEqual(order, expected)
        self.assertEqual(started.phase, "staged")
        self.assertEqual(self.store.load().node, self.node)

    def test_no_dns_does_not_create_dns_journal(self):
        with patch.object(self.store, "transaction") as transaction:
            cli.finish(self.store, self.state, HOST, no_dns=True)
        transaction.assert_not_called()
        self.assertFalse(self.store.journal.exists())

    def test_repeated_install_enters_maintenance_with_same_identity(self):
        with patch.object(cli, "maintain", return_value="show-subscription") as menu, patch.object(cli, "prepare") as fresh:
            with contextlib.redirect_stdout(io.StringIO()) as console:
                cli.dispatch(self.store, cli.arguments([]))
        menu.assert_called_once_with()
        self.assertEqual(self.store.load().node, self.node)
        self.assertIn(self.node.url, console.getvalue())
        fresh.assert_not_called()
        with self.assertRaisesRegex(DeployError, "已有部署"):
            cli.dispatch(self.store, cli.arguments(["new"]))

    def test_health_failure_rolls_back_and_prints_no_success(self):
        before = dns.Records("192.0.2.5", "2001:db8::5")
        resolver = FakeDNS(before)
        tx = dns.DNSTransaction(self.store.journal, resolver)
        with (
            patch.object(self.store, "transaction", return_value=tx),
            patch.object(linux, "service_health"),
            patch.object(linux, "check_firewall"),
            patch.object(linux, "set_caddy") as caddy,
            patch.object(linux, "wait_https", side_effect=DeployError("ACME 验证失败")),
            patch.object(cli, "hidden", return_value="synthetic-token"),
            patch.object(cli, "confirm"),
            contextlib.redirect_stdout(io.StringIO()) as console,
        ):
            with self.assertRaisesRegex(DeployError, "ACME"):
                cli.finish(self.store, self.state, HOST, False)
        self.assertEqual(caddy.call_args_list, [call(self.node, live=True), call(self.node, live=False)])
        self.assertNotIn("DEPLOY SUCCESS", console.getvalue())
        self.assertEqual(resolver.records, before)
        self.assertEqual(tx.read()["status"], "rolled_back")
        self.assertEqual(self.store.load().phase, "staged")

    def test_completed_dns_and_https_commit_ready(self):
        resolver = FakeDNS()
        tx = dns.DNSTransaction(self.store.journal, resolver)
        with (
            patch.object(self.store, "transaction", return_value=tx),
            patch.object(linux, "service_health"),
            patch.object(linux, "check_firewall"),
            patch.object(linux, "set_caddy"),
            patch.object(linux, "wait_https"),
            patch.object(cli, "hidden", return_value="synthetic-token"),
            patch.object(cli, "confirm"),
            contextlib.redirect_stdout(io.StringIO()) as console,
        ):
            cli.finish(self.store, self.state, HOST, False)
        self.assertIn("DEPLOY SUCCESS", console.getvalue())
        self.assertEqual(tx.read()["status"], "applied")
        self.assertEqual(self.store.load().phase, "ready")

    def test_dry_run_does_not_write_state_or_log(self):
        absent = self.path / "absent"
        with (
            patch.object(cli, "STATE_DIR", absent),
            patch.object(cli.os, "geteuid", return_value=0),
            patch.object(linux, "inspect", return_value=HOST) as inspect,
        ):
            umask = os.umask(0o077)
            try:
                self.assertEqual(cli.main(["--dry-run"]), 0)
            finally:
                os.umask(umask)
        self.assertTrue(inspect.call_args.kwargs["read_only"])
        self.assertFalse(absent.exists())

    def test_lock_prevents_overlapping_mutations(self):
        with self.store.lock():
            with self.assertRaisesRegex(DeployError, "正在运行"), self.store.lock():
                self.fail("lock should not allow a second writer")


class MigrationEntryTests(DirectoryTest):
    def run_prepare(self, action, inputs, secrets):
        with (
            patch("builtins.input", side_effect=inputs),
            patch.object(cli, "hidden", side_effect=secrets),
            patch.object(cli, "confirm"),
            patch.object(cli, "prerequisites", return_value=False),
            patch.object(linux, "inspect", return_value=HOST),
            patch.object(cli, "provision", side_effect=lambda _store, deployment, *_args, **_kw: deployment),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return cli.prepare(self.store, action, cli.arguments([action]))

    def test_new_entry_creates_backup_before_install(self):
        password = "Synthetic password with spaces"
        state = self.run_prepare("new", [DOMAIN], [password, password])
        self.assertEqual(state.mode, "NEW")
        self.assertEqual(state.phase, "provisioning")
        self.assertEqual(restore_backup(self.store.backup, password), state.node)
        self.assertEqual(self.store.load(), state)

    def test_restore_entry_recreates_key_pair_and_preserves_url(self):
        original = self.path / "previous.dfsbackup"
        old, new = "original synthetic password", "new synthetic password"
        make_backup(original, self.node, old)
        state = self.run_prepare("migrate", [DOMAIN, "1", str(original)], [old, new, new])
        self.assertEqual(state.mode, "MIGRATE")
        self.assertEqual(state.node, self.node)
        self.assertEqual(restore_backup(self.store.backup, new), self.node)

    def test_migrate_without_backup_preserves_old_url_but_rotates_keys(self):
        password = "new synthetic password"
        state = self.run_prepare("migrate", [DOMAIN, "2"], [self.node.url, password, password])
        self.assertEqual(state.node.url, self.node.url)
        self.assertNotEqual(state.node.private_key, self.node.private_key)
        self.assertNotEqual(state.node.uuid, self.node.uuid)

    def test_migrate_missing_url_has_no_side_effects(self):
        with self.assertRaisesRegex(DeployError, "必须提供"):
            self.run_prepare("migrate", [DOMAIN, "2"], [""])
        self.assertFalse(self.store.file.exists())
        self.assertFalse(self.store.backup.exists())
