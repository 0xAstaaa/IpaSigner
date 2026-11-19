"""
IPA Signer - local tool
Upload an IPA + .p12 + .mobileprovision + password, sign with zsign,
then install on your iPhone over-the-air via a cloudflared tunnel.

Run:  python app.py
Then open http://127.0.0.1:5000 in your browser.
"""

import os
import re
import sys
import uuid
import socket
import shutil
import plistlib
import subprocess
import threading
import time

from flask import (
    Flask, request, render_template, jsonify,
    send_from_directory, url_for
)

# ---------------------------------------------------------------- paths
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
SIGNED_DIR = os.path.join(BASE_DIR, "signed")
BIN_DIR    = os.path.join(BASE_DIR, "bin")

for d in (UPLOAD_DIR, SIGNED_DIR, BIN_DIR):
    os.makedirs(d, exist_ok=True)

# zsign / cloudflared executables (place them in ./bin)
def _exe(name):
    win = os.path.join(BIN_DIR, name + ".exe")
    nix = os.path.join(BIN_DIR, name)
    if os.path.exists(win):
        return win
    if os.path.exists(nix):
        return nix
    # fall back to PATH
    found = shutil.which(name)
    return found or (win if os.name == "nt" else nix)

ZSIGN       = _exe("zsign")
CLOUDFLARED = _exe("cloudflared")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024  # 4 GB

# ---------------------------------------------------------------- tunnel state
_tunnel = {"url": None, "proc": None}


