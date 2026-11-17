#######################
# Production Build
#
# Some of the ENV variables and labels below are pulled from these projects:
#
#   - https://github.com/iloveitaly/github-action-nixpacks/blob/2ad8c4fab7059ede8b6103f17b2ec23f42961fd9/entrypoint.sh
#   - https://devcenter.heroku.com/articles/dyno-metadata
#
#######################

GIT_DIRTY := `if [ -n "$(git status --porcelain)" ]; then echo "-dirty"; fi`
GIT_SHA := `git rev-parse HEAD` + GIT_DIRTY
BUILD_CREATED_AT := `date -u +%FT%TZ`
NIXPACKS_BUILD_METADATA := (
	'-e BUILD_COMMIT="' + GIT_SHA + '" ' +
	'-e BUILD_CREATED_AT="' + BUILD_CREATED_AT + '" '
)

# Resolve 1Password references in the shell command substitutions, then strip
# the 1Password and GitHub credentials before the pinned third-party builder is
# executed. Runtime values are still passed explicitly as build parameters.
# NOTE production secrets are *not* included in the image, they are set on deploy
NIXPACKS_CREDENTIAL_BARRIER := "env -u OP_SERVICE_ACCOUNT_TOKEN -u OP_SOCK -u OP_CONNECT_TOKEN -u GH_TOKEN -u GITHUB_TOKEN -u SENTRY_AUTH_TOKEN -u SENTRY_ORG -u SENTRY_PROJECT OP_CACHE=false"
PYTHON_NIXPACKS_BUILD_CMD := "nixpacks build ." + \
	" --name " + PYTHON_IMAGE_TAG + \
	" " + NIXPACKS_BUILD_METADATA + \
	" --platform=linux/amd64" + \
	" --label org.opencontainers.image.revision='" + GIT_SHA + "'" + \
	" --label org.opencontainers.image.created='" + BUILD_CREATED_AT + "'" + \
	' --label org.opencontainers.image.source="$(just _repo_url)"' + \
	' --label org.opencontainers.image.description="Primary application deployment image"' + \
	' --label build.run_id="$(just _build_id)"'

# Production assets bundle public "secrets" (safe to expose publicly) which are extracted from the environment
# for this reason, we need to emulate the production environment, then build the assets statically.
# Also, we can't just mount /app/build/server with -v since the build process removes the entire /app/build directory.
# Some ENV var are set for us, like NODE_ENV: https://nixpacks.com/docs/providers/node#environment-variables
# The protected container build intentionally disables Sentry release upload.
# Until a proven BuildKit secret path exists, release credentials must never
# enter Docker arguments, image metadata, layers, or a remote cache image.
JAVASCRIPT_NIXPACKS_BUILD_CMD := "nixpacks build " + WEB_DIR + " " + \
	" --name " + JAVASCRIPT_IMAGE_TAG + " " + \
	" " + NIXPACKS_BUILD_METADATA + \
	" --platform=linux/amd64 " + \
	" --env VITE_BUILD_COMMIT=" + GIT_SHA + " " + \
	" --env CI=true " + \
	" --label org.opencontainers.image.description=\"Used for building javascript assets, not for deployment\""

# .env file without any secrets that should exist on all environments
SHARED_ENV_FILE := ".env"

# .env file with production variables, no secrets, for python
PYTHON_PRODUCTION_ENV_FILE := ".env.production.backend"

# .env file with production variables that are safe to share publicly (frontend)
JAVASCRIPT_SECRETS_FILE := env("JAVASCRIPT_SECRETS_FILE", ".env.production.frontend")
JAVASCRIPT_BUILD_ENV := if JAVASCRIPT_SECRETS_FILE == ".env.staging.frontend" { "staging" } else { "production" }

