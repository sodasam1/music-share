#!/bin/sh
# doas sh ~/music-share/deploy/install.sh --check   (then without --check). Needs the login installed first: personal-site RESTORE.html B2.
. "$(dirname "$0")/appkit.sh"
appkit_main "$@"
