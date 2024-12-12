#!/usr/bin/env zsh
# Description: assume arg[1] is a `op read` key-path

# adapted from: https://medium.com/@JesseDearing/using-1password-values-in-terraform-71d2e3077380

set -e

key_path="$1"
password=$(op read "$key_path")

# Use JQ to put args into a JSON string we return to STDOUT
jq -n --arg password "$password" '{"password":$password}'