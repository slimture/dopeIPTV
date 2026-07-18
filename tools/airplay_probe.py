#!/usr/bin/env python3
"""Phase-0 AirPlay probe for dopeIPTV.

Answers ONE question before any real AirPlay integration is built:
does YOUR Apple TV play YOUR provider's live stream when driven by
pyatv? Everything else in the design (manager, cast dialog, pairing UI)
is known-good engineering - this is the part that must be proven on
real hardware first.

Usage (from the repo root, on a machine on the same network as the TV):

    python3 -m pip install pyatv
    python3 tools/airplay_probe.py                # scan, pick, pair, play
    python3 tools/airplay_probe.py URL            # same but URL given up front

Getting a URL to test: right-click a live channel in dopeIPTV ->
"Copy stream URL". If it ends in .ts, change the extension to .m3u8 -
Xtream serves the same channel as HLS, which is what Apple TV wants.

The first run against a device usually asks you to type a PIN shown on
the TV. Credentials from a successful pairing are stored in
~/.dopeiptv-airplay-probe.json, so the PIN is a one-time thing per
device (the real integration would keep them in QSettings instead).

Interpreting the result:
  - Video appears on the TV            -> Phase 0 is GREEN, build it.
  - Pairing OK but playback fails      -> the provider's HLS is the
    problem (codecs/container); try another channel before concluding.
  - No devices found                   -> same Wi-Fi/VLAN? AirPlay
    allowed on the TV (Settings > AirPlay and HomeKit)?
"""

import asyncio
import json
import os
import sys

CREDS_PATH = os.path.expanduser("~/.dopeiptv-airplay-probe.json")

# Apple's own public HLS sample (bipbop). If THIS plays on the TV, the whole
# AirPlay path works and any failure with a provider URL is about that
# stream's format - run with --demo to test it.
DEMO_URL = ("https://devstreaming-cdn.apple.com/videos/streaming/examples/"
            "img_bipbop_adv_example_ts/master.m3u8")


def load_creds() -> dict:
    try:
        with open(CREDS_PATH) as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_creds(d: dict) -> None:
    try:
        with open(CREDS_PATH, "w") as f:
            json.dump(d, f, indent=2)
    except OSError as e:
        print(f"  (could not save credentials: {e})")


async def main() -> int:
    try:
        import pyatv
        from pyatv.const import Protocol
    except ImportError:
        print("pyatv is not installed. Run:\n\n"
              "    python3 -m pip install pyatv\n")
        return 1

    loop = asyncio.get_running_loop()

    print("Scanning for AirPlay devices (5 s)…")
    confs = await pyatv.scan(loop, timeout=5)
    atvs = [c for c in confs if c.get_service(Protocol.AirPlay) is not None]
    if not atvs:
        print("No AirPlay devices found.\n"
              "  - Is this machine on the same Wi-Fi/VLAN as the Apple TV?\n"
              "  - On the TV: Settings > AirPlay and HomeKit > AirPlay on?\n"
              "  - Some routers isolate wireless clients (AP isolation).")
        return 1

    print("\nFound:")
    for i, c in enumerate(atvs):
        print(f"  [{i}] {c.name}  ({c.address})")
    if len(atvs) == 1:
        conf = atvs[0]
        print(f"\nUsing the only device: {conf.name}")
    else:
        try:
            conf = atvs[int(input("\nPick a device number: ").strip())]
        except (ValueError, IndexError):
            print("Not a valid number.")
            return 1

    creds_store = load_creds()
    creds = creds_store.get(conf.identifier)
    if creds:
        conf.set_credentials(Protocol.AirPlay, creds)
        print("Using stored pairing credentials (no PIN needed).")
    else:
        print(f"\nPairing with {conf.name} - watch the TV for a PIN…")
        pairing = await pyatv.pair(conf, Protocol.AirPlay, loop)
        await pairing.begin()
        try:
            if pairing.device_provides_pin:
                pin = input("PIN shown on the TV: ").strip()
                pairing.pin(int(pin))
            else:
                # Rare inverse mode: WE provide the pin and the TV asks
                # for it. Use a fixed one and tell the user.
                pairing.pin(1234)
                print("If the TV asks for a PIN, enter: 1234")
            await pairing.finish()
        except Exception as e:
            print(f"Pairing failed: {type(e).__name__}: {e}\n"
                  "Trying to connect anyway - some devices allow unpaired "
                  "AirPlay ('Anyone on the Same Network').")
        else:
            if pairing.has_paired and pairing.service.credentials:
                creds_store[conf.identifier] = pairing.service.credentials
                save_creds(creds_store)
                conf.set_credentials(Protocol.AirPlay,
                                     pairing.service.credentials)
                print("Paired. Credentials saved - next run skips the PIN.")
        finally:
            await pairing.close()

    if "--demo" in sys.argv[1:]:
        url = DEMO_URL
        print("\nUsing Apple's public HLS demo stream (bipbop).")
    elif len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        url = input("\nStream URL to play (.m3u8 recommended): ").strip()
    if not url:
        print("No URL - nothing to do.")
        return 1
    if url.split("?")[0].endswith(".ts"):
        print("NOTE: that's a raw MPEG-TS URL. Apple TV often refuses "
              "those - if playback fails, retry with .ts -> .m3u8.")

    print("\nConnecting…")
    atv = await pyatv.connect(conf, loop)
    try:
        print("Asking the Apple TV to play the stream - WATCH THE TV.\n"
              "(play_url blocks while the stream runs; Ctrl+C here stops "
              "the probe, the TV may keep playing a few seconds.)\n")
        try:
            await atv.stream.play_url(url)
            print("play_url returned - stream ended or the TV took over.")
        except pyatv.exceptions.HttpError as e:
            # The play command itself was ACCEPTED (we got as far as the
            # status polling). tvOS answers 500 on /playback-info when it
            # has no playable session - i.e. the TV most likely could not
            # play this particular stream (container/codecs), or briefly
            # during startup. What the TV screen showed is the verdict.
            print(f"Status polling failed: {e}\n\n"
                  "The play command WAS delivered. If the TV shows video "
                  "anyway, this is only a polling quirk (fine - the real "
                  "integration handles it). If the TV shows nothing or "
                  "bounced back to the home screen, the TV could not play "
                  "this stream - try:\n"
                  "  1. python3 tools/airplay_probe.py --demo\n"
                  "     (Apple's own HLS sample - proves the AirPlay path)\n"
                  "  2. the same channel with .m3u8 instead of .ts\n"
                  "  3. a couple of other channels")
    finally:
        atv.close()
    print("\nIf video played on the TV: Phase 0 is GREEN.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()) or 0)
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)
