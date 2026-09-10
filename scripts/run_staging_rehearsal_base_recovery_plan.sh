#!/bin/sh

set -efu
unset IFS CDPATH ENV BASH_ENV PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT

launcher_path='/Users/jw/Desktop/Cosmas/cosmaneura-staging-recovery-live-20260910/scripts/run_staging_rehearsal_base_recovery_plan.sh'
validator_path='/Users/jw/Desktop/Cosmas/cosmaneura-staging-recovery-live-20260910/scripts/validate_staging_rehearsal_base_recovery_plan.py'
manifest_path='/Users/jw/Desktop/Cosmas/cosmaneura-staging-recovery-live-20260910/scripts/staging_rehearsal_base_recovery_python_runtime.manifest'
python_source_root='/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9'
validator_sha256='937bbace7c66cb3a6918b3deb8ef6c9def6e691a1a28ab535c84f9dbc02113e9'
manifest_sha256='31ce8586794b020186db0dd05c8e33ca462084a31d27a38270fe3a9f9e344c86'
python_tree_entry_count='2006'
python_tree_metadata_sha256='b662c5523b9e8b6c3b923640d5bd72bc08c166dd6d7293bbb21d67bda289ecda'
python_tree_content_sha256='a4f26975947b867b232a79e5b1be0c0c7c230fa4fde9723f558f75a67e3b4e92'
python_tree_acl_entry_count='0'
python_tree_acl_sha256='e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
bootstrap_root=''

# shellcheck disable=SC2329 # Invoked by the EXIT trap.
cleanup() {
  trap - 0 1 2 3 15
  case "$bootstrap_root" in
    /private/tmp/cosmaneura-recovery-bootstrap.*)
      if [ -d "$bootstrap_root" ] && [ ! -L "$bootstrap_root" ]; then
        /bin/chmod -RN "$bootstrap_root" >/dev/null 2>&1 || :
        /bin/chmod -R u+w "$bootstrap_root" >/dev/null 2>&1 || :
        /bin/rm -rf -- "$bootstrap_root" >/dev/null 2>&1 || :
      fi
      ;;
  esac
}

refuse() {
  echo 'recovery_plan_launcher_refused' >&2
  exit 2
}

[ "$#" -ge 2 ] || refuse
[ "$1" = '--authenticated-launcher-sha256' ] || refuse
authenticated_launcher_sha256=$2
shift 2
case "$authenticated_launcher_sha256" in
  ''|*[!0-9a-f]*) refuse ;;
esac
[ "${#authenticated_launcher_sha256}" -eq 64 ] || refuse

operator_uid=$(/usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin /usr/bin/id -u) || refuse

require_no_acl() {
  acl_listing=$(
    /usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin /bin/ls -ldne "$1"
  ) || refuse
  printf '%s\n' "$acl_listing" |
    /usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin /usr/bin/awk "
      NR != 1 || NF < 8 || length(\$1) < 10 || length(\$1) > 11 ||
      \$1 !~ /^[-dl][rwxstST-]+@?$/ { bad = 1 }
      END { exit (bad || NR != 1) }
    " || refuse
}

