"""
Install PowerShell 7 (pwsh.exe) on Windows.
Run with: python install_pwsh.py
"""
import subprocess, sys, urllib.request, os, tempfile

def run(cmd, **kw):
    print(f">>> {cmd}")
    return subprocess.run(cmd, shell=True, **kw)

# ── Method 1: winget (Windows 10 1709+ / 11) ──────────────────────────────
print("=== Trying winget ===")
r = run("winget --version", capture_output=True, text=True)
if r.returncode == 0:
    print(f"winget found: {r.stdout.strip()}")
    result = run('winget install --id Microsoft.PowerShell --source winget --accept-package-agreements --accept-source-agreements')
    if result.returncode == 0:
        print("\n✅ PowerShell 7 installed via winget!")
        print("Restart your terminal, then verify with: pwsh --version")
        sys.exit(0)
    else:
        print("winget install failed, trying MSI download...")
else:
    print("winget not available, trying MSI download...")

# ── Method 2: Download MSI directly ──────────────────────────────────────
print("\n=== Downloading PowerShell 7 MSI ===")
# PowerShell 7.4.x LTS — x64
url = "https://github.com/PowerShell/PowerShell/releases/download/v7.4.6/PowerShell-7.4.6-win-x64.msi"
msi = os.path.join(tempfile.gettempdir(), "PowerShell-7.4.6-win-x64.msi")

print(f"Downloading from: {url}")
print(f"Saving to: {msi}")
print("(This is ~100 MB, please wait...)")

try:
    urllib.request.urlretrieve(url, msi, reporthook=lambda b, bs, ts: print(f"\r  {min(b*bs, ts)//1024//1024} MB / {ts//1024//1024} MB", end=""))
    print("\nDownload complete.")
except Exception as e:
    print(f"Download failed: {e}")
    sys.exit(1)

print("\nInstalling (silent)...")
result = run(f'msiexec /i "{msi}" /qn ADD_EXPLORER_CONTEXT_MENU_OPENPOWERSHELL=1 ENABLE_PSREMOTING=1 REGISTER_MANIFEST=1')
if result.returncode == 0:
    print("\n✅ PowerShell 7 installed!")
    print("Restart VS Code / your terminal, then verify with: pwsh --version")
else:
    print(f"\nMSI install failed (code {result.returncode}).")
    print("Try running the MSI manually:")
    print(f"  {msi}")
