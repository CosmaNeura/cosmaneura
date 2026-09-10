#!/bin/sh

set -eu
unset IFS CDPATH ENV BASH_ENV PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONINSPECT

launcher_path='/Users/jw/Desktop/Cosmas/cosmaneura-staging-recovery-live-20260910/scripts/run_staging_rehearsal_base_recovery_plan.sh'
validator_path='/Users/jw/Desktop/Cosmas/cosmaneura-staging-recovery-live-20260910/scripts/validate_staging_rehearsal_base_recovery_plan.py'
manifest_path='/Users/jw/Desktop/Cosmas/cosmaneura-staging-recovery-live-20260910/scripts/staging_rehearsal_base_recovery_python_runtime.manifest'
python_root='/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9'
python_path="$python_root/bin/python3.9"
validator_sha256='636ca3669e676a66e3c5559599a211b9ac4f3e0e8c63716120864757df08f18c'
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
  /usr/bin/env /usr/bin/find /usr/bin/id /usr/bin/mktemp /usr/bin/shasum \
  /usr/bin/stat /usr/bin/wc /bin/chmod /bin/cp /bin/rm /bin/rmdir
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

umask 077
snapshot_directory=$(
  /usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin \
    /usr/bin/mktemp -d /private/tmp/cosmaneura-recovery-validator.XXXXXX
) || refuse
trap '/bin/rm -rf "$snapshot_directory"' EXIT HUP INT TERM
snapshot_path="$snapshot_directory/validator.py"
/bin/cp "$validator_path" "$snapshot_path" || refuse
/bin/chmod 0400 "$snapshot_path" || refuse
exec 3< "$snapshot_path"
exec 4< "$snapshot_path"
/bin/rm "$snapshot_path" || refuse

descriptor_three=$(/usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin /usr/bin/stat -f '%i:%z:%m:%c:%u' /dev/fd/3) || refuse
descriptor_four=$(/usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin /usr/bin/stat -f '%i:%z:%m:%c:%u' /dev/fd/4) || refuse
[ "$descriptor_three" = "$descriptor_four" ] || refuse
observed_validator=$(/usr/bin/env -i LC_ALL=C PATH=/usr/bin:/bin /usr/bin/shasum -a 256 /dev/fd/4) || refuse
observed_validator=${observed_validator%% *}
[ "$observed_validator" = "$validator_sha256" ] || refuse
exec 4<&-
/bin/rmdir "$snapshot_directory" || refuse
trap - EXIT HUP INT TERM

exec /usr/bin/env -i "$python_path" -IS -c 'import sys;sys.argv[0]="/dev/fd/3";source=open(3,"rb",closefd=False).read();namespace={"__name__":"__main__","__file__":"/dev/fd/3","__builtins__":__builtins__};exec(compile(source,"/dev/fd/3","exec",dont_inherit=True),namespace)' "$@"
