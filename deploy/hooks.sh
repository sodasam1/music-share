# music-share's own part of the installer, sourced by appkit.sh: when this version replaces one that was served at /music, offer to
# change the old setting in /etc/musicshare.conf (the installer never rewrites an existing conf file on its own).
app_repair_conf() {
  if grep -q '^MUSICSHARE_BASE_PATH=/music$' "$CONF"; then
    say "   $CONF still says MUSICSHARE_BASE_PATH=/music, but music-share is now served at /music-share."
    if [ "$REPAIR" = 1 ] && ask "Change it to /music-share? (a copy is kept as $CONF.pre-urlchange)"; then
      [ -f "$CONF.pre-urlchange" ] || cp -p "$CONF" "$CONF.pre-urlchange"
      sed 's#^MUSICSHARE_BASE_PATH=/music$#MUSICSHARE_BASE_PATH=/music-share#' "$CONF" > "$TMP"
      cat "$TMP" > "$CONF"                                          # written in place, so owner and mode stay as they were
      chgd "$CONF (base path is now /music-share)"; CHANGED=1
    else held "base path left as /music (use --repair to change it); the app would still answer at /music, not /music-share"; fi
  fi
}