trusted_component() {
  component=$1
  [ ! -L "$component" ] || refuse
  metadata=$(
    /usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin \
      /usr/bin/stat -f '%u:%Lp' "$component"
  ) || refuse
  owner=${metadata%%:*}
  mode=${metadata#*:}
  mode_value=$((0$mode))
  [ "$owner" = 0 ] || refuse
  [ $((mode_value & 022)) -eq 0 ] || refuse
  require_no_acl "$component"
}

snapshot_file() {
  component=$1
  expected_mode=$2
  expected_sha256=$3
  [ -f "$component" ] && [ ! -L "$component" ] || refuse
  metadata=$(
    /usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin \
      /usr/bin/stat -f '%u:%Lp:%l:%z' "$component"
  ) || refuse
  owner=${metadata%%:*}
  remainder=${metadata#*:}
  mode=${remainder%%:*}
  remainder=${remainder#*:}
  links=${remainder%%:*}
  size=${remainder#*:}
  [ "$owner" = "$operator_uid" ] || refuse
  [ "$mode" = "$expected_mode" ] || refuse
  [ "$links" = 1 ] || refuse
  [ "$size" -gt 0 ] || refuse
  require_no_acl "$component"
  observed=$(
    /usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin \
      /usr/bin/shasum -a 256 "$component"
  ) || refuse
  [ "$observed" = "$expected_sha256  $component" ] || refuse
}

# The repository pathname is not execution authority. This launcher must arrive
# through a closed-writer pipe opened by an independently authenticated caller,
# or through a non-writable copy whose caller checked the exact digest first.
case "$0" in
  /dev/fd/[0-9]*) ;;
  *)
    [ "$0" != "$launcher_path" ] || refuse
    [ -f "$0" ] && [ ! -L "$0" ] || refuse
    invocation_metadata=$(
      /usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin \
        /usr/bin/stat -f '%u:%Lp:%l' "$0"
    ) || refuse
    [ "$invocation_metadata" = "$operator_uid:500:1" ] || refuse
    require_no_acl "$0"
    invocation_digest=$(
      /usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin \
        /usr/bin/shasum -a 256 "$0"
    ) || refuse
    [ "$invocation_digest" = "$authenticated_launcher_sha256  $0" ] || refuse
    invocation_parent=${0%/*}
    [ "$invocation_parent" != "$0" ] || refuse
    [ "$(/usr/bin/stat -f '%u:%Lp' "$invocation_parent")" = "$operator_uid:700" ] || refuse
    require_no_acl "$invocation_parent"
    ;;
esac

for component in \
  /Library /Library/Developer /Library/Developer/CommandLineTools \
  /Library/Developer/CommandLineTools/Library \
  /Library/Developer/CommandLineTools/Library/Frameworks \
  /Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework \
  /Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions \
  "$python_source_root" /usr /usr/bin /bin \
  /bin/chmod /bin/cp /bin/ls /bin/mkdir /bin/rm /usr/bin/awk /usr/bin/env \
  /usr/bin/find /usr/bin/id /usr/bin/mktemp /usr/bin/shasum /usr/bin/stat \
  /usr/bin/wc
do
  trusted_component "$component"
done

[ "$(/usr/bin/stat -f '%u:%Mp%Lp' /private/tmp)" = '0:1777' ] || refuse
require_no_acl /private/tmp

umask 077
bootstrap_root=$(
  /usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin \
    /usr/bin/mktemp -d /private/tmp/cosmaneura-recovery-bootstrap.XXXXXXXX
) || refuse
case "$bootstrap_root" in
  /private/tmp/cosmaneura-recovery-bootstrap.*) ;;
  *) refuse ;;
esac
trap 'cleanup' EXIT
trap 'exit 130' 1 2 3 15
[ -d "$bootstrap_root" ] && [ ! -L "$bootstrap_root" ] || refuse
[ "$(/usr/bin/stat -f '%u:%Lp' "$bootstrap_root")" = "$operator_uid:700" ] || refuse
require_no_acl "$bootstrap_root"

source_snapshot="$bootstrap_root/source"
/bin/mkdir -m 0700 "$source_snapshot" || refuse
/bin/cp -X "$validator_path" "$source_snapshot/validator.py" || refuse
/bin/cp -X "$manifest_path" "$source_snapshot/runtime.manifest" || refuse
/bin/chmod -N "$source_snapshot/validator.py" "$source_snapshot/runtime.manifest" || refuse
/bin/chmod 0400 "$source_snapshot/validator.py" "$source_snapshot/runtime.manifest" || refuse
snapshot_file "$source_snapshot/validator.py" 400 "$validator_sha256"
snapshot_file "$source_snapshot/runtime.manifest" 400 "$manifest_sha256"
/bin/chmod 0500 "$source_snapshot" || refuse

python_snapshot="$bootstrap_root/python"
/bin/cp -RX "$python_source_root" "$python_snapshot" || refuse
/bin/chmod -RN "$python_snapshot" || refuse
/bin/chmod -R go-rwx,u-w "$python_snapshot" || refuse

metadata_file="$bootstrap_root/runtime-metadata"
content_file="$bootstrap_root/runtime-content"
acl_file="$bootstrap_root/runtime-acls"
owner_file="$bootstrap_root/runtime-owners"
# On the pinned macOS implementation, find propagates a failed -exec ... {} +.
# Each producer writes directly to a private file and its status is checked
# before parsing; an unreadable ACL can never become a successful empty set.
(
  cd "$python_snapshot" || exit 2
  /usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin /usr/bin/find -s . -xdev \
    -exec /usr/bin/stat -f '%N|%HT|%Mp%Lp|%l|%z|%Y' {} + >"$metadata_file" || exit 2
  /usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin /usr/bin/find -s . -xdev -type f \
    -exec /usr/bin/shasum -a 256 {} + >"$content_file" || exit 2
  /usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin /usr/bin/find -s . -xdev \
    -exec /bin/ls -ldne {} + >"$acl_file" || exit 2
  /usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin /usr/bin/find -s . -xdev \
    ! -user "$operator_uid" -print >"$owner_file" || exit 2
) || refuse
[ ! -s "$owner_file" ] || refuse
/usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin /usr/bin/awk \
  -v expected="$python_tree_entry_count" "
    NF < 8 || length(\$1) < 10 || length(\$1) > 11 ||
    \$1 !~ /^[-dl][rwxstST-]+@?$/ { bad = 1 }
    END { exit (bad || NR != expected) }
  " "$acl_file" || refuse
[ "$python_tree_acl_entry_count" = 0 ] || refuse
empty_acl_digest=$(printf '' | /usr/bin/shasum -a 256) || refuse
[ "${empty_acl_digest%% *}" = "$python_tree_acl_sha256" ] || refuse

observed_metadata=$(
  /usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin \
    /usr/bin/shasum -a 256 "$metadata_file"
) || refuse
observed_metadata=${observed_metadata%% *}
[ "$observed_metadata" = "$python_tree_metadata_sha256" ] || refuse
observed_content=$(
  /usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin \
    /usr/bin/shasum -a 256 "$content_file"
) || refuse
observed_content=${observed_content%% *}
[ "$observed_content" = "$python_tree_content_sha256" ] || refuse
/bin/rm -f -- "$metadata_file" "$content_file" "$acl_file" "$owner_file" || refuse

snapshot_python="$python_snapshot/bin/python3.9"
snapshot_framework="$python_snapshot/Python3"
snapshot_file "$snapshot_python" 500 \
  bdea59019a38eb6600cc9e71e984a97fedadc406448431281e7657030f54987e
snapshot_file "$snapshot_framework" 500 \
  26422f0b21cb1e07236384afa4043c7401d39d09358c91df8bf3f5496f14712f
/bin/chmod 0500 "$bootstrap_root" || refuse

if /usr/bin/env -i "$snapshot_python" -IBS -c '
import hashlib
import os
import stat
import sys

def refuse():
    sys.stderr.write("recovery_plan_launcher_refused\n")
    raise SystemExit(2)

def read_exact(path, expected, expected_mode):
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        linked = os.stat(path, follow_symlinks=False)
        identity = lambda value: (value.st_dev, value.st_ino, value.st_uid, value.st_nlink, stat.S_IMODE(value.st_mode), value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid() or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != expected_mode or not 0 < before.st_size <= 1048576 or identity(before) != identity(linked):
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
        value = b"".join(chunks)
        if identity(before) != identity(after) or identity(after) != identity(linked_after) or hashlib.sha256(value).hexdigest() != expected:
            refuse()
        return value
    finally:
        os.close(descriptor)

try:
    validator_path = sys.argv[1]
    validator_digest = sys.argv[2]
    manifest_path = sys.argv[3]
    manifest_digest = sys.argv[4]
    runtime_root = sys.argv[5]
    launcher_digest = sys.argv[6]
    arguments = sys.argv[7:]
    if os.path.realpath(sys.base_prefix) != runtime_root or os.path.realpath(sys.executable) != os.path.join(runtime_root, "bin/python3.9"):
        refuse()
    source = read_exact(validator_path, validator_digest, 0o400)
    read_exact(manifest_path, manifest_digest, 0o400)
except (IndexError, OSError):
    refuse()

source_name = "/authenticated-memory/staging-rehearsal-base-recovery-validator.py"
sys.argv[:] = [source_name, *arguments]
namespace = {
    "__name__": "__main__",
    "__file__": source_name,
    "__builtins__": __builtins__,
    "_AUTHENTICATED_LAUNCHER_SHA256": launcher_digest,
    "_AUTHENTICATED_PYTHON_ROOT": runtime_root,
    "_AUTHENTICATED_RUNTIME_MANIFEST_PATH": manifest_path,
}
exec(compile(source, source_name, "exec", dont_inherit=True), namespace)
' "$source_snapshot/validator.py" "$validator_sha256" \
  "$source_snapshot/runtime.manifest" "$manifest_sha256" \
  "$python_snapshot" "$authenticated_launcher_sha256" "$@"
then
  launcher_status=0
else
  launcher_status=$?
fi
exit "$launcher_status"