# CI resolves only the allowlisted public frontend values in a dedicated
# credential-bearing step and gives the build the path to that 0600 JSON file.
# Local builds retain the direct, non-caching 1Password resolution path.
@_javascript_build_env_args:
	if [ -n "${JAVASCRIPT_BUILD_ENV_JSON:-}" ]; then \
		[ -f "$JAVASCRIPT_BUILD_ENV_JSON" ] || { \
			echo "Resolved frontend build configuration is unavailable." >&2; \
			exit 1; \
		}; \
		build_env_json=$(cat "$JAVASCRIPT_BUILD_ENV_JSON"); \
	else \
		build_env_json=$(just direnv_export "{{JAVASCRIPT_SECRETS_FILE}}" | \
			jq -ce '{NODE_ENV, OPENAPI_JSON_PATH, VITE_CLERK_PUBLIC_KEY, VITE_INTAKE_CHAT_DOMAIN, VITE_POSTHOG_HOST, VITE_POSTHOG_KEY, VITE_PYTHON_URL, VITE_SENTRY_DSN, VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED, VITE_SOVEREIGN_PROVIDER_URL}'); \
	fi; \
	production_frontend_admitted=false; \
	if [ "{{JAVASCRIPT_BUILD_ENV}}" = "production" ] && \
		[ "$(printf '%s\n' "$build_env_json" | jq -r '.VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED')" = "true" ]; then \
		printf '%s\n' "$build_env_json" | \
			python3 scripts/session_prep_production_frontend_admission.py verify \
				--admission "${JAVASCRIPT_SESSION_PREP_ADMISSION_JSON:-}" \
				--source-sha "$(git rev-parse HEAD)" || { \
				echo "Resolved frontend build configuration has an invalid shape." >&2; \
				exit 1; \
			}; \
		production_frontend_admitted=true; \
	fi; \
	printf '%s\n' "$build_env_json" | \
		jq -je --arg admitted "$production_frontend_admitted" 'if keys == ["NODE_ENV", "OPENAPI_JSON_PATH", "VITE_CLERK_PUBLIC_KEY", "VITE_INTAKE_CHAT_DOMAIN", "VITE_POSTHOG_HOST", "VITE_POSTHOG_KEY", "VITE_PYTHON_URL", "VITE_SENTRY_DSN", "VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED", "VITE_SOVEREIGN_PROVIDER_URL"] and all(.[]; type == "string" and length > 0 and (contains("\u0000") | not)) and (.VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED == "true" or .VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED == "false") and ("{{JAVASCRIPT_BUILD_ENV}}" == "staging" or .VITE_SOVEREIGN_PLATFORM_AUTHORITY_ENABLED == "false" or $admitted == "true") then to_entries[] | "--env\u0000", (.key + "=" + .value), "\u0000" else error("invalid frontend build configuration") end' || { \
			echo "Resolved frontend build configuration has an invalid shape." >&2; \
			exit 1; \
		}

# Emit NUL-delimited argv records. The consumer uses xargs -0, so values are
# never reparsed as shell source even if they contain spaces or metacharacters.
@_shared_build_env_args:
	just direnv_export "{{SHARED_ENV_FILE}}" | \
		jq -je 'if type == "object" and all(keys[]; test("^[A-Z][A-Z0-9_]*$")) and all(.[]; . == null or (type == "string" and (contains("\u0000") | not))) then to_entries[] | select(.value != null) | "--env\u0000", (.key + "=" + .value), "\u0000" else error("invalid shared build configuration") end'

@_python_nixpacks_env_args:
	just _shared_build_env_args
	case "${PYTHON_ENV:-production}" in \
		production | staging) printf '%s\0%s\0' --env "PYTHON_ENV=${PYTHON_ENV:-production}" ;; \
		*) echo "PYTHON_ENV must be production or staging." >&2; exit 1 ;; \
	esac; \
	printf '%s\0%s\0%s\0' --inline-cache --cache-from "$(just _validated_registry_host)/cosmaneura/cosmaneura:latest"

@_javascript_nixpacks_env_args:
	just _shared_build_env_args
	just _javascript_build_env_args

# by default, the py image name is pulled from the project name
PYTHON_IMAGE_NAME := PROJECT_NAME
PYTHON_IMAGE_TAG := PYTHON_IMAGE_NAME + ":" + GIT_SHA

