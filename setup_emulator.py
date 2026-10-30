"""
Prepare the (rooted) Android emulator so mitmproxy can see SpareEat's HTTPS traffic.

  python setup_emulator.py        -> install mitmproxy CA as a system cert + point the emulator's proxy at mitmproxy
  python setup_emulator.py off    -> remove the proxy setting (normal internet again)

The CA install lives on a tmpfs overlay, so it disappears when the emulator reboots;
just run this script again after every emulator restart.
"""
import hashlib
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ADB = Path(os.environ["LOCALAPPDATA"]) / "Android" / "Sdk" / "platform-tools" / "adb.exe"
CA_PEM = Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"
PACKAGE = "spareeat.com.spareeat"
PROXY = "10.0.2.2:8080"  # 10.0.2.2 = the host PC as seen from inside the emulator

INSTALL_SCRIPT = r"""
H=$1
D=/system/etc/security/cacerts
if [ -f $D/$H.0 ]; then echo "mitmproxy CA already installed"; exit 0; fi
B=/data/local/tmp/cacerts-copy
rm -rf $B; mkdir -p $B
cp $D/* $B/
mount -t tmpfs tmpfs $D
cp $B/* $D/
cp /data/local/tmp/$H.0 $D/
chown root:root $D/*
chmod 644 $D/*
chcon u:object_r:system_file:s0 $D/*
echo "mitmproxy CA installed as system cert"
"""


def adb(*args, check=True):
    r = subprocess.run([str(ADB), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"adb {' '.join(args)} failed:\n{r.stdout}{r.stderr}")
    return (r.stdout + r.stderr).strip()


def subject_hash_old(pem: bytes) -> str:
    """Android names system certs <openssl subject_hash_old>.0 (MD5 of the DER subject)."""
    from cryptography import x509
    cert = x509.load_pem_x509_certificate(pem)
    digest = hashlib.md5(cert.subject.public_bytes()).digest()
    return f"{int.from_bytes(digest[:4], 'little'):08x}"


def turn_off():
    adb("shell", "settings", "put", "global", "http_proxy", ":0")
    adb("shell", "am", "force-stop", PACKAGE, check=False)
    print("Proxy removed - emulator uses the internet directly again.")


def turn_on():
    if not CA_PEM.exists():
        sys.exit(f"{CA_PEM} not found. Run 'mitmdump' once (then Ctrl+C) so mitmproxy creates its CA, and retry.")

    print(adb("devices"))
    print(adb("root", check=False))
    time.sleep(2)
    adb("wait-for-device")

    h = subject_hash_old(CA_PEM.read_bytes())
    adb("push", str(CA_PEM), f"/data/local/tmp/{h}.0")

    with tempfile.NamedTemporaryFile("w", newline="\n", suffix=".sh", delete=False) as f:
        f.write(INSTALL_SCRIPT)
        script = f.name
    adb("push", script, "/data/local/tmp/install_ca.sh")
    os.unlink(script)
    print(adb("shell", "sh", "/data/local/tmp/install_ca.sh", h))

    adb("shell", "settings", "put", "global", "http_proxy", PROXY)
    adb("shell", "am", "force-stop", PACKAGE, check=False)
    print(f"Emulator proxy set to {PROXY}. Start run_capture.bat, then open SpareEat in the emulator.")


if __name__ == "__main__":
    if not ADB.exists():
        sys.exit(f"adb not found at {ADB}")
    turn_off() if (len(sys.argv) > 1 and sys.argv[1] == "off") else turn_on()
