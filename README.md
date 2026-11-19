# IPA Signer

Local tool: upload an **IPA + .p12 + .mobileprovision + password**, it **signs**
the app with your enterprise certificate (via `zsign`), then gives you a
**tap-to-install link + QR code** for your iPhone (via a free `cloudflared` tunnel).

Everything runs on your Windows PC. No VPS needed.

```
ipa-signer/
├─ app.py            ← the server
├─ templates/
│   └─ index.html    ← the web UI
├─ bin/              ← put zsign.exe and cloudflared.exe here
├─ uploads/          ← temp (auto-cleaned)
├─ signed/           ← signed IPAs land here
└─ requirements.txt
```

## 1. Install Python deps
```powershell
cd c:\Users\PC\Documents\BBH\ROB\ipa-signer
pip install -r requirements.txt
```

## 2. Get the two tools and drop them in `bin/`

### zsign.exe  (the signer — REQUIRED)
This is the cross-platform iOS code-signer. Windows builds:
- Releases:  https://github.com/zhlynn/zsign/releases
  (look for a Windows `.exe` asset, or an `-win` build)
- If no Windows binary is posted, use the **WSL/Linux** build instead (see note below).

Put the file at:  `bin\zsign.exe`

### cloudflared.exe  (the install tunnel — needed for tap-to-install)
- Download:  https://github.com/cloudflare/cloudflared/releases/latest
  (file: `cloudflared-windows-amd64.exe` → rename to `cloudflared.exe`)

Put the file at:  `bin\cloudflared.exe`

> Without cloudflared the app still **signs** and lets you **download** the IPA;
> you just won't get the direct over-the-air install button.

## 3. Run it
```powershell
python app.py
```
Open **http://127.0.0.1:5000** in your browser.

## 4. Use it
1. Pick your `.ipa`, `.p12`, `.mobileprovision`, and type the cert password.
2. Click **Sign App**.
3. On your **iPhone**: scan the QR code (or open the link), tap **Install**,
   confirm, and the app installs on the home screen.

### First launch of a sideloaded app
With an **enterprise** certificate, after install go to:
**Settings → General → VPN & Device Management → [your developer] → Trust**.
Then the app will open.

---

## If there's no Windows zsign build (use WSL)
```powershell
wsl --install            # once, then reboot
```
In Ubuntu (WSL):
```bash
sudo apt update && sudo apt install -y git g++ pkg-config libssl-dev zlib1g-dev
git clone https://github.com/zhlynn/zsign.git
cd zsign/build/linux && make
# binary appears at ./bin/zsign  — copy it into the Windows bin/ folder:
cp bin/zsign /mnt/c/Users/PC/Documents/BBH/ROB/ipa-signer/bin/zsign
```
`app.py` auto-detects `bin/zsign` (no `.exe`) too.

## Notes / limits
- **Enterprise cert**: installs on any device (needs the Trust step above). Good.
- The tunnel URL changes every run (free cloudflared quick tunnel). That's fine
  for on-demand installs.
- Uploaded `.p12`/profile are deleted right after signing. Signed IPAs stay in
  `signed/` — delete them when done.
- This is for signing **apps you are authorized to distribute** with **your own**
  certificate. Don't use it to redistribute other people's paid apps.
