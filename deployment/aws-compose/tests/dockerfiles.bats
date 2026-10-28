#!/usr/bin/env bats

normalized() {
  tr -d '\r' < "$1"
}

@test "backend images do not sleep before startup" {
  ! grep -E "sleep[[:space:]]+60" \
    maxkey-webs/maxkey-web-maxkey/Dockerfile \
    maxkey-webs/maxkey-web-mgt/Dockerfile
}

@test "JAVA_OPTS appears before -jar" {
  for file in maxkey-webs/maxkey-web-maxkey/Dockerfile \
              maxkey-webs/maxkey-web-mgt/Dockerfile; do
    grep -E 'exec java \$\{JAVA_OPTS\} -jar' "$file"
  done
}

@test "backend runtime images contain curl" {
  for file in maxkey-webs/maxkey-web-maxkey/Dockerfile \
              maxkey-webs/maxkey-web-mgt/Dockerfile; do
    grep -F 'apk add --no-cache curl' "$file"
  done
}

@test "all deployed Dockerfile bases use literal digests and never latest" {
  for file in maxkey-webs/maxkey-web-maxkey/Dockerfile \
              maxkey-webs/maxkey-web-mgt/Dockerfile \
              maxkey-web-frontend/maxkey-web-ng/Dockerfile \
              maxkey-web-frontend/maxkey-web-mgt-ng/Dockerfile; do
    ! grep -Ei '^FROM[[:space:]]+([^[:space:]]*:latest|nginx)([[:space:]]|$)' "$file"
    normalized "$file" | grep -E '^FROM[[:space:]]+[^[:space:]]+@sha256:[0-9a-f]{64}$'
  done
}
