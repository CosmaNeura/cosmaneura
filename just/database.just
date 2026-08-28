##############################################
# Database Migrations
#
# Goal is to have similar semantics to rails.
##############################################

# separate task for the db to support db_reset
db_up:
	docker compose up -d --wait postgres

# TODO may need to run `docker rm $(docker ps -aq)` as well
# TODO docker down does not exit 1 if it partially failed
# turn off the database *and* completely remove the data
db_down:
	docker compose down --volumes postgres

# completely destroy the dev and test databases, destroying the containers and rebuilding them
db_reset_hard: db_down db_up db_migrate db_seed

# NOTE migration & seed are intentionally omitted so db_nuke and friends can run
# destroys all data in the dev and test databases, leaves the containers running
db_reset:
	psql $DATABASE_URL -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
	psql $TEST_DATABASE_URL -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

[script]
db_lint:
	set -euo pipefail
	uv run alembic check

	# Only squawk-lint migrations newly added in this branch so historical
	# migrations don't pollute CI annotations forever. Requires git history
	# back to the merge-base with origin/master (workflow uses fetch-depth: 0).
	# TODO there's also a more advanced github integration, but seems a bit cleaner:
	# https://squawkhq.com/docs/github_app
	# TODO remove rule exclusion when https://github.com/sbdchd/squawk/issues/392 is fixed
	if ! git rev-parse --verify origin/master >/dev/null 2>&1; then
		echo "origin/master not available locally — skipping squawk"
		exit 0
	fi
	base=$(git merge-base origin/master HEAD 2>/dev/null || true)
	if [ -z "$base" ]; then
		echo "No common ancestor with origin/master — skipping squawk"
		exit 0
	fi
	new_migrations=$(git diff --name-only --diff-filter=A "$base" HEAD -- 'migrations/versions/*.py' | sort)
	if [ -z "$new_migrations" ]; then
		echo "No new migrations vs origin/master — skipping squawk"
		exit 0
	fi
	prev_rev=$(printf '%s\n' "$new_migrations" | uv run python scripts/migration_lint_base.py)
	if [ -z "$prev_rev" ]; then
		echo "Could not determine the new migration ancestry range" >&2
		exit 1
	fi
	echo "Linting $(echo "$new_migrations" | wc -l | tr -d ' ') new migration(s) from $prev_rev to head"

	zsh scripts/squawk_migration_lint.sh "$prev_rev"

# open the database in the default macos GUI
[macos]
db_open:
	# TablePlus via Setapp is a great option here
	open $DATABASE_URL

# tui to interact with the database
[macos]
db_play:
	uv tool run pgcli $DATABASE_URL

# run migrations on dev and test
db_migrate:
	# if this folder is wiped, you'll get a strange error from alembic
	mkdir -p migrations/versions

	# dev database is created automatically, but test database is not. We need to fail gracefully when the database already exists.
	psql $DATABASE_URL -c "CREATE DATABASE ${TEST_DATABASE_NAME};" || true

	@just _banner_echo "Migrating Database"

	uv run alembic upgrade head

	[ -n "${CI:-}" ] || (just _banner_echo "Migrating Test Database" && {{EXECUTE_IN_TEST}} uv run alembic upgrade head)

# pick a migration to downgrade to
[macos]
db_downgrade:
	alembic_target_id=$(uv run alembic history | fzf --delimiter '[->\s,]+' --bind 'enter:become(echo {2})') && \
		just _banner_echo "Downgrading Dev Database..." && \
		uv run alembic downgrade $alembic_target_id && \
		just _banner_echo "Downgrading Test Database..." && \
		{{EXECUTE_IN_TEST}} uv run alembic downgrade $alembic_target_id

# add seed data to dev and test
db_seed: db_migrate
	@just _banner_echo "Seeding Database"
	uv run python migrations/seed.py

	[ -n "${CI:-}" ] || (just _banner_echo "Seeding Test Database" && {{EXECUTE_IN_TEST}} uv run python migrations/seed.py)

# TODO you can't preview what the migration will look like before naming it?
# generate migration based on the current state of the database
[script]
db_generate_migration migration_name="":
	if [ -z "{{migration_name}}" ]; then
		echo "Enter the migration name (use add/remove/update prefix): "
		read name
	else
		name={{migration_name}}
	fi

	# underscores & alpha chars only
	name=$(echo "$name" | tr ' ' '_' | tr '-' '_' | tr -cd '[:alnum:]_')

	uv run alembic revision --autogenerate -m "$name"

	just _banner_echo "Migration Generated. Run 'just db_migrate' to apply the migration"

# destroy and rebuild the database from the ground up, without mutating migrations
db_destroy: db_reset db_migrate db_seed

# rm migrations and regenerate: only for use in early development
db_nuke:
	# I personally hate having a nearly-greenfield project with a bunch of migrations from DB schema iteration
	# this should only be used *before* you've launched and prod and don't need properly migration support

	# first, wipe all of the existing migrations
	rm -rf migrations/versions/* || true

	just db_reset
	just db_generate_migration "initial_commit"

# enable SQL debugging on the postgres database
[macos]
db_debug:
	docker compose exec postgres \
		psql -U $POSTGRES_USER -c "ALTER SYSTEM SET log_statement = 'all'; SELECT pg_reload_conf();"

[macos]
db_debug_off:
	docker compose exec postgres \
		psql -U $POSTGRES_USER -c "ALTER SYSTEM SET log_statement = 'none'; SELECT pg_reload_conf();"

# dump the production database locally, obviously this is a bad idea most of the time
[macos]
[script]
db_dump_production:
	echo "{{ BLUE }}Enter the op:// reference to the production DB (e.g., op://Dev/prod DB/db-connection-string):{{ NORMAL }}"
	read op_ref

	local dump_file="tmp/$(date +%Y-%m-%d)_production.dump"
	echo "Dumping production database..."
	pg_dump $(op read "$op_ref") -F c -f "$dump_file"

	echo "Created file: $dump_file"
	echo "Example restore: \n{{ BLUE }}pg_restore --no-owner --no-privileges --if-exists --clean -d \$DATABASE_URL $dump_file{{ NORMAL }}"
