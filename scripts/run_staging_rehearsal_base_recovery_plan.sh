#!/bin/sh

set -eu

python_path='/opt/homebrew/Cellar/python@3.14/3.14.7/Frameworks/Python.framework/Versions/3.14/bin/python3.14'
python_sha256='87d4df53fd91304be5bac391fb204643c36b7df2023c04a0953bcbc7d4fdf634'

case "$0" in
  /*) ;;
  *)
    echo 'recovery_plan_launcher_refused' >&2
    exit 2
    ;;
esac

validator_path="${0%/*}/validate_staging_rehearsal_base_recovery_plan.py"
if [ ! -f "$validator_path" ] || [ -L "$validator_path" ]; then
  echo 'recovery_plan_launcher_refused' >&2
  exit 2
fi

observed=$(/usr/bin/env -i LC_ALL=C /usr/bin/shasum -a 256 "$python_path") || {
  echo 'recovery_plan_launcher_refused' >&2
  exit 2
}
if [ "$observed" != "$python_sha256  $python_path" ]; then
  echo 'recovery_plan_launcher_refused' >&2
  exit 2
fi

exec /usr/bin/env -i "$python_path" -IS "$validator_path" "$@"
