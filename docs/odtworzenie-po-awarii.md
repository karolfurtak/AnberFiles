# Odtworzenie instancji AnberFiles (Jarvis) po awarii

Cel: postawić AnberFiles na czystym systemie (Raspberry Pi OS 64-bit) tak, by
po otwarciu `http://<adres-tailscale>:8790/` pokazał się ekran **„Ustaw hasło"**,
a po jego ustawieniu — vault, katalog Wykonawcy i nagrania lektora.

Symbole (repozytorium jest publiczne — żadnych adresów ani haseł):
`<adres-tailscale>` — adres urządzenia w sieci Tailscale; `<kopia>` — katalog
z kopią plików instancji (wynik `anberfiles-kopia-plikow`).

## 1. Skąd co się bierze

| Element | Źródło | Uwagi |
|---|---|---|
| Kod AnberFiles | GitHub `karolfurtak/AnberFiles` | `git clone` |
| Vault (pokazywany tylko do odczytu) | GitHub `karolfurtak/backup-asystent-ai` | nowy klucz wdrożeniowy tylko do odczytu |
| Katalog Wykonawcy | GitHub `karolfurtak/jarwis-wykonawca` | nowy klucz wdrożeniowy tylko do odczytu |
| `/etc/anberfiles/` (bez `haslo.env`) | kopia plików instancji | ustawienia instancji `jarvis.conf` |
| `/etc/systemd/system/anberfiles*` (jednostki, timer, katalogi `*.d`) | kopia plików instancji | wzorce też w `konfiguracja/*.przyklad` |
| `/etc/cups/printers.conf`, `/etc/cups/ppd/` | kopia plików instancji | drukarki |
| `/srv/anberfiles/eksport/lektor-ustawienia.conf` | kopia plików instancji | ustawienia lektora |
| `/srv/anberfiles/korzen/lektor/` | kopia plików instancji | nagrania lektora (jedyna treść tworzona na Jarvisie) |
| `/usr/local/bin/anberfiles-odswiez` | kopia plików instancji | także `narzedzia/` w repozytorium |
| `/etc/systemd/system/jarvis-backup-obraz-karty.service.d/anberfiles.conf` | **nie jest w kopii plików** (patrz punkt 4) | odtworzenie jednym poleceniem |
| `/srv/anberfiles/.ssh/config` | kopia plików instancji | aliasy hostów kluczy wdrożeniowych |
| Lista pakietów z wersjami | kopia plików instancji (`pakiety.txt`) | do porównania po instalacji |

### Czego w kopii NIE ma — i dlaczego

| Plik | Powód | Skutek po odtworzeniu |
|---|---|---|
| `dane/auth/haslo.json` (skrót hasła) | kopia nie może otwierać dostępu do serwera | ekran „Ustaw hasło" przy pierwszym wejściu |
| `dane/auth/sekret` (klucz podpisu ciasteczek) | jak wyżej | każde urządzenie loguje się od nowa |
| `/etc/anberfiles/haslo.env` | hasło trybu `basic` | w trybie `formularz` niepotrzebny |
| klucze prywatne `.ssh/id_*` | klucz w kopii = klucz poza kontrolą | nowe klucze wdrożeniowe (krok 9); stare usunąć w GitHubie |
| reszta `dane/` (rejestr zdarzeń, kolejka lektora, pamięć podręczna) | odtwarzalne albo ulotne | rejestr zaczyna się od zera |

## 2. Kroki (jedno polecenie na krok)

Czasy zmierzone przy pierwszej instalacji na Jarvisie (Raspberry Pi 5, łącze domowe).
Łączny czas bez kroków wymagających człowieka (klucze wdrożeniowe, Tailscale,
hasło): ok. 8 min; z tymi krokami zwykle poniżej 30 min.

1. **Pakiety systemowe** (zmierzone: ok. 2,5 min, w tym `apt-get update`):
   `sudo apt-get update && sudo apt-get install -y --no-install-recommends git rsync python3-venv mpg123 flac cups cups-client cups-filters avahi-utils libreoffice-writer-nogui fonts-liberation2 fonts-dejavu-core`
   Wersje porównać z `<kopia>/pakiety.txt`.
2. **Tailscale** (ok. 5 min, logowanie w przeglądarce):
   `curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`
3. **Użytkownik usługi** (1 min):
   `sudo useradd --system --home-dir /srv/anberfiles --create-home --shell /usr/sbin/nologin anberfiles`
