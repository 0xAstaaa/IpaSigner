# IPA Signer

A small local web tool to **re-sign an iOS `.ipa`** with your own certificate
and install it on an iPhone — either **over-the-air** (QR / install link via a
free Cloudflare tunnel) or **straight over the USB cable**.

Everything runs on your own machine. No account, no cloud build service.

```
┌─────────────── your PC ───────────────┐
│  web UI: pick .ipa + .p12 + .mobileprov │
│            │                            │
│         zsign  (re-sign)                │
│            │                            │
│   ┌────────┴─────────┐                  │
│   │                  │                  │
│  USB cable        OTA tunnel            │
│ ideviceinstaller  cloudflared + QR      │
└───┼──────────────────┼──────────────────┘
    ▼                  ▼
          your iPhone installs
```

## Features
- Re-sign any `.ipa` with a `.p12` cert + `.mobileprovision` profile
- **USB install** to a connected iPhone (via `libimobiledevice`)
- **Over-the-air install** with a QR code / `itms-services://` link (via `cloudflared`)
- **Sign from a local path** — no slow upload for large (hundreds-of-MB) apps
- Served by `waitress` so big uploads don't time out
- Logs each stage with timing to `app.log`

## Requirements
- **Python 3.9+**
- **Windows** (paths/driver notes below are Windows-specific; the Python is cross-platform)
- External binaries placed in `bin/` (see Setup):
  - [`zsign`](https://github.com/zhlynn/zsign) — the signer *(required)*
  - [`cloudflared`](https://github.com/cloudflare/cloudflared) — for OTA install *(optional)*
  - [`libimobiledevice`](https://github.com/libimobiledevice-win32/imobiledevice-net/releases)
    (`ideviceinstaller`, `idevice_id` + DLLs) — for USB install *(optional)*
- A code-signing **certificate** you are authorized to use (an enterprise cert
  installs on any device; a normal dev/ad-hoc cert only installs on devices
  whose UDID is in the provisioning profile).

## Setup
```bash
pip install -r requirements.txt
```
Place the binaries in `bin/` (they are git-ignored, not committed). For USB
install on Windows you also need the **Apple Mobile Device driver** — see
[README-usb.md](README-usb.md) (`setup-usb.ps1` automates it).

## Run
```bash
python app.py
```
Open **http://127.0.0.1:5000**.

> For large apps, open the tool on `127.0.0.1` (local) and either pick the file
> or paste its path — don't upload a 500 MB IPA through the public tunnel.

## Use
1. Provide the `.ipa` (file or local path), `.p12`, `.mobileprovision`, password.
2. **Sign App.**
3. Install:
   - **USB:** tap *Install via USB* (iPhone connected + trusted).
   - **OTA:** tick *Also create QR / OTA link*, then scan the QR on the iPhone.
4. First launch of an enterprise-signed app:
   **Settings → General → VPN & Device Management → [developer] → Trust.**

## Limitations (read this)
- **Encrypted App Store apps cannot be sideloaded.** A `.ipa` pulled from the
  App Store is FairPlay-encrypted (`cryptid=1`, no `SC_Info/*.sinf`). Re-signing
  does **not** decrypt it — it will be rejected or crash on launch. You need a
  **decrypted** IPA (a non-App-Store build, your own app, or a legitimately
  decrypted dump).
- Re-signing rewrites the app's **bundle id** to one your profile allows, so the
  installed app has a new identity (it won't "upgrade" the App Store version).
- The free Cloudflare quick-tunnel URL **changes every run**.

## Legal
Use this only to sign apps **you are authorized to distribute** with **your own**
certificate (your own apps, enterprise distribution, development builds). Do not
use it to remove DRM from, repackage, or redistribute apps you don't have the
rights to.

## Files
| Path | What |
|------|------|
| `app.py` | the server (Flask + waitress) |
| `templates/index.html` | the web UI |
| `setup-usb.ps1` | one-time Windows USB-driver setup |
| `README-usb.md` | USB install details |
| `bin/` | drop external binaries here (git-ignored) |
| `uploads/`, `signed/` | runtime scratch (git-ignored) |
