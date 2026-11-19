# USB install (branch: `usb-install`)

Adds a **"Install via USB cable"** button. After signing, if an iPhone is
connected and the toolchain is present, the app pushes the signed IPA straight
to the phone — no QR, no tunnel.

It uses [`libimobiledevice`](https://libimobiledevice.org/):
- `ideviceinstaller.exe` — installs the IPA
- `idevice_id.exe` — lists connected devices

Place both (plus their `.dll` dependencies) in `bin/`. The app auto-detects them.

## The driver requirement (important)

`ideviceinstaller` talks to the iPhone through the **Apple Mobile Device
driver**, installed by the **classic desktop iTunes** — *not* the Microsoft
Store "iTunes"/"Apple Devices" app.

Check what you have (PowerShell):
```powershell
Get-Service Apple*          # classic iTunes => "Apple Mobile Device Service"
Get-AppxPackage *AppleInc*  # Store version  => "AppleInc.iTunes"
```
- If you see **Apple Mobile Device Service** → you're good.
- If you only see the **AppleInc.iTunes** appx (Store) → USB install won't work
  until you install classic iTunes. The Store app keeps the driver private to
  itself; other tools can't use it.

### To switch to the classic driver
1. Optional: uninstall the Store "iTunes"/"Apple Devices" app.
2. Download classic **iTunes64Setup.exe** from Apple's support downloads
   (the standalone installer, not the Store listing).
3. Install it — this adds *Apple Mobile Device Support* + the service.
4. Reconnect the iPhone, unlock it, tap **Trust This Computer**.

## Getting the libimobiledevice binaries
Prebuilt Windows binaries:
- `imobiledevice-net` releases: https://github.com/libimobiledevice-win32/imobiledevice-net/releases
  (grab the win-x64 zip; copy `ideviceinstaller.exe`, `idevice_id.exe` and all
  `.dll` files into `bin/`)

Verify from the project folder:
```powershell
.\bin\idevice_id.exe -l          # should print your iPhone's UDID
```
If that prints a UDID, the USB button will light up after you sign.

## Alternative: WSL + usbipd (no iTunes change)
`usbipd-win` is already installed on this PC. You can forward the iPhone into
WSL (Kali) and run libimobiledevice there instead:
```powershell
usbipd list                      # find the Apple Mobile Device BUSID
usbipd bind   --busid <BUSID>
usbipd attach --wsl --busid <BUSID>
```
Then in WSL: `sudo apt install libimobiledevice-utils ideviceinstaller usbmuxd`,
`sudo usbmuxd`, `idevice_id -l`, `ideviceinstaller -i signed.ipa`.
(This path isn't wired into the web UI — it's a manual fallback.)

## What the UI does
- `GET /devices` → `{usb_available, devices:[udid,...]}`
- After `POST /sign`, the response includes `signed_name`, `usb_available`,
  `usb_devices`.
- `POST /install_usb` with `name=<signed ipa filename>` (+ optional `udid`)
  runs `ideviceinstaller -u <udid> -i signed/<name>`.
