#!/usr/bin/env python3
"""Render a demo's CloudFront test page from demos/_kit/web/page.html.tmpl and the "web" block of demos/<demo>/demo.json.

    render.py <demo> --out DIR            write DIR/index.html
    render.py <demo> --stack-params       print the (non-secret) CloudFormation parameter overrides, one Key=Value per line
    render.py <demo> --get <key>          print one value of the demo's "web" block

Environment (set by deploy-web.sh, never written to the repo):
    ACXD_KEYS_JSON   JSON {"deploymentKey": "...", "apiKey": "..."} from Secrets Manager demo-kit/web/<demo>/acxd.
                     The page only gets a Live Sync block when both values are present. These are browser-side keys by
                     design (the ACXD Deploy tab puts them in the page), so anyone who opens the page can read them.
    WEB_CHAT_READY   "1" when the demo's chat flow exists and is wired; otherwise the page shows "chat not available".

Optional per-demo files (trusted repo content, inserted as is):
    demos/<demo>/web/actions.js   after Touchpoint starts; can use the variable `touchpoint` and `await`
                                  (for example touchpoint.sendContext({actions: [...]}) for Live Sync)
    demos/<demo>/web/body.html    extra page content, shown above the "Try asking" card
    demos/<demo>/web/extra.css    extra styles (the page defines --accent, --ink, --muted, --bg, --card, --line)
"""
import argparse
import html
import json
import os
import re
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
DEMOS = KIT.parent
TP_VERSION = "1.0.4"   # @amazon-connect-touchpoint/web, pinned (supports liveSync; checked 2026-10-19)
TP_SRI = "sha384-5it4W2WciU5/f3WlfMZtKdXEv3wrcwTPpOJHqJX3qE2g9pHsXYDtX1xWKr/O0omW"


def load_demo(name):
    d = DEMOS / name
    if not (d / "demo.json").is_file():
        sys.exit(f"unknown demo '{name}'")
    cfg = json.loads((d / "demo.json").read_text(encoding="utf-8"))
    if "web" not in cfg:
        sys.exit(f"demo '{name}' has no \"web\" block in demo.json")
    return d, cfg


def _js(obj):
    """JSON that is safe inside an HTML <script> block."""
    s = json.dumps(obj, ensure_ascii=False)
    for ch, rep in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        s = s.replace(ch, rep)
    return s


def _live_sync(env):
    try:
        keys = json.loads(env.get("ACXD_KEYS_JSON") or "")
    except ValueError:
        return None
    if isinstance(keys, dict) and keys.get("deploymentKey") and keys.get("apiKey"):
        return {"deploymentKey": str(keys["deploymentKey"]), "apiKey": str(keys["apiKey"])}
    return None


def render(name, env=None):
    env = os.environ if env is None else env
    d, cfg = load_demo(name)
    web = cfg["web"]
    live = _live_sync(env)
    page_cfg = {
        "demo": cfg["name"],
        "instanceId": web.get("connect_instance_id", ""),
        "region": cfg["region"],
        "displayName": web.get("display_name", "Customer"),
        "assistantName": web.get("assistant_name", "Assistant"),
        "language": web.get("language", "en-US"),
        "accent": web.get("accent", "#0b6bcb"),
        "chatReady": env.get("WEB_CHAT_READY") == "1" and bool(web.get("connect_instance_id")),
        "liveSync": live,
    }
    def extra(name):
        f = d / "web" / name
        return f.read_text(encoding="utf-8") if f.is_file() else ""

    actions, extra_body, extra_css = extra("actions.js"), extra("body.html"), extra("extra.css")
    prompts = "".join(f"<li>{html.escape(p)}</li>" for p in web.get("prompts", []))
    tmpl = (Path(__file__).resolve().parent / "page.html.tmpl").read_text(encoding="utf-8")
    values = {
        "__LANG__": html.escape(page_cfg["language"][:2] or "en"),
        "__TITLE__": html.escape(web.get("title", cfg.get("title", name))),
        "__BRAND__": html.escape(web.get("brand", name)),
        "__BLURB__": html.escape(web.get("blurb", "")),
        "__PROMPTS__": prompts,
        "__ACCENT__": html.escape(page_cfg["accent"]) if page_cfg["accent"].startswith("#") else "#0b6bcb",
        "__TP_VERSION__": TP_VERSION,
        "__TP_SRI__": TP_SRI,
        "__CONFIG__": _js(page_cfg),
        "__ACTIONS__": actions,
        "__EXTRA_BODY__": extra_body,
        "__EXTRA_CSS__": extra_css,
    }
    # one pass over the template: substituted text is never scanned again, so a title or blurb cannot inject a token
    return re.sub(r"__[A-Z_]+__", lambda m: values.get(m.group(0), m.group(0)), tmpl)


def stack_params(name):
    _, cfg = load_demo(name)
    web = cfg["web"]
    acxd = web.get("acxd") or {}
    pairs = [("ConnectInstanceId", web.get("connect_instance_id")), ("AcxdWorkspaceId", acxd.get("workspace_id")),
             ("AcxdApplicationId", acxd.get("application_id"))]
    return [f"{k}={v}" for k, v in pairs if v]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("demo")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--out")
    g.add_argument("--stack-params", action="store_true")
    g.add_argument("--get")
    a = ap.parse_args()
    if a.stack_params:
        lines = stack_params(a.demo)
        if lines:                      # print nothing at all when there is nothing, so the shell array stays empty
            print("\n".join(lines))
    elif a.get:
        _, cfg = load_demo(a.demo)
        v = cfg["web"].get(a.get, "")
        print(v if isinstance(v, str) else json.dumps(v))
    else:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text(render(a.demo), encoding="utf-8")
        live = _live_sync(os.environ)
        print(f"page written to {out / 'index.html'} (chat ready: {os.environ.get('WEB_CHAT_READY') == '1'}, "
              f"Live Sync: {'on' if live else 'off'})")


if __name__ == "__main__":
    main()
