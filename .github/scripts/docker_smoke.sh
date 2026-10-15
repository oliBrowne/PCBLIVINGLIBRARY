#!/bin/bash
# Sign in to the running compose stack over HTTPS (Caddy's internal CA), as a browser would.
#   .github/scripts/docker_smoke.sh https://localhost:8443 PASSWORD http://localhost:8080    (admin user "ci")
set -euo pipefail
base=$1; pw=$2; http_base=$3
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt "$work/ca.crt" >/dev/null
c() { curl -sS --noproxy '*' --cacert "$work/ca.crt" "$@"; }
code() { c -o /dev/null -w '%{http_code}' "$@"; }
expect() { [ "$2" = "$3" ] && echo "ok   $1 ($3)" || { echo "FAIL $1: got $2, want $3"; exit 1; }; }

expect "anonymous admin route" "$(code "$base/api/team/users")" 401
expect "wrong password" "$(code -H 'Content-Type: application/json' -d '{"username":"ci","password":"wrong-password-123"}' "$base/api/login")" 401
printf '{"username":"ci","password":"%s"}' "$pw" \
  | c -D "$work/h" -c "$work/jar" -H 'Content-Type: application/json' --data-binary @- "$base/api/login" > "$work/login.json"
expect "signed in as" "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["user"]["role"])' "$work/login.json")" admin
grep -qi '^set-cookie:.*HttpOnly; SameSite=Strict.*; Secure' "$work/h" && echo "ok   Secure session cookie" \
  || { echo "FAIL cookie is not Secure/HttpOnly"; exit 1; }
expect "admin route with session" "$(code -b "$work/jar" "$base/api/team/users")" 200
expect "client-sent X-Forwarded-User is ignored" "$(code -H 'X-Forwarded-User: ci' "$base/api/team/users")" 401
expect "HTTP redirects to HTTPS" "$(curl -sS --noproxy '*' -o /dev/null -w '%{http_code} %{redirect_url}' "$http_base/")" "308 $base/"
