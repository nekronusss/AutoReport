import os
import secrets
import sys
from pathlib import Path

if sys.argv[1:] != ["local-init"]:
    raise SystemExit("Usage: bootstrap local-init (local only)")
root = Path("/secrets")
root.mkdir(exist_ok=True)
for role in ("admin", "runner", "publisher", "reader", "migrator"):
    target = root / f"db_{role}_password"
    if not target.exists():
        target.write_text(secrets.token_urlsafe(36))
    os.chmod(target, 0o600)
    os.chown(target, 10001, 10001)
for name in ("artifacts", "raw", "demo", "mock"):
    target = Path("/data") / name
    target.mkdir(exist_ok=True)
    os.chown(target, 10001, 10001)
print('{"state":"INITIALIZED"}')
