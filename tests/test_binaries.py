"""Optional integration checks with installed upstream binaries and loopback only."""
import contextlib
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from dfs import linux
from dfs.model import DeployError, Node, render


def binary(variable):
    value = os.environ.get(variable)
    return Path(value).resolve() if value else None


XRAY, CADDY, MIHOMO = (binary(name) for name in ("TEST_XRAY", "TEST_CADDY", "TEST_MIHOMO"))
DOMAIN = "dfs-test.duckdns.org"  # No DNS query comes out of this fixture.


def run(args, **kwargs):
    process = subprocess.run(args, timeout=40, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    if process.returncode:
        raise AssertionError(f"{Path(args[0]).name} exited {process.returncode}\n"
                             f"{process.stdout.decode()}\n{process.stderr.decode()}")
    return process.stdout


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.contextmanager
def server(args, address, directory, *, env=None):
    with tempfile.TemporaryFile() as log:
        with subprocess.Popen(args, cwd=directory, stdin=subprocess.DEVNULL,
                              stdout=log, stderr=log, env=env) as child:
            try:
                for _ in range(50):
                    if child.poll() is not None:
                        break
                    try:
                        with socket.create_connection(("127.0.0.1", address), timeout=0.1):
                            break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise AssertionError("Test server did not start")
                if child.poll() is not None:
                    log.seek(0)
                    raise AssertionError(log.read().decode())
                yield
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        child.kill()


def certificate(path, hostname):
    crt, key = path / "fixture.crt", path / "fixture.key"
    run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes", "-days", "1",
         "-subj", "/CN=" + hostname, "-addext", "subjectAltName=DNS:" + hostname,
         "-keyout", str(key), "-out", str(crt)])
    return crt, key


class NoContent(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_args):
        pass


class BinaryTests(unittest.TestCase):
    @unittest.skipUnless(CADDY, "set TEST_CADDY to the Caddy binary")
    def test_caddy_config_uses_http01_and_only_serves_exact_path_over_verified_tls(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            plain, secure = port(), port()
            node = replace(Node.generate(DOMAIN), https_port=secure)
            crt, key = certificate(parent, DOMAIN)
            published = parent / "subscription.yaml"
            published.write_bytes(render(node, "mihomo-v1.yaml.tpl"))
            config = parent / "Caddyfile"
            env = os.environ | {"XDG_DATA_HOME": directory, "XDG_CONFIG_HOME": directory}
            production = render(node, "Caddyfile.tpl", www_root=parent)
            config.write_bytes(production)
            command = [str(CADDY), "validate", "--adapter", "caddyfile", "--config", str(config)]
            run(command, env=env)
            adapted = json.loads(run([str(CADDY), "adapt", "--config", str(config), "--adapter", "caddyfile"], env=env))
            issuers = adapted["apps"]["tls"]["automation"]["policies"][0]["issuers"]
            self.assertTrue(issuers[0]["challenges"]["tls-alpn"]["disabled"])
            self.assertNotIn("http", issuers[0]["challenges"])
            acme = b"tls {\n\t\tissuer acme {\n\t\t\tdisable_tlsalpn_challenge\n\t\t}\n\t}"
            self.assertIn(acme, production)
            fixture = production.replace(acme, f"tls {crt} {key}".encode())
            fixture = fixture.replace(b"http://:80 {", f"http://:{plain} {{\n\tbind 127.0.0.1".encode())
            site = f"https://{DOMAIN}:{secure} {{".encode()
            config.write_bytes(fixture.replace(site, site + b"\n\tbind 127.0.0.1"))
            run(command, env=env)

            def request(protocol, listen, route):
                return run(["curl", "-q", "--noproxy", "*", "--cacert", str(crt), "--resolve",
                            f"{DOMAIN}:{listen}:127.0.0.1", "--max-time", "5", "-sS", "-w", ":%{http_code}",
                            f"{protocol}://{DOMAIN}:{listen}{route}"])

            with server([str(CADDY), "run", "--adapter", "caddyfile", "--config", str(config)],
                        secure, parent, env=env):
                self.assertEqual(request("https", secure, node.route), published.read_bytes() + b":200")
                self.assertEqual(request("https", secure, "/subscription.yaml"), b":404")
                self.assertEqual(request("https", secure, node.route + "/wrong"), b":404")
                self.assertEqual(request("http", plain, node.route), b":404")

    @unittest.skipUnless(XRAY, "set TEST_XRAY to the Xray binary")
    def test_xray_real_reality_vless_vision_handshake_and_reject_bad_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            reality, target = port(), port()
            node = replace(Node.generate(DOMAIN), reality_port=reality)
            crt, key = certificate(parent, node.sni)
            config = parent / "server.json"
            settings = json.loads(render(node, "xray.json.tpl"))
            settings["log"].pop("error")
            settings["inbounds"][0]["listen"] = "127.0.0.1"
            settings["inbounds"][0]["streamSettings"]["realitySettings"]["target"] = f"127.0.0.1:{target}"
            config.write_text(json.dumps(settings))
            run([str(XRAY), "run", "-test", "-config", str(config)])
            # Keep this offline. The first run checks the rendered private-network
            # restriction. The second permits the loopback fixture as test traffic.
            tls = ["openssl", "s_server", "-accept", f"127.0.0.1:{target}", "-cert", str(crt),
                   "-key", str(key), "-alpn", "h2", "-tls1_3", "-quiet"]
            xray = [str(XRAY), "run", "-config", str(config)]
            with ThreadingHTTPServer(("127.0.0.1", 0), NoContent) as target_http:
                thread = threading.Thread(target=target_http.serve_forever, daemon=True)
                thread.start()
                try:
                    url = f"http://127.0.0.1:{target_http.server_port}/generate_204"
                    with server(tls, target, parent):
                        with server(xray, reality, parent):
                            with self.assertRaises(DeployError):
                                linux.reality_probe(node, binary=XRAY, endpoint=url)
                        settings.pop("routing")
                        config.write_text(json.dumps(settings))
                        with server(xray, reality, parent):
                            linux.reality_probe(node, binary=XRAY, endpoint=url)
                            wrong = replace(node, uuid=Node.generate(DOMAIN).uuid)
                            with self.assertRaises(DeployError):
                                linux.reality_probe(wrong, binary=XRAY, endpoint=url)
                finally:
                    target_http.shutdown()
                    thread.join(timeout=2)

    @unittest.skipUnless(MIHOMO and os.environ.get("TEST_COUNTRY_MMDB"),
                         "set TEST_MIHOMO and TEST_COUNTRY_MMDB")
    def test_mihomo_parses_complete_generated_yaml_with_actual_core(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            shutil.copyfile(os.environ["TEST_COUNTRY_MMDB"], parent / "Country.mmdb")
            path = parent / "config.yaml"
            path.write_bytes(render(Node.generate(DOMAIN), "mihomo-v1.yaml.tpl"))
            run([str(MIHOMO), "-t", "-d", directory, "-f", str(path)])