4. **Katalogi** (1 min; `korzen/vault` i `korzen/wykonawca` to puste punkty montowania):
   `sudo -u anberfiles mkdir -p /srv/anberfiles/dane /srv/anberfiles/eksport /srv/anberfiles/korzen/lektor /srv/anberfiles/korzen/vault /srv/anberfiles/korzen/wykonawca /srv/anberfiles/.ssh`
5. **Kod** (1 min):
   `sudo -u anberfiles git clone https://github.com/karolfurtak/AnberFiles.git /srv/anberfiles/kod`
6. **Środowisko Pythona** (zmierzone: ok. 1 min, 7 bibliotek):
   `sudo -u anberfiles python3 -m venv /srv/anberfiles/venv && sudo -u anberfiles /srv/anberfiles/venv/bin/pip install -r /srv/anberfiles/kod/requirements.txt`
7. **Zatrzymanie CUPS przed wgraniem drukarek** (1 min):
   `sudo systemctl stop cups`
8. **Pliki instancji z kopii** (zmierzone: 2 s przy 272 kB; więcej przy dużej liczbie nagrań):
   `sudo rsync -a <kopia>/pliki/ /`
   Potem właściciel (numery użytkowników na nowym systemie bywają inne):
   `sudo chown -R anberfiles:anberfiles /srv/anberfiles/eksport /srv/anberfiles/korzen/lektor /srv/anberfiles/.ssh`
   Bez kopii: `jarvis.conf` i jednostki z `kod/konfiguracja/*.przyklad` (zdjąć `.przyklad`).
9. **Nowe klucze wdrożeniowe** (ok. 5 min; po jednym na repozytorium, nazwy zgodne
   z `IdentityFile` w `.ssh/config`):
   `sudo -u anberfiles ssh-keygen -t ed25519 -N '' -C anberfiles-vault -f /srv/anberfiles/.ssh/vault`
   `sudo -u anberfiles ssh-keygen -t ed25519 -N '' -C anberfiles-wykonawca -f /srv/anberfiles/.ssh/wykonawca`
   Klucze publiczne dodać w GitHubie jako *Deploy keys* **bez** prawa zapisu
   (z komputera z `gh`; brak `-w` = tylko do odczytu):
   `gh repo deploy-key add vault.pub -R karolfurtak/backup-asystent-ai -t jarvis-anberfiles`
   `gh repo deploy-key add wykonawca.pub -R karolfurtak/jarwis-wykonawca -t jarvis-anberfiles`
   Stare klucze wdrożeniowe utraconego urządzenia usunąć tam samo (`gh repo deploy-key list`/`delete`).
   Bez kopii `.ssh/config` — wpisy (alias → klucz):
   `Host github-vault` / `HostName github.com` / `User git` / `IdentityFile /srv/anberfiles/.ssh/vault`
   i analogicznie `github-wykonawca`.
10. **Klony vaulta i Wykonawcy** (zmierzone: ok. 25 s łącznie, vault 270 MB):
    `sudo -u anberfiles git clone git@github-vault:karolfurtak/backup-asystent-ai.git /srv/anberfiles/vault`
    `sudo -u anberfiles git clone git@github-wykonawca:karolfurtak/jarwis-wykonawca.git /srv/anberfiles/wykonawca`
11. **Lektor — program syntezy** (1 min):
    `sudo -u anberfiles cp /srv/anberfiles/kod/tools/czytaj_tts.py /srv/anberfiles/eksport/`
12. **Polecenia** (1 min; kopie należące do roota). Odświeżanie klonów
    woła jednostka systemd, więc leży w `/usr/local/bin`; polecenia administracyjne
    (reset hasła, kopia plików) w `/usr/local/sbin`. Root wykonuje te skrypty
    (sudo, cotygodniowa kopia), więc nie mogą być dowiązaniami do katalogu
    użytkownika usługi, a kod i środowisko muszą należeć do roota:
    `sudo install -o root -g root -m 0755 /srv/anberfiles/kod/narzedzia/anberfiles-odswiez /usr/local/bin/`
    `sudo install -o root -g root -m 0755 /srv/anberfiles/kod/narzedzia/anberfiles-reset-hasla /srv/anberfiles/kod/narzedzia/anberfiles-kopia-plikow /usr/local/sbin/`
    `sudo chown -R root:root /srv/anberfiles/kod /srv/anberfiles/venv && sudo chown root:root /srv/anberfiles`
    Oba skrypty roota mają samokontrolę: odmawiają (kod 1), gdy ich plik, katalog
    `app/`, środowisko albo katalog nadrzędny należy do nie-roota lub ma zapis
    grupy/innych. Po każdej aktualizacji kodu (`git pull` jako `anberfiles`)
    powtórz `chown -R root:root` i `install` — inaczej reset hasła odmówi.
    Reset hasła sam przełącza się na `/srv/anberfiles/venv/bin/python`, bo
    systemowy Python nie ma biblioteki `aiohttp` (inna ścieżka środowiska: zmienna
    `ANBERFILES_VENV`).
