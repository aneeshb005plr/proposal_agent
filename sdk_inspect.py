# sdk_inspect_3.py — last remaining piece: how to actually get a
# bearer token from MsalConnectionManager, now that get_access_token
# is confirmed NOT to exist. get_token_provider/get_connection are
# the real candidates — need their signatures and what they return.

import inspect


def show(label, obj):
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
    try:
        print(inspect.signature(obj))
    except (TypeError, ValueError) as e:
        print(f"(no signature available: {e})")
    doc = inspect.getdoc(obj)
    if doc:
        print(f"\n--- docstring ---\n{doc[:800]}")


from microsoft_agents.authentication.msal import MsalConnectionManager

show("MsalConnectionManager.get_token_provider", MsalConnectionManager.get_token_provider)
show("MsalConnectionManager.get_connection", MsalConnectionManager.get_connection)
show("MsalConnectionManager.get_default_connection", MsalConnectionManager.get_default_connection)

try:
    from microsoft_agents.hosting.core import AccessTokenProviderBase
    show("AccessTokenProviderBase", AccessTokenProviderBase)
    print("\nAccessTokenProviderBase public methods:")
    for name in dir(AccessTokenProviderBase):
        if not name.startswith("_"):
            print(f"  {name}")
            method = getattr(AccessTokenProviderBase, name, None)
            if callable(method):
                try:
                    print(f"    {inspect.signature(method)}")
                except (TypeError, ValueError):
                    pass
except ImportError as e:
    print(f"AccessTokenProviderBase import failed: {e}")

print("\n\nDONE — paste everything above back.")