# the js image is not deployed and is only used during build, so we simply add a -javascript suffix
JAVASCRIPT_IMAGE_NAME := PYTHON_IMAGE_NAME + "-javascript"
JAVASCRIPT_IMAGE_TAG := JAVASCRIPT_IMAGE_NAME + ":" + GIT_SHA

@_validated_registry_host:
	registry_host="${CONTAINER_REGISTRY_HOST:-cosmaneuraprodregistry.azurecr.io}"; \
	registry_host="${registry_host#http://}"; \
	registry_host="${registry_host#https://}"; \
	registry_host="${registry_host%/}"; \
	case "$registry_host" in \
		"" | *[!A-Za-z0-9.-]*) echo "Container registry host has an invalid shape." >&2; exit 1 ;; \
	esac; \
	printf '%s' "$registry_host"

[script]
_production_build_assertions:
	# TODO we should abstract out "IS_CI" to some sort of Justfile check :/

	# only run this on CI
	[ ! -z "${CI:-}" ] || exit 0

	# if the workspace is dirty, some configuration is not correct: we want a completely clean build environment
	if [ ! -z "{{GIT_DIRTY}}" ]; then \
			echo "Git workspace is dirty! This should never happen on prod!" >&2; \
			git status; \
			exit 1; \
	fi

	if [ ! -d "{{JINJA_TEMPLATE_DIR}}" ]; then \
		echo "Jinja template directory does not exist! This should never happen on prod" >&2; \
		exit 1; \
	fi

# within nixpacks, this is where the SPA client assets are built
JAVASCRIPT_CONTAINER_BUILD_DIR := "/app/build/" + JAVASCRIPT_BUILD_ENV + "/client"
# outside of nixpacks, within the python application folder, this is where the SPA assets are stored
JAVASCRIPT_PRODUCTION_BUILD_DIR := "public"

# build the javascript assets by creating an image, building assets inside the container, and then copying them to the host
build_javascript: _production_build_assertions
	@just _banner_echo "Building JavaScript Assets in Container..."
	rm -rf "{{JAVASCRIPT_PRODUCTION_BUILD_DIR}}" || true

	@just _javascript_nixpacks_env_args | xargs -0 {{NIXPACKS_CREDENTIAL_BARRIER}} {{JAVASCRIPT_NIXPACKS_BUILD_CMD}}

	@just _banner_echo "Extracting JavaScript Assets from Container..."

	# Cannot extract files out of a image, only a container. We create a tmp container to extract assets.
	docker rm tmp-js-container || true
	docker create --name tmp-js-container {{JAVASCRIPT_IMAGE_TAG}}
	docker cp tmp-js-container:{{JAVASCRIPT_CONTAINER_BUILD_DIR}} "{{JAVASCRIPT_PRODUCTION_BUILD_DIR}}"

# dump nixpacks-generated Dockerfile for manual build and production debugging
build_javascript_dump:
	@just _javascript_nixpacks_env_args | xargs -0 {{NIXPACKS_CREDENTIAL_BARRIER}} {{JAVASCRIPT_NIXPACKS_BUILD_CMD}} --out web

# CI installs the reviewed, checksum-verified Nixpacks release before any
# registry login or secret-bearing build step. Local developers install it
# through their package manager.
_build_requirements:
	@if ! command -v nixpacks > /dev/null 2>&1; then \
		echo "nixpacks is required; CI must run the pinned installer action first." >&2; \
		exit 1; \
	fi

# url of the repo on github for build metadata
@_repo_url:
	if [ -n "${GITHUB_SERVER_URL:-}" ] && [ -n "${GITHUB_REPOSITORY:-}" ]; then \
		printf '%s/%s' "$GITHUB_SERVER_URL" "$GITHUB_REPOSITORY"; \
	elif command -v gh >/dev/null 2>&1; then \
		gh repo view --json url --jq ".url" | tr -d " \n"; \
	else \
		git remote get-url origin | sed -E 's#git@github.com:#https://github.com/#; s#\.git$##' | tr -d " \n"; \
	fi

