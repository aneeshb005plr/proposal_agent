# sdk_inspect_6.py — verify the managed-identity migration briefing's
# claims directly, rather than trust a secondhand transcription.
# Specifically checking: the real _create_client_application source
# (or whatever the real method is called), whether TENANT_ID is
# referenced ANYWHERE else in the auth chain (not just this one
# method), and confirming ManagedIdentityClient's real constructor.

import inspect

from microsoft_agents.authentication.msal import MsalAuth

print(f"\n{'=' * 70}\nMsalAuth — all methods referencing 'client' or 'token'\n{'=' * 70}")
for name in dir(MsalAuth):
    if not name.startswith("__") and ("client" in name.lower() or "token" in name.lower()):
        print(f"  {name}")

# Try the most likely real method name forTEAMS_APP_PASSWORD  building the MSAL/managed-
# identity client — adjust if this doesn't exist under this exact name.
for candidate in ["_get_client", "_create_client_application", "_build_client"]:
    if hasattr(MsalAuth, candidate):
        print(f"\n{'=' * 70}\nMsalAuth.{candidate} — full source\n{'=' * 70}")
        try:
            print(inspect.getsource(getattr(MsalAuth, candidate)))
        except (TypeError, OSError) as e:
            print(f"(source not available: {e})")

# Confirm whether TENANT_ID is referenced ANYWHERE in MsalAuth's
# full source, not just the one method the briefing showed.
print(f"\n{'=' * 70}\nFull MsalAuth source — grep for TENANT_ID references\n{'=' * 70}")
try:
    full_source = inspect.getsource(MsalAuth)
    for i, line in enumerate(full_source.splitlines()):
        if "TENANT_ID" in line or "tenant_id" in line:
            print(f"  line {i}: {line.strip()}")
except (TypeError, OSError) as e:
    print(f"(source not available: {e})")

# Confirm ManagedIdentityClient's real constructor and what it needs.
try:
    from azure.identity import ManagedIdentityCredential  # or wherever it actually lives
    print(f"\n{'=' * 70}\nManagedIdentityCredential.__init__\n{'=' * 70}")
    print(inspect.signature(ManagedIdentityCredential.__init__))
except ImportError as e:
    print(f"\nManagedIdentityCredential import (azure.identity) failed: {e}")
    # Try the msal-specific one the briefing referenced instead
    try:
        import msal
        print(dir(msal))
    except ImportError:
        pass

print("\n\nDONE — paste everything above back.")