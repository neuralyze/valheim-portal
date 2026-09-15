#!/usr/bin/env python3
"""Drive the portal's admin surface: regenerate a world's map and read the analysis.

The admin POST is CSRF-protected in the double-submit form: a GET on any admin
page sets a `portal_csrf` cookie and embeds the same nonce in a hidden `csrf`
field, and the POST has to carry both.  So this GETs the map page first, keeps
the cookie, scrapes the field, and posts with the pair.

Auth is the `X-Portal-Admin-Token` header plus `X-Forwarded-User`, which is what
the reverse proxy would normally supply.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:18080"
TOKEN_PATH = "/etc/valheim-portal/admin-token"
CSRF_RE = re.compile(r'name="csrf"\s+value="([^"]+)"')


def admin_token() -> str:
    out = subprocess.run(["sudo", "-n", "cat", TOKEN_PATH], capture_output=True, text=True, check=True)
    return out.stdout.strip()

class Portal:
    """MEASURED gotcha: the portal sets `portal_csrf` with the `Secure` flag, and
    both urllib's and curl's cookie jars refuse to send a Secure cookie over
    plain http.  The portal is only reachable here as http://127.0.0.1:18080,
    so the cookie is captured from the Set-Cookie header and replayed as an
    explicit `Cookie:` header instead of being managed by a jar.  The cookie
    value and the hidden field value DIFFER (the pair is HMAC-linked, not
    double-submit-equal), so both halves have to come from the same GET."""

    def __init__(self, user: str = "operator"):
        self.token = admin_token()
        self.user = user
        self.cookie = ""

    def _headers(self) -> dict[str, str]:
        head = {"X-Portal-Admin-Token": self.token, "X-Forwarded-User": self.user}
        if self.cookie:
            head["Cookie"] = f"portal_csrf={self.cookie}"
        return head

    def get(self, path: str) -> str:
        req = urllib.request.Request(BASE + path, headers=self._headers())
        with urllib.request.urlopen(req, timeout=600) as resp:
            for key, value in resp.getheaders():
                if key.lower() == "set-cookie" and value.startswith("portal_csrf="):
                    self.cookie = value.split("=", 1)[1].split(";", 1)[0]
            return resp.read().decode("utf-8", "replace")

    def post(self, path: str, page_for_csrf: str, fields: dict[str, str] | None = None) -> int:
        page = self.get(page_for_csrf)
        found = CSRF_RE.search(page)
        if not found:
            raise SystemExit(f"no csrf field on {page_for_csrf}")
        form = {"csrf": found.group(1)}
        form.update(fields or {})
        body = urllib.parse.urlencode(form).encode()
        req = urllib.request.Request(BASE + path, data=body, headers={
            **self._headers(), "Content-Type": "application/x-www-form-urlencoded"})
        # The handler answers 303 on success; urllib would follow it and lose the code.
        opener = urllib.request.build_opener(NoRedirect)
        with opener.open(req, timeout=3600) as resp:
            return resp.status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("op", choices=["analysis", "regenerate"])
    ap.add_argument("--world", default="Ulfsland")
    ap.add_argument("--out")
    args = ap.parse_args()

    portal = Portal()
    if args.op == "regenerate":
        portal.post(f"/admin/worlds/{args.world}/analysis", f"/admin/worlds/{args.world}/map")
        print(f"regenerate posted for {args.world}")
    body = portal.get(f"/admin/worlds/{args.world}/analysis.json")
    if args.out:
        Path(args.out).write_text(body)
        print(f"analysis -> {args.out} ({len(body)} bytes)")
    else:
        doc = json.loads(body)
        print(json.dumps(doc.get("summary", doc), indent=1)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