# unique ID (mostly) to identify where/when this image was built for docker labeling
@_build_id:
	if [ -z "${GITHUB_RUN_ID:-}" ]; then \
		echo "{{ os() }}-$(whoami)"; \
	else \
		echo "$GITHUB_RUN_ID"; \
	fi

# build the docker container using nixpacks
build: _build_requirements _production_build_assertions build_javascript
	@just _banner_echo "Building Python Image..."
	@just _python_nixpacks_env_args | xargs -0 {{NIXPACKS_CREDENTIAL_BARRIER}} {{PYTHON_NIXPACKS_BUILD_CMD}}

build_push: _production_build_assertions
	# Only the deployable Python image is published. The JS builder image stays on
	# the ephemeral hosted runner and is never used as a remote cache artifact.
	@registry_image="$(just _validated_registry_host)/cosmaneura/cosmaneura"; \
		docker tag "{{PYTHON_IMAGE_TAG}}" "${registry_image}:{{GIT_SHA}}"; \
		docker push "${registry_image}:{{GIT_SHA}}"; \
		docker tag "{{PYTHON_IMAGE_TAG}}" "${registry_image}:latest"; \
		docker push "${registry_image}:latest"

# dump json output of the built image, ex: j build_inspect '.Config.Env'
build_inspect *flags:
	docker image inspect --format "{{ '{{ json . }}' }}" "{{PYTHON_IMAGE_TAG}}" | jq -r {{ flags }}

# interactively inspect the layers of the built image
[macos]
build_dive: (_brew_check_and_install "dive")
	dive "{{PYTHON_IMAGE_TAG}}"

# dump nixpacks-generated Dockerfile for manual build and production debugging
build_dump:
	@just _python_nixpacks_env_args | xargs -0 {{NIXPACKS_CREDENTIAL_BARRIER}} {{PYTHON_NIXPACKS_BUILD_CMD}} --out .

# clear out nixpacks and other artifacts specific to production containers
build_clean:
	rm -rf .nixpacks web/.nixpacks || true

# inject a shell where the build fails
build_debug: build_dump
	# note that you *may* run into trouble using the interactive injected shell if you are using an old builder version
	# Force the latest builder: `docker buildx use orbstack`

	# store the modified build command in a variable rather than editing the file
	BUILD_DEBUG_CMD=$(sed 's/docker build/BUILDX_EXPERIMENTAL=1 docker buildx debug --invoke bash build/' .nixpacks/build.sh) && \
		eval "$BUILD_DEBUG_CMD"

	# BUILDX_EXPERIMENTAL=1 docker buildx debug --invoke bash build . -f ./.nixpacks/Dockerfile

# instead of using autogenerated Dockerfile, build from the dumped Dockerfile which can be manually modified for development
build_from-dump:
	.nixpacks/build.sh

# open up a bash shell in the last built container, helpful for debugging production builds
build_shell: build
	docker run -it {{PYTHON_IMAGE_TAG}} bash -l

# open up a *second* shell in the *same* container that's already running
build_shell-exec:
	docker exec -it $(docker ps -q --filter "ancestor={{PYTHON_IMAGE_TAG}}") bash -l

# TODO https://discord.com/channels/1106380155536035840/1318603119990538340/1318603119990538340
# run the container locally, as if it were in production (against production DB, resources, etc)
build_run-as-production procname="api" image_name=PYTHON_IMAGE_TAG:
	# NOTE that resources are limited to a production-like environment, change if your production requirements are different
	docker run -p 8202:80 \
		--memory=1g --cpus=2 \
		$(just direnv_export_docker ".env.production.backend" --params) \
		--platform linux/amd64 \
		"{{image_name}}" \
		"$(just _extract_proc "{{procname}}")"

# extract worker start command from Procfile, useful for defining entrypoints in docker containers
[script]
_extract_proc procname:
	[ -n "{{procname}}" ] || exit 0
	yq -r '.{{procname}}' Procfile
