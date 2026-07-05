# sdk_inspect_5.py — last check: does MsalConnectionManager.__init__
# actually consult anonymous_allowed when deciding what to build for
# a connection, or does it always build a real MsalAuth regardless?

import inspect

from microsoft_agents.authentication.msal import MsalConnectionManager

print(f"\n{'=' * 70}\nMsalConnectionManager.__init__ — full source\n{'=' * 70}")
try:
    print(inspect.getsource(MsalConnectionManager.__init__))
except (TypeError, OSError) as e:
    print(f"(source not available: {e})")

# Also check MsalAuth.get_access_token's source directly — does IT
# check anonymous_allowed before calling _get_client(), or does
# _get_client() run unconditionally (matching the traceback, which
# showed resolve_tenant_id() failing before any apparent anonymous
# check)?
try:
    from microsoft_agents.authentication.msal import MsalAuth
    print(f"\n{'=' * 70}\nMsalAuth.get_access_token — full source\n{'=' * 70}")
    print(inspect.getsource(MsalAuth.get_access_token))
except Exception as e:
    print(f"MsalAuth.get_access_token source check failed: {e}")

print("\n\nDONE — paste everything above back.")