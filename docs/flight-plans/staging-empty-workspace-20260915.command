'/usr/bin/env' '-i' 'PATH=/usr/bin:/bin' 'LANG=C' 'LC_ALL=C' '/bin/sh' '-c' 'set -eu
umask 077
ulimit -c 0
refuse() { /usr/bin/printf '"'"'%s\n'"'"' staging_empty_workspace_launch_refused >&2; exit 2; }
[ -t 0 ] && [ -t 1 ] && [ -t 2 ] || refuse
[ "$(/usr/bin/id -u)" = 501 ] && [ "$(/usr/bin/id -ru)" = 501 ] || refuse
[ "$(/usr/bin/uname -s)" = Darwin ] && [ "$(/usr/bin/uname -m)" = arm64 ] || refuse
check_file() {
  [ -f "$1" ] && [ ! -L "$1" ] && [ -r "$1" ] || refuse
  workspace_meta=$(/usr/bin/stat -f "%z:%Lp:%u:%l" "$1") || refuse
  [ "$workspace_meta" = "$3:$4:$5:1" ] || refuse
  workspace_sum=$(/usr/bin/shasum -a 256 -- "$1" </dev/null) || refuse
  [ "$workspace_sum" = "$2  $1" ] || refuse
}
check_file '"'"'/private/tmp/cosmaneura-private-foundation-preflight.J40HZoi7/.trusted-runtime-bin/homebrew/Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/bin/python3.14'"'"' '"'"'e8179bf1ac3710499f164f877a3713bbaa34b5921beb70881f5dcd4c45a17827'"'"' '"'"'52448'"'"' '"'"'500'"'"' '"'"'501'"'"'
check_file '"'"'/private/tmp/cosmaneura-staging-baseline-20260914.adNnmt/human-source-6c69bb96c17d150daf8010bf18ee1add/staging_encrypted_workspace.py'"'"' '"'"'eaa2d745dcb3bad28452d5db3854e96b6a3c9f37da614c7201d5e6faca267960'"'"' '"'"'17340'"'"' '"'"'400'"'"' '"'"'501'"'"'
check_file '"'"'/usr/bin/hdiutil'"'"' '"'"'7b73b38f363c3fa1a338fb876d7bb7c9a6a8a9e5c1ba82fd74d8453a74c6795a'"'"' '"'"'730864'"'"' '"'"'755'"'"' '"'"'0'"'"'
check_file '"'"'/usr/sbin/diskutil'"'"' '"'"'624a8601b97ddfaa4b18dd2854c299e084cd45eb18fc152011c97615c3b9da1f'"'"' '"'"'1937776'"'"' '"'"'755'"'"' '"'"'0'"'"'
[ -t 0 ] && [ -t 1 ] && [ -t 2 ] || refuse
exec '"'"'/usr/bin/env'"'"' '"'"'-i'"'"' '"'"'PATH=/usr/bin:/bin'"'"' '"'"'HOME=/dev/null'"'"' '"'"'LANG=C'"'"' '"'"'LC_ALL=C'"'"' '"'"'__CF_USER_TEXT_ENCODING=0x1F5:0x0:0x0'"'"' '"'"'OPENSSL_CONF=/dev/null'"'"' '"'"'OPENSSL_MODULES=/private/tmp/cosmaneura-private-foundation-preflight.J40HZoi7/.trusted-runtime-bin/homebrew/opt/openssl@3/lib/ossl-modules'"'"' '"'"'DYLD_LIBRARY_PATH=/private/tmp/cosmaneura-private-foundation-preflight.J40HZoi7/.trusted-runtime-bin/homebrew/Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14:/private/tmp/cosmaneura-private-foundation-preflight.J40HZoi7/.trusted-runtime-bin/homebrew/opt/libsodium/lib:/private/tmp/cosmaneura-private-foundation-preflight.J40HZoi7/.trusted-runtime-bin/homebrew/opt/libyaml/lib:/private/tmp/cosmaneura-private-foundation-preflight.J40HZoi7/.trusted-runtime-bin/homebrew/opt/lz4/lib:/private/tmp/cosmaneura-private-foundation-preflight.J40HZoi7/.trusted-runtime-bin/homebrew/opt/mpdecimal/lib:/private/tmp/cosmaneura-private-foundation-preflight.J40HZoi7/.trusted-runtime-bin/homebrew/opt/openssl@3/lib:/private/tmp/cosmaneura-private-foundation-preflight.J40HZoi7/.trusted-runtime-bin/homebrew/opt/readline/lib:/private/tmp/cosmaneura-private-foundation-preflight.J40HZoi7/.trusted-runtime-bin/homebrew/opt/sqlite/lib:/private/tmp/cosmaneura-private-foundation-preflight.J40HZoi7/.trusted-runtime-bin/homebrew/opt/xz/lib:/private/tmp/cosmaneura-private-foundation-preflight.J40HZoi7/.trusted-runtime-bin/homebrew/opt/zstd/lib'"'"' '"'"'/private/tmp/cosmaneura-private-foundation-preflight.J40HZoi7/.trusted-runtime-bin/homebrew/Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/bin/python3.14'"'"' '"'"'-I'"'"' '"'"'-S'"'"' '"'"'-B'"'"' '"'"'/private/tmp/cosmaneura-staging-baseline-20260914.adNnmt/human-source-6c69bb96c17d150daf8010bf18ee1add/staging_encrypted_workspace.py'"'"' '"'"'prepare-empty'"'"' '"'"'--expected-source-sha256'"'"' '"'"'eaa2d745dcb3bad28452d5db3854e96b6a3c9f37da614c7201d5e6faca267960'"'"''