13. **CUPS z powrotem** (1 min):
    `sudo systemctl start cups`
14. **Usługi** (1 min):
    `sudo systemctl daemon-reload && sudo systemctl enable --now anberfiles anberfiles-vault.timer`
15. **Sprawdzenie startu** (1 min; oczekiwane „logowanie formularzem — hasło NIEUSTAWIONE"):
    `journalctl -u anberfiles -n 20 --no-pager`
16. **Sprawdzenie autostartu** (1 min; obie odpowiedzi `enabled`):
    `systemctl is-enabled anberfiles anberfiles-vault.timer`
17. **Hasło** (1 min; port 8790 jest dostępny tylko przez Tailscale — zapora `ufw`
    domyślnie odrzuca ruch przychodzący z sieci lokalnej i nie ma reguły dla 8790;
    reguły `ufw allow 8790` NIE dodawać): z urządzenia w Tailscale otworzyć `http://<adres-tailscale>:8790/`,
    ekran „Ustaw hasło" (co najmniej 10 znaków). Ekran działa wyłącznie z sieci lokalnej
    i Tailscale; po ustawieniu znika na stałe.
18. **Dowód** (3 min): `sudo reboot`, po starcie ponownie krok 15 i wejście z zapamiętanego
    urządzenia bez hasła.

## 3. Hasło zapomniane albo urządzenie utracone

`sudo anberfiles-reset-hasla` — usuwa skrót hasła i wymienia sekret ciasteczek
(wszystkie urządzenia wylogowane). Restart usługi niepotrzebny. Potem krok 17.
Resetu przez stronę WWW nie ma.

Odtworzenie z **obrazu całej karty systemowej** przywraca też `dane/auth/`
(skrót hasła scrypt i sekret), czyli STARE hasło i zapamiętane urządzenia.
Odtworzenie z **kopii plików instancji** tego nie robi — wtedy ekran „Ustaw hasło".

## 4. Kopia plików instancji (przed awarią)

`sudo anberfiles-kopia-plikow <kopia>` — lustro wymienionych w punkcie 1 plików
w `<kopia>/pliki/`, lista pakietów w `<kopia>/pakiety.txt`, data w `<kopia>/.ostatni`.
Skrypt przerywa z kodem 1, jeśli w kopii znalazłby się skrót hasła, sekret,
`haslo.env` albo klucz prywatny. `<kopia>` musi leżeć poza Jarvisem
(inny dysk albo urządzenie), inaczej awaria zabierze ją razem z instancją.

Kopia wykonuje się automatycznie przy cotygodniowym obrazie karty systemowej:
drop-in `/etc/systemd/system/jarvis-backup-obraz-karty.service.d/anberfiles.conf`
z linią `ExecStartPre=-+/usr/local/sbin/anberfiles-kopia-plikow /srv/kopie/obrazy-systemow/anberfiles`
zapisuje ją na osobny dysk kopii (`/srv/kopie`, migawki dzienne). Sam drop-in
nie jest w kopii plików — odtworzenie jednym poleceniem:
`sudo mkdir -p /etc/systemd/system/jarvis-backup-obraz-karty.service.d && printf '[Service]
ExecStartPre=-+/usr/local/sbin/anberfiles-kopia-plikow /srv/kopie/obrazy-systemow/anberfiles
' | sudo tee /etc/systemd/system/jarvis-backup-obraz-karty.service.d/anberfiles.conf && sudo systemctl daemon-reload`

## 5. Uwagi o ustawieniach systemu

- **Limit pamięci jest dziś deklaracją.** Jądro Raspberry Pi OS startuje z
  `cgroup_disable=memory`, więc `MemoryMax=768M` w jednostce nie jest egzekwowany,
  dopóki w `/boot/firmware/cmdline.txt` nie ma `cgroup_enable=memory` (decyzja
  właściciela, wymaga restartu).
- **Lektor — drugi głos Piper:** wybierany w `lektor-ustawienia.conf`
  (`silnik = piper`, usługa lokalna na porcie 8123).
