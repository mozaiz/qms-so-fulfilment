"""Work out every address and name this machine can be reached on.

Shared by the app (the Setup & Addresses screen) and by make_cert.py (the
certificate's Subject Alternative Name list). They must agree: if the setup
screen shows a phone an address the certificate does not cover, the phone gets a
certificate error and the camera will not work — and nothing in the UI would
explain why.

No dependencies. `ip` exists on Linux and not on macOS; `ifconfig` is the other
way round; and on many minimal installs the hostname resolves to 127.0.1.1, which
is worse than useless. So try all of them and merge.
"""

import socket
import subprocess


def _run(cmd, timeout=5):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _usable(ip):
    """Keep real addresses, drop loopback and link-local noise."""
    if not ip or ip.startswith("127.") or ip.startswith("169.254."):
        return False
    try:
        socket.inet_aton(ip)
    except OSError:
        return False
    return True


def local_ipv4():
    """Every non-loopback IPv4 address, best-effort, de-duplicated, ordered."""
    found = []

    def add(ip):
        if _usable(ip) and ip not in found:
            found.append(ip)

    # 1. whatever the hostname resolves to
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except OSError:
        pass

    # 2. the address the default route would use — what a phone on the same wifi
    #    actually sees. No packet is sent.
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            add(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass

    # 3. ask the OS for everything on every interface
    for line in _run(["ip", "-4", "-o", "addr", "show"]).splitlines():
        parts = line.split()
        if len(parts) >= 4 and "/" in parts[3]:
            add(parts[3].split("/")[0])

    for line in _run(["ifconfig"]).splitlines():
        line = line.strip()
        if line.startswith("inet "):
            add(line.split()[1])

    return found


def local_names():
    """Names a device could type or that resolve to this machine."""
    names = ["localhost"]
    try:
        host = socket.gethostname()
    except OSError:
        return names
    if host:
        for n in (host, host.split(".")[0] + ".local"):
            if n not in names:
                names.append(n)
    return names


def ipv6_loopback():
    return ["::1"]


def all_names_and_ips():
    """Everything the certificate must cover."""
    names = set(local_names()) | {"127.0.0.1"}
    ips = set(local_ipv4()) | {"127.0.0.1"}
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        try:
            s.connect(("2001:4860:4860::8888", 80))
            ips.add(s.getsockname()[0].split("%")[0])
        finally:
            s.close()
    except OSError:
        pass
    return names, ips


if __name__ == "__main__":
    n, i = all_names_and_ips()
    print("names:", ", ".join(sorted(n)))
    print("addresses:", ", ".join(sorted(i)))