def start_tunnel(port):
    """Start a cloudflared quick tunnel pointing at our local port.
    Returns the public https url, or None if cloudflared isn't available."""
    if _tunnel["url"]:
        return _tunnel["url"]
    if not os.path.exists(CLOUDFLARED) and not shutil.which("cloudflared"):
        return None

    proc = subprocess.Popen(
        [CLOUDFLARED, "tunnel", "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    _tunnel["proc"] = proc

    url_re = re.compile(r"https://[-\w]+\.trycloudflare\.com")
    deadline = time.time() + 30
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            continue
        m = url_re.search(line)
        if m:
            _tunnel["url"] = m.group(0)
            # keep draining stdout so the pipe doesn't fill up
            threading.Thread(
                target=lambda: [None for _ in proc.stdout],
                daemon=True,
            ).start()
            return _tunnel["url"]
    return None


# ---------------------------------------------------------------- signing
def run_zsign(ipa_path, p12_path, prov_path, password, out_path):
    """Call zsign. Returns (success: bool, log: str)."""
    if not (os.path.exists(ZSIGN) or shutil.which("zsign")):
        return False, (
            "zsign not found. Download zsign.exe and put it in the 'bin' "
            "folder next to app.py. See the README."
        )

    cmd = [
        ZSIGN,
        "-k", p12_path,          # .p12 private key / cert
        "-p", password,          # p12 password
        "-m", prov_path,         # .mobileprovision
        "-o", out_path,          # output ipa
        "-z", "9",               # max compression
        ipa_path,                # input ipa
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=1800
        )
    except subprocess.TimeoutExpired:
        return False, "zsign timed out (>30 min)."

    log = (proc.stdout or "") + "\n" + (proc.stderr or "")
    ok = proc.returncode == 0 and os.path.exists(out_path)
    return ok, log


def read_ipa_metadata(ipa_path):
    """Pull bundle id + display name + version from the IPA's Info.plist."""
    import zipfile
    info = {"bundle_id": "", "name": "", "version": "1.0"}
    try:
        with zipfile.ZipFile(ipa_path) as z:
            plist_name = None
            for n in z.namelist():
                m = re.match(r"Payload/[^/]+\.app/Info\.plist$", n)
                if m:
                    plist_name = n
                    break
            if plist_name:
                data = z.read(plist_name)
                pl = plistlib.loads(data)
                info["bundle_id"] = pl.get("CFBundleIdentifier", "")
                info["name"] = (
                    pl.get("CFBundleDisplayName")
                    or pl.get("CFBundleName", "App")
                )
                info["version"] = pl.get("CFBundleShortVersionString", "1.0")
    except Exception as e:
        info["error"] = str(e)
    return info


def make_manifest(ipa_url, bundle_id, version, title):
    """Build the OTA manifest.plist iOS needs for itms-services installs."""
    manifest = {
        "items": [
            {
                "assets": [
                    {"kind": "software-package", "url": ipa_url}
                ],
                "metadata": {
                    "bundle-identifier": bundle_id or "com.example.app",
                    "bundle-version": version or "1.0",
                    "kind": "software",
                    "title": title or "App",
                },
            }
        ]
    }
    return plistlib.dumps(manifest)


# ---------------------------------------------------------------- routes
@app.route("/")
def index():
    return render_template(
        "index.html",
        zsign_ok=bool(os.path.exists(ZSIGN) or shutil.which("zsign")),
        cf_ok=bool(os.path.exists(CLOUDFLARED) or shutil.which("cloudflared")),
    )


@app.route("/sign", methods=["POST"])
def sign():
    ipa  = request.files.get("ipa")
    p12  = request.files.get("p12")
    prov = request.files.get("prov")
    pwd  = request.form.get("password", "")

    if not (ipa and p12 and prov):
        return jsonify(ok=False, error="Please provide IPA, .p12 and .mobileprovision files."), 400

    job = uuid.uuid4().hex[:8]
    job_dir = os.path.join(UPLOAD_DIR, job)
    os.makedirs(job_dir, exist_ok=True)

    ipa_path  = os.path.join(job_dir, "input.ipa")
    p12_path  = os.path.join(job_dir, "cert.p12")
    prov_path = os.path.join(job_dir, "profile.mobileprovision")
    ipa.save(ipa_path)
    p12.save(p12_path)
    prov.save(prov_path)

    meta = read_ipa_metadata(ipa_path)

    out_name = f"{job}.ipa"
    out_path = os.path.join(SIGNED_DIR, out_name)
    ok, log = run_zsign(ipa_path, p12_path, prov_path, pwd, out_path)

    # cleanup sensitive inputs
    try:
        shutil.rmtree(job_dir)
    except Exception:
        pass

    if not ok:
        return jsonify(ok=False, error="Signing failed.", log=log), 500

    # read fresh metadata from the SIGNED ipa (bundle id may be rewritten)
    signed_meta = read_ipa_metadata(out_path)
    bundle_id = signed_meta.get("bundle_id") or meta.get("bundle_id")
    version   = signed_meta.get("version") or meta.get("version")
    title     = signed_meta.get("name") or meta.get("name") or "App"

    # start tunnel (needed for OTA install on device)
    port = request.environ.get("SERVER_PORT", "5000")
    public = start_tunnel(port)

    if public:
        ipa_url = f"{public}/signed/{out_name}"
        manifest_bytes = make_manifest(ipa_url, bundle_id, version, title)
        man_name = f"{job}.plist"
        with open(os.path.join(SIGNED_DIR, man_name), "wb") as f:
            f.write(manifest_bytes)
        manifest_url = f"{public}/signed/{man_name}"
        install_url = f"itms-services://?action=download-manifest&url={manifest_url}"
    else:
        ipa_url = url_for("signed_file", filename=out_name, _external=True)
        install_url = None

    return jsonify(
        ok=True,
        title=title,
        bundle_id=bundle_id,
        version=version,
        ipa_url=ipa_url,
        install_url=install_url,
        tunnel=bool(public),
        log=log,
    )


@app.route("/signed/<path:filename>")
def signed_file(filename):
    # iOS needs the right content types for OTA
    resp = send_from_directory(SIGNED_DIR, filename)
    if filename.endswith(".ipa"):
        resp.headers["Content-Type"] = "application/octet-stream"
    elif filename.endswith(".plist"):
        resp.headers["Content-Type"] = "text/xml"
    return resp


def get_lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    port = 5000
    print("=" * 60)
    print(" IPA Signer running")
    print(f"  Open:  http://127.0.0.1:{port}")
    print(f"  LAN :  http://{get_lan_ip()}:{port}")
    print("=" * 60)
    if not (os.path.exists(ZSIGN) or shutil.which("zsign")):
        print("  [!] zsign not found - put zsign.exe in ./bin  (see README)")
    if not (os.path.exists(CLOUDFLARED) or shutil.which("cloudflared")):
        print("  [!] cloudflared not found - OTA install link disabled")
        print("      (signing still works; you'll get the IPA file)")
    app.run(host="0.0.0.0", port=port, threaded=True)
