#!/bin/sh

set -eu
unset IFS CDPATH ENV BASH_ENV PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT

launcher_path='/Users/jw/Desktop/Cosmas/cosmaneura-staging-recovery-live-20260910/scripts/run_staging_rehearsal_base_recovery_plan.sh'
validator_path='/Users/jw/Desktop/Cosmas/cosmaneura-staging-recovery-live-20260910/scripts/validate_staging_rehearsal_base_recovery_plan.py'
manifest_path='/Users/jw/Desktop/Cosmas/cosmaneura-staging-recovery-live-20260910/scripts/staging_rehearsal_base_recovery_python_runtime.manifest'
python_root='/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9'
python_path="$python_root/bin/python3.9"
validator_sha256='551af41787804c3756521cd8d06559fe43c46296fd9bf685b1a60846bb571fdb'
manifest_sha256='4f0f0e22c89afc4a373f40deda9b504ae51082504643fde0767079ea238b41dd'
python_tree_entry_count='2006'
python_tree_metadata_sha256='102e14547e442dd8be9f2d9585982a1819147d3f08cdcab5db9b12b0036100fb'
python_tree_content_sha256='63c163925c96a4f5d09c1209556bbfb2dbe9c2b9727dc1ebca1d4f53f010631b'

refuse() {
  echo 'recovery_plan_launcher_refused' >&2
  exit 2
}

trusted_component() {
  component=$1
  ownership=$2
  [ ! -L "$component" ] || refuse
  metadata=$(/usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin /usr/bin/stat -f '%u:%Lp' "$component") || refuse
  owner=${metadata%%:*}
  mode=${metadata#*:}
  mode_value=$((0$mode))
  [ $((mode_value & 022)) -eq 0 ] || refuse
  case "$ownership:$owner" in
    root:0|operator:0|operator:"$operator_uid") ;;
    *) refuse ;;
  esac
}

[ "$0" = "$launcher_path" ] || refuse
operator_uid=$(/usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin /usr/bin/id -u) || refuse

for component in \
  / /Users /Users/jw /Users/jw/Desktop /Users/jw/Desktop/Cosmas \
  /Users/jw/Desktop/Cosmas/cosmaneura-staging-recovery-live-20260910 \
  /Users/jw/Desktop/Cosmas/cosmaneura-staging-recovery-live-20260910/scripts \
  "$launcher_path" "$validator_path" "$manifest_path"
do
  trusted_component "$component" operator
done

for component in \
  /Library /Library/Developer /Library/Developer/CommandLineTools \
  /Library/Developer/CommandLineTools/Library \
  /Library/Developer/CommandLineTools/Library/Frameworks \
  /Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework \
  /Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions \
  "$python_root" "$python_path" /usr /usr/bin /bin \
  /usr/bin/env /usr/bin/find /usr/bin/id /usr/bin/shasum \
  /usr/bin/stat /usr/bin/wc
do
  trusted_component "$component" root
done

observed_manifest=$(/usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin /usr/bin/shasum -a 256 "$manifest_path") || refuse
[ "$observed_manifest" = "$manifest_sha256  $manifest_path" ] || refuse

observed_count=$(
  /usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin \
    /usr/bin/find -s "$python_root" -xdev -print |
    /usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin /usr/bin/wc -l
) || refuse
observed_count=${observed_count##* }
[ "$observed_count" = "$python_tree_entry_count" ] || refuse

observed_metadata=$(
  /usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin \
    /usr/bin/find -s "$python_root" -xdev \
      -exec /usr/bin/stat -f '%N|%HT|%u|%g|%Mp%Lp|%z|%Y' {} + |
    /usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin /usr/bin/shasum -a 256
) || refuse
observed_metadata=${observed_metadata%% *}
[ "$observed_metadata" = "$python_tree_metadata_sha256" ] || refuse

observed_content=$(
  /usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin \
    /usr/bin/find -s "$python_root" -xdev -type f \
      -exec /usr/bin/shasum -a 256 {} + |
    /usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin /usr/bin/shasum -a 256
) || refuse
observed_content=${observed_content%% *}
[ "$observed_content" = "$python_tree_content_sha256" ] || refuse

exec /usr/bin/env -i "$python_path" -IS -c '
import hashlib
import os
import stat
import sys

def refuse():
    sys.stderr.write("recovery_plan_launcher_refused\n")
    raise SystemExit(2)

try:
    path = sys.argv[1]
    expected = sys.argv[2]
    arguments = sys.argv[3:]
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        linked = os.stat(path, follow_symlinks=False)
        identity = lambda value: (value.st_dev, value.st_ino, value.st_uid, value.st_nlink, stat.S_IMODE(value.st_mode), value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o644 or not 0 < before.st_size <= 1048576 or identity(before) != identity(linked):
            refuse()
        remaining = before.st_size
        chunks = []
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                refuse()
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            refuse()
        after = os.fstat(descriptor)
        linked_after = os.stat(path, follow_symlinks=False)
        source = b"".join(chunks)
        if identity(before) != identity(after) or identity(after) != identity(linked_after) or hashlib.sha256(source).hexdigest() != expected:
            refuse()
    finally:
        os.close(descriptor)
except (IndexError, OSError):
    refuse()

source_name = "/authenticated-memory/staging-rehearsal-base-recovery-validator.py"
sys.argv[:] = [source_name, *arguments]
namespace = {"__name__": "__main__", "__file__": source_name, "__builtins__": __builtins__}
exec(compile(source, source_name, "exec", dont_inherit=True), namespace)
' "$validator_path" "$validator_sha256" "$@"
