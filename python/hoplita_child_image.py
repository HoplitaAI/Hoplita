
import os
import shutil
import subprocess
import sys
import tempfile

IMAGE = "hoplita-child"

_DIRS = ("lang", "wb_canvas", "wb_instructions")

_SKIP_REQ = ("pyqt6",)

DOCKERFILE = """\
FROM docker.io/library/python:3.12-slim
# The tools a child is sent to use: media, fetching, and the two launchers
# most MCP servers ship for (npx and uvx).
RUN export DEBIAN_FRONTEND=noninteractive \\
 && apt-get update \\
 && apt-get install -y --no-install-recommends ffmpeg git curl ca-certificates \\
        nodejs npm \\
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt uv
COPY app /app
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENV HOPLITA_HOME=/data PYTHONUNBUFFERED=1
VOLUME /data
WORKDIR /data
ENTRYPOINT ["/entrypoint.sh"]
"""

ENTRYPOINT = """\
#!/bin/sh
set -e
mkdir -p /data
[ -e /data/lang ] || ln -s /app/lang /data/lang
[ -f /data/config.ini ] || cp /app/config.ini.example /data/config.ini
# Open vault unless a password was given (see hoplita_child_image).
[ -n "$HOPLITA_VAULT_PASSWORD" ] || export HOPLITA_VAULT=open
exec python /app/hoplita-mcp.py --host 127.0.0.1 --port 9077
"""


def _code():
    return os.path.dirname(os.path.abspath(__file__))


def _beside(code, name):
    for p in (os.path.join(code, name), os.path.join(code, "..", name)):
        if os.path.exists(p):
            return os.path.abspath(p)
    return None


def runtime():
    for rt in ("docker", "podman"):
        if shutil.which(rt):
            return rt
    return ""


def _requirements(code):
    p = _beside(code, "requirements.txt")
    if not p:
        return None
    keep = []
    for line in open(p, encoding="utf-8"):
        name = line.split("#", 1)[0].strip()
        if name and not name.lower().startswith(_SKIP_REQ):
            keep.append(name)
    return keep


def stage(dst, code=None):
    code = code or _code()
    if not os.path.isfile(os.path.join(code, "hoplita-mcp.py")):
        return f"No HOPLITA code found in {code}."
    reqs = _requirements(code)
    if reqs is None:
        return (f"No requirements.txt beside {code}: update the installation "
                "(install.py --update) so the image knows what to install.")
    ex = _beside(code, "config.ini.example")
    if not ex:
        return f"No config.ini.example beside {code}."
    app = os.path.join(dst, "app")
    os.makedirs(app)
    for f in os.listdir(code):
        if f.endswith(".py"):
            shutil.copy2(os.path.join(code, f), app)
    for d in _DIRS:
        src = _beside(code, d)
        if src and os.path.isdir(src):
            shutil.copytree(src, os.path.join(app, d), dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(ex, app)
    with open(os.path.join(dst, "requirements.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(reqs) + "\n")
    with open(os.path.join(dst, "Dockerfile"), "w", encoding="utf-8") as fh:
        fh.write(DOCKERFILE)
    with open(os.path.join(dst, "entrypoint.sh"), "w", encoding="utf-8") as fh:
        fh.write(ENTRYPOINT)
    return ""


LABEL = "hoplita.build"


def _this_build():
    try:
        import hoplita_version
        return hoplita_version.BUILD
    except Exception:
        return ""


def image_exists(rt):
    try:
        r = subprocess.run([rt, "image", "inspect", "--format",
                            "{{index .Config.Labels \"" + LABEL + "\"}}", IMAGE],
                           capture_output=True, text=True, timeout=30,
                           **__import__("hoplita_paths").no_console())
    except Exception:
        return False
    return r.returncode == 0 and (r.stdout or "").strip() == _this_build()


def build(rt=None, code=None, timeout=1800):
    rt = rt or runtime()
    if not rt:
        return False, "Neither docker nor podman is installed on this machine."
    tmp = tempfile.mkdtemp(prefix="hoplita-child-")
    try:
        err = stage(tmp, code)
        if err:
            return False, err
        try:
            r = subprocess.run([rt, "build", "-t", IMAGE,
                                "--label", f"{LABEL}={_this_build()}", tmp],
                               capture_output=True, text=True, timeout=timeout,
                               **__import__("hoplita_paths").no_console())
        except subprocess.TimeoutExpired:
            return False, f"The image build took over {timeout} s and was stopped."
        out = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        return r.returncode == 0, "\n".join(out[-15:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run_args(rt, handle, server_url, token, network=None):
    net = network if network is not None else os.environ.get("HOPLITA_CHILD_NETWORK", "")
    args = [rt, "run", "-d", "--name", handle, "--restart", "unless-stopped",
            "-v", f"{handle}:/data"]
    if net:
        args += ["--network", net]
    return args + ["-e", f"HOPLITA_SPAWN_SERVER={server_url}",
                   "-e", f"HOPLITA_SPAWN_HANDLE={handle}",
                   "-e", f"HOPLITA_SPAWN_TOKEN={token}", IMAGE]


def launch(handle, server_url, token, rt=None):
    rt = rt or runtime()
    if not rt:
        return {"error": "Neither docker nor podman is installed on this machine."}
    built = False
    if not image_exists(rt):
        ok, tail = build(rt)
        if not ok:
            return {"error": f"Building the {IMAGE} image failed:\n{tail}"}
        built = True
    try:
        r = subprocess.run(run_args(rt, handle, server_url, token),
                           capture_output=True, text=True, timeout=120,
                           **__import__("hoplita_paths").no_console())
    except subprocess.TimeoutExpired:
        return {"error": f"{rt} run did not answer in 120 s."}
    if r.returncode != 0:
        return {"error": f"{rt} run failed: {(r.stderr or r.stdout).strip()[-600:]}"}
    return {"ok": True, "container": handle, "built": built, "runtime": rt}


if __name__ == "__main__":
    if sys.argv[1:] == ["build"]:
        ok, tail = build()
        print(tail)
        sys.exit(0 if ok else 1)
    print(__doc__)
    sys.exit(2)
