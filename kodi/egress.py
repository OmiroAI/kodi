"""CONNECT-only Unix-socket proxy for network-disabled coding containers.

Workers have loopback only. socat connects their loopback HTTP proxy to this
socket. Resolve once and reject private destinations to prevent DNS rebinding.
"""
import ipaddress
import os
import select
import socket
import socketserver
from pathlib import Path

DOMAINS = ('chatgpt.com', 'openai.com', 'auth0.com', 'generativelanguage.googleapis.com',
           'cloudcode-pa.googleapis.com', 'daily-cloudcode-pa.googleapis.com', 'aicode.googleapis.com',
           'antigravity.google', 'antigravity.google.com', 'antigravity-unleash.goog', 'oauth2.googleapis.com', 'www.googleapis.com', 'lh3.googleusercontent.com', 'accounts.google.com',
           'pypi.org', 'pythonhosted.org', 'registry.npmjs.org')


def allowed(host, port):
    return port == 443 and any(host == d or host.endswith('.'+d) for d in DOMAINS)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        upstream = None
        try:
            self.connection.settimeout(10)
            line = self.rfile.readline(4096).decode('ascii').strip().split()
            if len(line) != 3 or line[0] != 'CONNECT':
                self.wfile.write(b'HTTP/1.1 403 Forbidden\r\n\r\n'); return
            host, port_text = line[1].rsplit(':',1)
            host = host.lower().rstrip('.'); port = int(port_text)
            if not allowed(host, port):
                self.wfile.write(b'HTTP/1.1 403 Forbidden\r\n\r\n'); return
            size = 0
            while True:
                header = self.rfile.readline(4096); size += len(header)
                if size > 16384: return
                if header in (b'\r\n', b'\n'): break
                if not header: return
            addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                self.wfile.write(b'HTTP/1.1 403 Forbidden\r\n\r\n'); return
            family, kind, proto, _, address = addresses[0]
            upstream = socket.socket(family, kind, proto); upstream.settimeout(15); upstream.connect(address)
            self.wfile.write(b'HTTP/1.1 200 Connection Established\r\n\r\n'); self.wfile.flush()
            self.connection.settimeout(None); upstream.settimeout(None)
            while True:
                # Long model reasoning can leave an authenticated SSE connection
                # idle for over a minute. Worker runtime limits still bound jobs.
                ready, _, _ = select.select([self.connection,upstream], [], [], 300)
                if not ready: return
                for source in ready:
                    chunk = source.recv(65536)
                    if not chunk: return
                    (upstream if source is self.connection else self.connection).sendall(chunk)
        except (OSError, ValueError, UnicodeError):
            return
        finally:
            if upstream: upstream.close()


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def serve(path):
    path = Path(path); path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists(): path.unlink()
    with Server(str(path), Handler) as server:
        os.chmod(path, 0o600)
        server.serve_forever()
