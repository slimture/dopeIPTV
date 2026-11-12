#!/usr/bin/env python3
"""admin_users.py - the admin page's admins, from the server's shell.

For when no one can sign in any more (every passkey lost, a password
forgotten) or for a first admin without the old password. Run as the
service's user, in the jail:

    su -m iptv -c 'python3 -I /usr/local/www/iptv/donate/admin_users.py list'
    ... admin_users.py invite pontus "Pontus"   a one-time link, valid 7 days
    ... admin_users.py remove pontus            then invite again to start over
    ... admin_users.py signout pontus           ends that admin's sessions

The page's own Account tab does all of this for a signed-in admin.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import donate  # noqa: E402 - the shared settings and the session key


def main(argv: list) -> int:
    auth = donate._auth()
    cmd, args = (argv[1] if len(argv) > 1 else "list"), argv[2:]
    try:
        if cmd == "list":
            data = auth._load()
            for user, rec in sorted(data["users"].items()):
                ways = f"{len(rec.get('passkeys', []))} passkeys" + (", password" if rec.get("password") else "") + \
                       (", authenticator" if rec.get("totp") else "")
                print(f"{user:16} {rec.get('name', ''):24} {ways}")
            for inv in auth.account_invites():
                print(f"{inv['user']:16} {inv.get('name', ''):24} invited, until {time.ctime(inv['until'])}")
            if not data["users"] and not auth.account_invites():
                print("No admins yet.")
        elif cmd == "invite" and args:
            token, until = auth.invite("server", args[0], " ".join(args[1:]))
            print(f"{donate.ORIGIN}/admin/login?invite={token}")
            print(f"Works once, until {time.ctime(until)}. Open it on the device that will hold the passkey.")
        elif cmd == "remove" and args:
            data = auth._load()
            if args[0] not in data["users"]:
                raise auth.AuthError("No such admin.")
            del data["users"][args[0]]
            auth._save(data)
            print(f"Removed {args[0]}.")
        elif cmd == "signout" and args:
            auth.sign_out_everywhere(args[0])
            print(f"{args[0]} is signed out everywhere.")
        else:
            print(__doc__.strip())
            return 2
    except (auth.AuthError, KeyError) as e:
        print(f"Refused: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
