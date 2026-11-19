#!/bin/sh
# Runs deploy/install.sh in test mode against a copy of your real relayd.conf (no OpenBSD needed).
#   VENV_PY=/path/to/python-with-flask sh tests/installer/test-install.sh
set -eu
cd "$(dirname "$0")/../.."
FIX=$(pwd)/tests/installer/fixtures; W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
fail=0; ok() { echo "PASS: $*"; }; bad() { echo "FAIL: $*"; fail=1; }
export DOAS_USER=me APPKIT_TEST_PYTHON="${VENV_PY:-python3}"
mkdir -p "$W/up"; cp -R app static deploy "$W/up/"; find "$W/up" -name __pycache__ -prune -exec rm -rf {} +
R="$W/root"; mkdir -p "$R/etc"; cp "$FIX/relayd.pristine.conf" "$R/etc/relayd.conf"; export APPKIT_TEST_ROOT="$R"
A="sh $W/up/deploy/install.sh"
# the data volume: the installer refuses unless it is mounted (marker file) -- first prove it refuses, then "mount" it
$A --yes > "$R/o0" 2>&1 && bad "installer ran without the volume" || { grep -q 'must be mounted' "$R/o0" && [ ! -e "$R/etc/musicshare.conf" ] && ok "refuses to run when the volume is not mounted, and creates nothing" || bad "mount guard"; }
mkdir -p "$R/mnt/data"; touch "$R/mnt/data/.volume-ok"
$A --check > "$R/c" 2>&1; [ ! -e "$R/etc/musicshare.conf" ] && [ ! -d "$R/mnt/data/music-share" ] && ok "--check writes nothing" || bad "--check wrote something"
$A --yes > "$R/o1" 2>&1 || { bad "install failed"; cat "$R/o1"; }
[ -d "$R/mnt/data/music-share/data" ] && [ -d "$R/mnt/data/music-share/backup" ] && [ -d "$R/mnt/data/music-share/restore" ] && ok "data, backup and restore folders created" || bad "folders"
grep -q '^rc_pre() {$' "$R/etc/rc.d/musicshare" && grep -q ' on /mnt/data type ' "$R/etc/rc.d/musicshare" && ok "service script refuses to start unless /mnt/data is mounted and holds .volume-ok" || bad "rc_pre guard"
grep -q 'daemon_flags="-m waitress --listen=127.0.0.1:8101 --threads=4 wsgi:app"' "$R/etc/rc.d/musicshare" && grep -q 'daemon_user="_musicshare"' "$R/etc/rc.d/musicshare" && ok "service script: port 8101, user _musicshare" || bad "rc.d"
grep -q '^MUSICSHARE_BASE_PATH=/music$' "$R/etc/musicshare.conf" && grep -q '^MUSICSHARE_BACKUP_DIR=/mnt/data/music-share/backup$' "$R/etc/musicshare.conf" && grep -q '^MUSICSHARE_DATA_DIR=/mnt/data/music-share/data$' "$R/etc/musicshare.conf" && ok "conf has the base path and the backup folder" || bad "conf"
for need in '<musicshare> { 127.0.0.1 }' 'path "/music" forward to <musicshare>' 'path "/music/\*" forward to <musicshare>' 'forward to <musicshare> port 8101'; do
  [ "$(grep -c "$need" "$R/etc/relayd.conf")" = 1 ] && ok "relayd has exactly one: $need" || bad "relayd: $need"
done
grep -q 'site login is not installed yet' "$R/o1" && [ ! -e "$R/etc/site-auth/keys/musicshare.key" ] && ok "without the login service it holds back and says so" || bad "missing login handling"
"$APPKIT_TEST_PYTHON" app/siteauth/keys.py init --dir "$R/etc/site-auth" --no-chown >/dev/null
$A --yes > "$R/o2" 2>&1 || { bad "second install failed"; cat "$R/o2"; }
grep -qx 'musicshare /music' "$R/etc/site-auth/apps.conf" && [ -f "$R/etc/site-auth/keys/musicshare.key" ] && ok "once the login exists the app registers itself and gets its own key" || bad "registration"
cp "$R/etc/relayd.conf" "$R/r1"; cp "$R/etc/site-auth/keys/musicshare.key" "$R/k1"; $A --yes > "$R/o3" 2>&1
cmp -s "$R/r1" "$R/etc/relayd.conf" && cmp -s "$R/k1" "$R/etc/site-auth/keys/musicshare.key" && ok "a third run changes nothing" || bad "not idempotent"
[ -z "$(find "$W/up" -name __pycache__)" ] && ok "the upload folder stays free of __pycache__" || bad "pycache"
[ "$fail" = 0 ] && echo "ALL PASSED" || { echo "SOME FAILED"; exit 1; }
