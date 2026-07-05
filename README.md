# dopeIPTV

En elegant IPTV-klient för Linux i dopeIPTV-stil (macOS-inspirerat mörkt gränssnitt) med stöd för **Xtream Codes API**, **EPG** och uppspelning via **mpv** eller **VLC**.

## Funktioner

- Logga in med Xtream Codes (server, användarnamn, lösenord) — uppgifterna sparas
- Live-TV, Filmer (VOD) och Serier med säsonger och avsnitt
- Kategorier i sidopanelen och snabb sökning
- EPG: "Nu spelas" med förloppsindikator + kommande program för vald kanal
- Kanallogotyper som laddas asynkront
- Spela i mpv eller VLC (dubbelklick, knappar eller högerklicksmeny)
- Kopiera ström-URL via högerklick
- Val av standardspelare och live-format (ts / m3u8) i Inställningar

## Installation

```bash
# Beroenden
sudo apt install python3 python3-pip mpv vlc      # Debian/Ubuntu
# eller: sudo dnf install python3 mpv vlc          # Fedora
# eller: sudo pacman -S python mpv vlc             # Arch

pip install PyQt6 requests
```

## Starta

```bash
python3 dopeiptv.py
```

Första gången anger du din Xtream-server (t.ex. `http://server:8080`), användarnamn och lösenord.

## Lägg till i programmenyn (valfritt)

```bash
mkdir -p ~/.local/share/applications ~/.local/bin
cp dopeiptv.py ~/.local/bin/dopeiptv.py
cp swiptv.desktop ~/.local/share/applications/
```

## Användning

| Åtgärd | Så här |
|---|---|
| Spela | Dubbelklicka, eller knapparna "Spela i mpv / VLC" |
| Byt spelare tillfälligt | Högerklicka på en rad |
| Öppna en serie | Dubbelklicka på serien → avsnittslista visas |
| Sök | Skriv i sökfältet överst |
| Byt konto | Inställningar → "Byt konto / server" |

## Felsökning

- **"Spelare saknas"** — installera mpv eller VLC (se ovan).
- **Live-strömmen startar inte** — prova att byta live-format till `m3u8` i Inställningar.
- **Inga kategorier** — kontrollera server-URL:en (inkludera port, t.ex. `:8080`).
