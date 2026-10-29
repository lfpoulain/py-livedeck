"""Requêtes HTTP (remplace curl / generic-http) et lecture d'une commande curl collée."""
from __future__ import annotations

import base64
import re
import shlex
import urllib.error
import urllib.request


def parse_headers(text: str) -> dict[str, str]:
    headers = {}
    for line in (text or "").splitlines():
        k, sep, v = line.partition(":")
        if sep and k.strip():
            headers[k.strip()] = v.strip()
    return headers


def request(method: str, url: str, body: str = "", headers: str = "", content_type: str = "",
            timeout: float = 5.0) -> tuple[int, str]:
    hdrs = parse_headers(headers)
    data = None
    if method.upper() not in ("GET", "HEAD") or body:
        data = (body or "").encode("utf-8")
        if content_type and not any(k.lower() == "content-type" for k in hdrs):
            hdrs["Content-Type"] = content_type
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(65536).decode("utf-8", "replace")
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
