"""Read one secret from the macOS login keychain.

Convention (rickarm/scripts docs/1password-project-secrets.md): a variable cached
from a 1Password Environment is stored with service = the Environment name and
account = the variable name, loaded by ~/scripts/op-keychain-load.sh. This job
runs every 30 minutes, too often to call `op` per run, so it reads the cache.
"""

import subprocess


def keychain_secret(service: str, account: str) -> str:
    """Return the stored value, or "" when the item is absent or the keychain is locked."""
    result = subprocess.run(
        ["/usr/bin/security", "find-generic-password", "-s", service, "-a", account, "-w"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()
