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

ZSIGN            = _exe("zsign")
CLOUDFLARED      = _exe("cloudflared")
IDEVICEINSTALLER = _exe("ideviceinstaller")
IDEVICE_ID       = _exe("idevice_id")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024  # 4 GB


# ---------------------------------------------------------------- USB install
def _have(path):
    return bool(path and (os.path.exists(path) or shutil.which(os.path.basename(path))))


def usb_available():
    """Is the native USB-install toolchain present?"""
    return _have(IDEVICEINSTALLER)


def list_usb_devices():
    """Return a list of connected iPhone UDIDs via idevice_id, or []."""
    tool = IDEVICE_ID if _have(IDEVICE_ID) else None
    if not tool:
        return []
    try:
        proc = subprocess.run([tool, "-l"], capture_output=True, text=True, timeout=15)
    except Exception:
        return []
    udids = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    return udids


def usb_install(ipa_path, udid=None):
    """Install a signed IPA onto a USB-connected device.
    Returns (ok: bool, log: str)."""
    if not usb_available():
        return False, (
            "ideviceinstaller not found. USB install needs libimobiledevice "
            "(ideviceinstaller.exe + idevice_id.exe) in ./bin AND the Apple "
            "Mobile Device driver (classic iTunes, not the Microsoft Store "
            "version). See README-usb.md."
        )
    devices = list_usb_devices()
    if not devices:
        return False, (
            "No iPhone detected over USB. Check: cable connected, phone "
            "unlocked, 'Trust This Computer' tapped, and the Apple Mobile "
            "Device driver installed (classic iTunes). "
            "If iTunes sees the phone but this doesn't, you likely have the "
            "Microsoft Store iTunes, which does not expose the shared driver."
        )
    target = udid or devices[0]
    cmd = [IDEVICEINSTALLER, "-u", target, "-i", ipa_path]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return False, "ideviceinstaller timed out."
    log = (proc.stdout or "") + "\n" + (proc.stderr or "")
    ok = proc.returncode == 0 and (
        "Complete" in log or "Installing" in log or "- 100%" in log
    )
    return ok, log

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
        "-z", "1",               # fast compression (9 = tiny but very slow)
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
        usb_ok=usb_available(),
    )


@app.route("/devices")
def devices():
    """List connected iPhones (for the USB-install UI)."""
    return jsonify(
        usb_available=usb_available(),
        devices=list_usb_devices(),
    )


@app.route("/install_usb", methods=["POST"])
def install_usb():
    """Install an already-signed IPA (by filename in signed/) over USB."""
    name = request.form.get("name", "")
    udid = request.form.get("udid") or None
    # guard against path traversal
    safe = os.path.basename(name)
    ipa_path = os.path.join(SIGNED_DIR, safe)
    if not safe or not os.path.exists(ipa_path):
        return jsonify(ok=False, error="Signed IPA not found."), 404
    ok, log = usb_install(ipa_path, udid)
    if not ok:
        return jsonify(ok=False, error="USB install failed.", log=log), 500
    return jsonify(ok=True, log=log)


@app.route("/sign", methods=["POST"])
def sign():
    ipa  = request.files.get("ipa")
    p12  = request.files.get("p12")
    prov = request.files.get("prov")
    pwd  = request.form.get("password", "")
    # Optional: a path to an IPA already on THIS PC (skips the upload entirely,
    # ideal for large apps). The cert/profile are tiny so still come as uploads.
    local_ipa = (request.form.get("ipa_path") or "").strip().strip('"')

    if not (p12 and prov):
        return jsonify(ok=False, error="Please provide the .p12 and .mobileprovision files."), 400
    if not ipa and not local_ipa:
        return jsonify(ok=False, error="Provide an IPA file, or a local IPA path."), 400
    if local_ipa and not os.path.isfile(local_ipa):
        return jsonify(ok=False, error=f"Local IPA not found: {local_ipa}"), 400

    job = uuid.uuid4().hex[:8]
    job_dir = os.path.join(UPLOAD_DIR, job)
    os.makedirs(job_dir, exist_ok=True)

    ipa_path  = os.path.join(job_dir, "input.ipa")
    p12_path  = os.path.join(job_dir, "cert.p12")
    prov_path = os.path.join(job_dir, "profile.mobileprovision")
    if local_ipa:
        ipa_path = local_ipa          # sign in place; don't copy 500MB around
    else:
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

    # Only spin up the tunnel + OTA manifest if the user wants the QR/link
    # path. For USB install it's unnecessary (and slow to start), so skip it.
    want_ota = request.form.get("want_ota", "1") != "0"
    port = request.environ.get("SERVER_PORT", "5000")
    public = start_tunnel(port) if want_ota else None

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
        signed_name=out_name,          # for USB install
        usb_available=usb_available(),
        usb_devices=list_usb_devices(),
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
    if usb_available():
        devs = list_usb_devices()
        print(f"  [+] USB install ready - {len(devs)} device(s) connected")
    else:
        print("  [i] USB install off - ideviceinstaller not in ./bin")
        print("      (needs classic iTunes' Apple Mobile Device driver)")
    print("  >> For BIG apps, upload via http://127.0.0.1:5000 (local),")
    print("     NOT the tunnel URL - the tunnel is only for installing on")
    print("     the phone. Local upload of a 500MB IPA is near-instant.")
    print("=" * 60)

    # Production server (waitress) - handles large uploads without the
    # Flask dev-server timeouts. Falls back to Flask's server if missing.
    try:
        from waitress import serve
        serve(
            app,
            host="0.0.0.0",
            port=port,
            threads=8,
            # allow very large uploads and slow clients
            max_request_body_size=6 * 1024 * 1024 * 1024,  # 6 GB
            channel_timeout=1800,                           # 30 min
            cleanup_interval=60,
        )
    except ImportError:
        print("  [i] waitress not installed - using Flask dev server")
        app.run(host="0.0.0.0", port=port, threaded=True)
