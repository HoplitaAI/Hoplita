import getpass
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

HERE = os.path.dirname(os.path.abspath(__file__))


def change(mcp_url, current, new, timeout=600):
    import requests
    try:
        r = requests.post(mcp_url.rstrip("/") + "/vault/password",
                          json={"current": current, "new": new}, timeout=timeout)
    except Exception as e:
        return {"error": "unreachable", "message": str(e)}
    try:
        body = r.json()
    except Exception:
        body = {}
    if r.status_code == 200 and body.get("ok"):
        return body
    if "error" not in body:
        body = {"error": "failed", "message": f"HTTP {r.status_code}"}
    return body


_ERRORS = {
    "wrong_current": "vault.err_wrong_current",
    "open_vault": "vault.err_open_vault",
    "same": "vault.err_same",
    "empty": "vault.err_empty",
    "busy": "vault.err_busy",
    "no_store": "vault.err_no_store",
    "local_only": "vault.err_local_only",
}


def explain(result, t, url=""):
    code = str(result.get("error") or "failed")
    if code == "unreachable":
        return t("vault.err_unreachable", url=url)
    if code in _ERRORS:
        return t(_ERRORS[code])
    return t("vault.err_failed", message=str(result.get("message") or code))


def outcome(result, t):
    lines = [t("vault.changed", backup=result.get("backup") or "backups/")]
    lines.append(t("vault.changed_env"))
    return lines


def main():
    import configparser
    sys.path.insert(0, HERE)
    import hoplita_paths
    from hoplita_i18n import load_language, t
    cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cfg.read(hoplita_paths.config_path(), encoding="utf-8")
    url = cfg.get("MCP", "url", fallback="http://127.0.0.1:9077").rstrip("/")
    load_language(cfg.get("APP", "language", fallback="en"))
    print(t("vault.cli_intro", url=url))
    current = getpass.getpass(t("vault.current") + ": ")
    new = getpass.getpass(t("vault.new") + ": ")
    again = getpass.getpass(t("vault.again") + ": ")
    if new != again:
        print(t("vault.mismatch"))
        return 1
    print(t("vault.working"))
    r = change(url, current, new)
    if r.get("ok"):
        for line in outcome(r, t):
            print(line)
        return 0
    print(explain(r, t, url))
    return 1


if __name__ == "__main__":
    sys.exit(main())
