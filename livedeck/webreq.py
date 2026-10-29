"""Requêtes HTTP (remplace curl / generic-http) et lecture d'une commande curl collée."""
from __future__ import annotations

import base64
import ipaddress
import re
import shlex
import socket
import urllib.error
import urllib.parse
import urllib.request

# Ouvreur sans proxy : un proxy système ou HTTP_PROXY ne doit jamais intercepter 127.0.0.1 ni le réseau
# local (py-intercom, tallys, orchestream sur le LAN). Les adresses publiques gardent le proxy du système.
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_LOCAL_SUFFIXES = (".local", ".localhost", ".lan", ".home", ".internal", ".intranet", ".home.arpa")


def is_local_host(host: str | None) -> bool:
    """Loopback, IP privée / lien local, « localhost », *.local… ou nom sans point (machine du réseau)."""
    host = (host or "").strip("[]").lower()
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return host == "localhost" or "." not in host or host.endswith(_LOCAL_SUFFIXES)


def is_local_url(url: str) -> bool:
    try:
        return is_local_host(urllib.parse.urlsplit(url).hostname)
    except ValueError:
        return False


def open_url(req, timeout: float):
    """urlopen, mais en direct (sans proxy) pour une adresse locale."""
    url = req.full_url if isinstance(req, urllib.request.Request) else req
    if is_local_url(url):
        return DIRECT.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def error_kind(e: BaseException, host: str = "") -> str:
    """Nature d'une erreur réseau : refused | timeout | dns | reset | network.
    Sous Windows, un port fermé sur 127.0.0.1 ne répond « refusé » qu'au bout de 2 s : en local, un délai
    dépassé veut donc dire « personne n'écoute »."""
    r = getattr(e, "reason", e)
    if isinstance(r, ConnectionRefusedError):
        return "refused"
    try:
        loop = ipaddress.ip_address((host or "").strip("[]")).is_loopback
    except ValueError:
        loop = (host or "").lower() == "localhost"
    if isinstance(r, (TimeoutError, socket.timeout)) or "timed out" in str(r):
        return "refused" if loop else "timeout"
    if isinstance(r, socket.gaierror):
        return "dns"
    if isinstance(r, (ConnectionResetError, ConnectionAbortedError)):
        return "refused" if loop else "reset"  # en local : le programme vient de se fermer
    return "network"


def error_text(e: BaseException, host: str = "") -> str:
    """Erreur réseau en français, pour le journal."""
    return {"refused": "connexion refusée (rien n'écoute sur ce port)",
            "timeout": "pas de réponse (adresse, pare-feu ou appareil éteint ?)",
            "dns": "nom d'hôte introuvable",
            "reset": "connexion coupée par l'appareil"}.get(error_kind(e, host), str(getattr(e, "reason", e)))


def parse_headers(text: str) -> dict[str, str]:
    headers = {}
    for line in (text or "").splitlines():
        k, sep, v = line.partition(":")
        if sep and k.strip():
            headers[k.strip()] = v.strip()
    return headers


def request(method: str, url: str, body: str = "", headers: str = "", content_type: str = "",
            timeout: float = 5.0, max_bytes: int = 65536) -> tuple[int, str]:
    hdrs = parse_headers(headers)
    data = None
    if method.upper() not in ("GET", "HEAD") or body:
        data = (body or "").encode("utf-8")
        if content_type and not any(k.lower() == "content-type" for k in hdrs):
            hdrs["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=hdrs)
    try:
        with open_url(req, timeout) as resp:
            return resp.status, resp.read(max_bytes).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(4096).decode("utf-8", "replace")


_SKIP_WITH_VALUE = {"-o", "--output", "-m", "--max-time", "--connect-timeout", "--retry", "-w",
                    "--write-out", "-x", "--proxy", "--cacert", "--cert", "--key"}


def parse_curl(cmd: str) -> dict:
    """Transforme « curl -X POST http://… -H … -d … » en action HTTP."""
    text = re.sub(r"[\\^`]\s*\r?\n", " ", cmd.strip())  # continuations bash / cmd / PowerShell
    text = re.sub(r"\^(.)", r"\1", text)  # échappements cmd.exe (« copier en cURL (cmd) »)
    args = shlex.split(text, posix=True)
    if args and args[0].lower() in ("curl", "curl.exe"):
        args = args[1:]
    method, url, body, headers, ctype, force_get = "", "", [], [], "", False
    it = iter(args)
    for a in it:
        if a in ("-X", "--request"):
            method = next(it, "").upper()
        elif a in ("-H", "--header"):
            h = next(it, "")
            k, _, v = h.partition(":")
            if k.strip().lower() == "content-type":
                ctype = v.strip()
            else:
                headers.append(f"{k.strip()}: {v.strip()}")
        elif a in ("-d", "--data", "--data-raw", "--data-binary", "--data-ascii", "--data-urlencode"):
            body.append(next(it, ""))
        elif a == "--json":
            body.append(next(it, ""))
            ctype = ctype or "application/json"
            headers.append("Accept: application/json")
        elif a in ("-u", "--user"):
            token = base64.b64encode(next(it, "").encode()).decode()
            headers.append(f"Authorization: Basic {token}")
        elif a in ("-A", "--user-agent"):
            headers.append(f"User-Agent: {next(it, '')}")
        elif a in ("-e", "--referer"):
            headers.append(f"Referer: {next(it, '')}")
        elif a in ("-b", "--cookie"):
            headers.append(f"Cookie: {next(it, '')}")
        elif a in ("-G", "--get"):
            force_get = True
        elif a == "--url":
            url = next(it, "")
        elif a in _SKIP_WITH_VALUE:
            next(it, None)
        elif a.startswith("-"):
            continue
        elif not url:
            url = a
    data = "&".join(body)
    if force_get and data:
        url += ("&" if "?" in url else "?") + data
        data = ""
    if data and not ctype:
        ctype = "application/json" if data.lstrip()[:1] in ("{", "[") else "application/x-www-form-urlencoded"
    return {"method": method or ("POST" if data else "GET"), "url": url, "body": data,
            "headers": "\n".join(headers), "content_type": ctype}
