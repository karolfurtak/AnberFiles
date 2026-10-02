"""Narzędzia instancji Jarvis: kopia plików instancji (bez sekretów),
odświeżanie klonów vaulta i Wykonawcy, przykładowe jednostki systemd,
procedura odtworzenia po awarii. Skrypty sh — tylko na Linuksie."""
import os
import re
import shutil
import subprocess
import sys

import pytest

from conftest import REPO

KOPIA = REPO / 'narzedzia' / 'anberfiles-kopia-plikow'
ODSWIEZ = REPO / 'narzedzia' / 'anberfiles-odswiez'
KONF = REPO / 'konfiguracja'
DOC = REPO / 'docs' / 'odtworzenie-po-awarii.md'

tylko_linux = pytest.mark.skipif(sys.platform == 'win32',
                                 reason='skrypty sh instancji Jarvis — tylko Linux')


def _drzewo_instancji(zr):
    """Atrapa systemu plików Jarvisa w katalogu testowym."""
    pliki = {
        'etc/anberfiles/jarvis.conf': '[serwer]\nlogowanie = formularz\n',
        'etc/anberfiles/haslo.env': 'SERVER_PASS=tajne-haslo\n',
        'etc/systemd/system/anberfiles.service': '[Service]\n',
        'etc/systemd/system/anberfiles-vault.service': '[Service]\n',
        'etc/systemd/system/anberfiles-vault.timer': '[Timer]\n',
        'etc/systemd/system/anberfiles.service.d/override.conf': '[Service]\nNice=5\n',
        'etc/systemd/system/inna-usluga.service': '[Service]\n',
        'etc/cups/printers.conf': '<Printer Biuro>\n</Printer>\n',
        'etc/cups/ppd/Biuro.ppd': '*PPD-Adobe: "4.3"\n',
        'srv/anberfiles/eksport/lektor-ustawienia.conf': 'glos = x\n',
        'srv/anberfiles/eksport/czytaj_tts.py': 'print(1)\n',
        'srv/anberfiles/korzen/lektor/vault/a_lektor.mp3': 'ID3nagranie',
        'usr/local/bin/anberfiles-odswiez': '#!/bin/sh\n',
        'srv/anberfiles/.ssh/config': 'Host github-vault\n',
        'srv/anberfiles/.ssh/id_ed25519': '-----BEGIN OPENSSH PRIVATE KEY-----\n',
        'srv/anberfiles/.ssh/id_ed25519.pub': 'ssh-ed25519 AAAA\n',
        'srv/anberfiles/dane/auth/haslo.json': '{"skrot": "x"}',
        'srv/anberfiles/dane/auth/sekret': 'S' * 32,
    }
    for rel, tresc in pliki.items():
        p = zr / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(tresc, encoding='utf-8')


def _kopia(zr, cel, path=None):
    env = dict(os.environ, ANBERFILES_KOPIA_ZRODLO=str(zr))
    if path is not None:
        env['PATH'] = path
    return subprocess.run(['sh', str(KOPIA), str(cel)], env=env, capture_output=True,
                          text=True, timeout=120)


def _wszystkie(cel):
    return sorted(p.relative_to(cel).as_posix() for p in cel.rglob('*') if p.is_file())


def _sprawdz_kopie(cel):
    pliki = _wszystkie(cel)
    tekst = '\n'.join(pliki)
    for jest in ('etc/anberfiles/jarvis.conf', 'etc/systemd/system/anberfiles.service',
                 'etc/systemd/system/anberfiles-vault.timer',
                 'etc/systemd/system/anberfiles.service.d/override.conf',
                 'etc/cups/printers.conf', 'etc/cups/ppd/Biuro.ppd',
                 'srv/anberfiles/eksport/lektor-ustawienia.conf',
                 'srv/anberfiles/korzen/lektor/vault/a_lektor.mp3',
                 'usr/local/bin/anberfiles-odswiez', 'srv/anberfiles/.ssh/config'):
        assert jest in tekst, (jest, pliki)
    assert 'haslo.env' not in tekst
    assert '/auth/' not in tekst and 'haslo.json' not in tekst and 'sekret' not in tekst
    assert not any(p.endswith('/id_ed25519') for p in pliki)
    assert 'inna-usluga' not in tekst
    assert 'czytaj_tts.py' not in tekst                 # kod jest w GitHubie
    for p in cel.rglob('*'):
        if p.is_file():
            t = p.read_bytes()
            assert b'tajne-haslo' not in t and b'PRIVATE KEY' not in t, p
    assert (cel / '.ostatni').is_file()
    assert any('pakiety' in p for p in pliki)


@tylko_linux
def test_kopia_plikow_instancji_bez_sekretow(tmp_path):
    zr, cel = tmp_path / 'zrodlo', tmp_path / 'kopia'
    _drzewo_instancji(zr)
    r = _kopia(zr, cel)
    assert r.returncode == 0, r.stdout + r.stderr
    _sprawdz_kopie(cel)
    # drugi przebieg: usunięty u źródła plik znika z kopii (lustro)
    (zr / 'etc/cups/ppd/Biuro.ppd').unlink()
    r = _kopia(zr, cel)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not any(p.endswith('Biuro.ppd') for p in _wszystkie(cel))


@tylko_linux
def test_kopia_plikow_bez_rsync(tmp_path):
    """Ścieżka zapasowa cp -a: PATH bez rsync."""
    zr, cel = tmp_path / 'zrodlo', tmp_path / 'kopia'
    _drzewo_instancji(zr)
    binx = tmp_path / 'bin'
    binx.mkdir()
    for prog in ('sh', 'cp', 'mkdir', 'rm', 'mv', 'find', 'date', 'cat', 'printf',
                 'dirname', 'basename', 'grep', 'dpkg-query', 'sed', 'ls', 'chmod',
                 'touch', 'test', 'head', 'tr'):
        w = shutil.which(prog)
        if w:
            (binx / prog).symlink_to(w)
    assert not (binx / 'rsync').exists()
    r = _kopia(zr, cel, path=str(binx))
    assert r.returncode == 0, r.stdout + r.stderr
    _sprawdz_kopie(cel)


@tylko_linux
def test_kopia_plikow_blad_kod_wyjscia(tmp_path):
    zr = tmp_path / 'zrodlo'
    _drzewo_instancji(zr)
    cel = tmp_path / 'plik-zamiast-katalogu'
    cel.write_text('x', encoding='utf-8')
    r = _kopia(zr, cel)
    assert r.returncode != 0
    r = subprocess.run(['sh', str(KOPIA)], capture_output=True, text=True, timeout=30)
    assert r.returncode != 0                                 # brak argumentu


@tylko_linux
@pytest.mark.skipif(shutil.which('git') is None, reason='brak git')
def test_odswiez_klony(tmp_path):
    def git(*a, cwd=None):
        subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@t', *a],
                       cwd=cwd, check=True, capture_output=True)
    baza = tmp_path / 'srv'
    for r in ('vault', 'wykonawca'):
        zrodlo = tmp_path / f'{r}-zrodlo'
        git('init', '-q', str(zrodlo))
        (zrodlo / 'a.txt').write_text('1', encoding='utf-8')
        git('add', '.', cwd=zrodlo)
        git('commit', '-qm', 'pierwszy', cwd=zrodlo)
        git('clone', '-q', str(zrodlo), str(baza / r))
        (zrodlo / 'a.txt').write_text('2', encoding='utf-8')
        git('commit', '-qam', 'drugi', cwd=zrodlo)
    env = dict(os.environ, ANBERFILES_BAZA=str(baza))
    r = subprocess.run(['sh', str(ODSWIEZ)], env=env, capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0, r.stderr
    assert re.search(r'vault: [0-9a-f]{7,} -> [0-9a-f]{7,}', r.stdout), r.stdout
    assert re.search(r'wykonawca: [0-9a-f]{7,} -> [0-9a-f]{7,}', r.stdout), r.stdout
    assert (baza / 'vault' / 'a.txt').read_text(encoding='utf-8') == '2'
    # zepsuty klon → komunikat na stderr i kod 1
    shutil.rmtree(baza / 'wykonawca' / '.git')
    r = subprocess.run(['sh', str(ODSWIEZ)], env=env, capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 1
    assert 'wykonawca' in r.stderr


def test_jednostka_systemd_przyklad():
    t = (KONF / 'anberfiles.service.przyklad').read_text(encoding='utf-8')
    for w in ('User=anberfiles', 'Environment=ANBERFILES_CONF=/etc/anberfiles/jarvis.conf',
              'EnvironmentFile=-/etc/anberfiles/haslo.env',
              'ExecStart=/srv/anberfiles/venv/bin/python /srv/anberfiles/kod/app/server.py',
              'Restart=on-failure', 'RestartSec=5', 'MemoryMax=768M', 'Nice=10',
              'NoNewPrivileges=yes', 'PrivateTmp=yes', 'ProtectSystem=strict',
              'ProtectHome=tmpfs',
              'ReadWritePaths=/srv/anberfiles/dane /srv/anberfiles/korzen/lektor',
              'BindReadOnlyPaths=/srv/anberfiles/vault:/srv/anberfiles/korzen/vault '
              '/srv/anberfiles/wykonawca:/srv/anberfiles/korzen/wykonawca',
              'WantedBy=multi-user.target',
              'After=network-online.target tailscaled.service',
              'Environment=HOME=/srv/anberfiles/dane'):
        assert w in t, w
    v = (KONF / 'anberfiles-vault.service.przyklad').read_text(encoding='utf-8')
    assert 'Type=oneshot' in v and 'User=anberfiles' in v
    assert 'ExecStart=/usr/local/bin/anberfiles-odswiez' in v
    tm = (KONF / 'anberfiles-vault.timer.przyklad').read_text(encoding='utf-8')
    assert 'OnBootSec=2min' in tm and 'OnUnitActiveSec=15min' in tm


def test_procedura_odtworzenia_kompletna():
    t = DOC.read_text(encoding='utf-8')
    for w in ('AnberFiles', 'backup-asystent-ai', 'jarwis-wykonawca',
              'anberfiles-kopia-plikow', 'anberfiles-reset-hasla', 'haslo.json', 'sekret',
              'Ustaw hasło', '<adres-tailscale>'):
        assert w in t, w


IPV4 = re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b')


@pytest.mark.parametrize('plik', [
    'docs/odtworzenie-po-awarii.md', 'konfiguracja/jarvis.conf.przyklad',
    'konfiguracja/anberfiles.service.przyklad',
    'konfiguracja/anberfiles-vault.service.przyklad',
    'konfiguracja/anberfiles-vault.timer.przyklad',
    'narzedzia/anberfiles-kopia-plikow', 'narzedzia/anberfiles-odswiez',
    'narzedzia/anberfiles-reset-hasla'])
def test_bez_adresow_ip_i_hasel(plik):
    t = (REPO / plik).read_text(encoding='utf-8')
    reszta = [a for a in IPV4.findall(t) if a not in ('0.0.0.0', '127.0.0.1')]
    assert reszta == [], reszta
    assert not re.search(r'SERVER_PASS=\S', t)
