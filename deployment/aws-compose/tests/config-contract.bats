#!/usr/bin/env bats

AUTH="maxkey-webs/maxkey-web-maxkey/src/main/resources/application-maxkey.properties"
MGT="maxkey-webs/maxkey-web-mgt/src/main/resources/application-maxkey-mgt.properties"

normalized() {
  tr -d '\r' < "$1"
}

@test "all services require JWT secrets from the environment" {
  for file in "$AUTH" "$MGT"; do
    normalized "$file" | grep -E 'maxkey\.auth\.jwt\.secret[[:space:]]*=\$\{JWT_SECRET\}$'
    normalized "$file" | grep -E 'maxkey\.auth\.jwt\.refresh\.secret[[:space:]]*=\$\{JWT_REFRESH_SECRET\}$'
  done
}

@test "auth and management public URLs can be overridden behind nginx" {
  grep -F 'maxkey.server.name' "$AUTH" | grep -F '${SERVER_PUBLIC_URL:'
  grep -F 'maxkey.server.authz.uri' "$AUTH" | grep -F '${SERVER_AUTHZ_URL:'
  grep -F 'maxkey.server.uri' "$MGT" | grep -F '${SERVER_MGT_API_URL:'
  grep -F 'maxkey.server.authz.uri' "$MGT" | grep -F '${SERVER_AUTHZ_URL:'
}

@test "management CAS URLs have no public maxkey.top default" {
  grep -F 'maxkey.login.cas.serverUrlPrefix' "$MGT" | grep -F '${LOGIN_CAS_SERVER}'
  grep -F 'maxkey.login.cas.service' "$MGT" | grep -F '${LOGIN_CAS_SERVICE}'
  ! grep -E 'LOGIN_CAS_(SERVER|SERVICE):http.*maxkey\.top' "$MGT"
}

@test "only nginx publishes host ports" {
  run python3 - <<'PY'
import yaml
doc = yaml.safe_load(open("deployment/aws-compose/compose.yaml"))
published = [name for name, svc in doc["services"].items() if svc.get("ports")]
assert published == ["nginx"], published
PY
  [ "$status" -eq 0 ]
}

@test "application services use redis persistence" {
  run python3 - <<'PY'
import yaml
doc = yaml.safe_load(open("deployment/aws-compose/compose.yaml"))
for name in ("maxkey-auth", "maxkey-mgt"):
    env = doc["services"][name]["environment"]
    assert env["SERVER_PERSISTENCE"] == "2", name
    assert env["REDIS_HOST"] == "redis", name
    assert env["REDIS_PORT"] == "6379", name
    assert "REDIS_PWD" in env, name
PY
  [ "$status" -eq 0 ]
}

@test "mysql and both backends receive the non-root database contract" {
  run python3 - <<'PY'
import yaml
doc = yaml.safe_load(open("deployment/aws-compose/compose.yaml"))
mysql = doc["services"]["mysql"]["environment"]
for key in ("MYSQL_DATABASE", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD"):
    assert key in mysql, key
for name in ("maxkey-auth", "maxkey-mgt"):
    env = doc["services"][name]["environment"]
    for key in ("DATABASE_HOST", "DATABASE_PORT", "DATABASE_NAME", "DATABASE_USER", "DATABASE_PWD"):
        assert key in env, (name, key)
    assert env["DATABASE_USER"] != "root"
PY
  [ "$status" -eq 0 ]
}

@test "compose gets every image from a digest-only variable" {
  run python3 - <<'PY'
import re, yaml
doc = yaml.safe_load(open("deployment/aws-compose/compose.yaml"))
for name, service in doc["services"].items():
    image = service["image"]
    assert re.fullmatch(r"\$\{[A-Z0-9_]+_IMAGE(?::\?[^}]+)?\}", image), (name, image)
    assert ":latest" not in image, (name, image)
    assert not re.search(r":\s*$", image), (name, image)
for line in open("deployment/aws-compose/images.env"):
    if line.strip() and not line.startswith("#"):
        assert re.fullmatch(r"[A-Z0-9_]+_IMAGE=[^@\s]+@sha256:[0-9a-f]{64}\n?", line), line
PY
  [ "$status" -eq 0 ]
}

@test "compose has isolated edge and data networks" {
  run python3 - <<'PY'
import yaml
doc = yaml.safe_load(open("deployment/aws-compose/compose.yaml"))
assert doc["networks"]["maxkey_data"]["internal"] is True
assert set(doc["networks"]) == {"maxkey_edge", "maxkey_data"}
expected = {
  "nginx": {"maxkey_edge"},
  "maxkey-frontend": {"maxkey_edge"},
  "maxkey-mgt-frontend": {"maxkey_edge"},
  "maxkey-auth": {"maxkey_edge", "maxkey_data"},
  "maxkey-mgt": {"maxkey_edge", "maxkey_data"},
  "mysql": {"maxkey_data"},
  "redis": {"maxkey_data"},
}
for name, networks in expected.items():
    assert set(doc["services"][name]["networks"]) == networks, name
PY
  [ "$status" -eq 0 ]
}

@test "compose has a stable project name" {
  run python3 - <<'PY'
import yaml
doc = yaml.safe_load(open("deployment/aws-compose/compose.yaml"))
assert doc["name"] == "maxkey"
PY
  [ "$status" -eq 0 ]
}

@test "required compose inputs fail fast with guards" {
  run python3 - <<'PY'
import re
text = open("deployment/aws-compose/compose.yaml").read()
required = {
    "AUTH_IMAGE", "MGT_IMAGE", "FRONTEND_IMAGE", "MGT_FRONTEND_IMAGE", "MYSQL_IMAGE", "REDIS_IMAGE", "NGINX_IMAGE",
    "PUBLIC_BASE_URL", "SERVER_SCHEME", "SERVER_HOST", "SERVER_AUTHZ_URL", "SERVER_MGT_URL", "SERVER_MGT_API_URL",
    "LOGIN_CAS_SERVER", "LOGIN_CAS_SERVICE",
    "DATABASE_NAME", "DATABASE_USER", "DATABASE_PWD", "MYSQL_ROOT_PASSWORD", "REDIS_PWD",
    "AUTH_JWT_SECRET", "AUTH_JWT_REFRESH_SECRET", "MGT_JWT_SECRET", "MGT_JWT_REFRESH_SECRET",
}
missing = [name for name in sorted(required) if not re.search(r"\$\{%s:\?[^}]+\}" % name, text)]
assert not missing, missing
PY
  [ "$status" -eq 0 ]
}

@test "backends use separate jwt secret mappings" {
  run python3 - <<'PY'
import yaml
doc = yaml.safe_load(open("deployment/aws-compose/compose.yaml"))
auth = doc["services"]["maxkey-auth"]["environment"]
mgt = doc["services"]["maxkey-mgt"]["environment"]
assert auth["JWT_SECRET"].startswith("${AUTH_JWT_SECRET:?"), auth["JWT_SECRET"]
assert auth["JWT_REFRESH_SECRET"].startswith("${AUTH_JWT_REFRESH_SECRET:?"), auth["JWT_REFRESH_SECRET"]
assert mgt["JWT_SECRET"].startswith("${MGT_JWT_SECRET:?"), mgt["JWT_SECRET"]
assert mgt["JWT_REFRESH_SECRET"].startswith("${MGT_JWT_REFRESH_SECRET:?"), mgt["JWT_REFRESH_SECRET"]
assert auth["JWT_SECRET"] != mgt["JWT_SECRET"]
assert auth["JWT_REFRESH_SECRET"] != mgt["JWT_REFRESH_SECRET"]
PY
  [ "$status" -eq 0 ]
}

@test "mysql seed bind mount fails closed when missing" {
  run python3 - <<'PY'
import yaml
doc = yaml.safe_load(open("deployment/aws-compose/compose.yaml"))
volumes = doc["services"]["mysql"]["volumes"]
seed = next(v for v in volumes if isinstance(v, dict) and v["target"] == "/docker-entrypoint-initdb.d/00-maxkey.sql")
assert seed["type"] == "bind"
assert seed["source"] == "/srv/maxkey/mysql/init/00-maxkey.sql"
assert seed["read_only"] is True
assert seed["bind"]["create_host_path"] is False
PY
  [ "$status" -eq 0 ]
}

@test "health checks use authenticated dependencies and expected http paths" {
  run python3 - <<'PY'
import yaml
doc = yaml.safe_load(open("deployment/aws-compose/compose.yaml"))
mysql = doc["services"]["mysql"]["healthcheck"]
mysql_test = " ".join(mysql["test"])
assert "mysqladmin" not in mysql_test
assert "MYSQL_PWD=$${MYSQL_PASSWORD}" in mysql_test
assert "mysql --protocol=TCP" in mysql_test
assert "-u$${MYSQL_USER}" in mysql_test
assert "$${MYSQL_DATABASE}" in mysql_test
assert "SELECT 1" in mysql_test
assert mysql["start_period"] == "300s"
redis_test = " ".join(doc["services"]["redis"]["healthcheck"]["test"])
assert 'redis-cli -a "$${REDIS_PWD}" ping' in redis_test
assert "http://localhost:9527/sign/" in " ".join(doc["services"]["maxkey-auth"]["healthcheck"]["test"])
assert "http://localhost:9526/maxkey-mgt-api/" in " ".join(doc["services"]["maxkey-mgt"]["healthcheck"]["test"])
PY
  [ "$status" -eq 0 ]
}

@test "redis command preserves official entrypoint privilege drop" {
  run python3 - <<'PY'
import yaml
doc = yaml.safe_load(open("deployment/aws-compose/compose.yaml"))
command = doc["services"]["redis"]["command"]
assert command[0] == "redis-server", command
assert "sh" not in command and "-c" not in command, command
assert "/usr/local/etc/redis/redis.conf" in command, command
assert "--requirepass" in command, command
assert any(item.startswith("${REDIS_PWD:?") for item in command), command
PY
  [ "$status" -eq 0 ]
}

@test "compose sets memory limits and json log rotation" {
  run python3 - <<'PY'
import yaml
doc = yaml.safe_load(open("deployment/aws-compose/compose.yaml"))
expected_memory = {
    "maxkey-auth": "3g",
    "maxkey-mgt": "2g",
    "mysql": "4g",
    "redis": "1g",
}
for name, memory in expected_memory.items():
    assert doc["services"][name]["mem_limit"] == memory, (name, doc["services"][name].get("mem_limit"))
for name, service in doc["services"].items():
    logging = service["logging"]
    assert logging["driver"] == "json-file", name
    assert logging["options"]["max-size"] == "10m", name
    assert logging["options"]["max-file"] == "5", name
PY
  [ "$status" -eq 0 ]
}

@test "nginx masks the stock default server and uses docker dns" {
  run python3 - <<'PY'
import re
import yaml
doc = yaml.safe_load(open("deployment/aws-compose/compose.yaml"))
targets = [v["target"] if isinstance(v, dict) else v.split(":")[1] for v in doc["services"]["nginx"]["volumes"]]
assert "/etc/nginx/templates/default.conf.template" in targets, targets
text = open("deployment/aws-compose/nginx/conf.d/maxkey.conf.template").read()
assert "listen 80 default_server;" in text
assert "server_name _;" in text
assert "resolver 127.0.0.11 valid=10s ipv6=off;" in text
assert "client_max_body_size 20m;" in text
assert "proxy_set_header Host $http_host;" in text
assert "proxy_set_header X-Forwarded-Port $server_port;" in text
assert "location = /" in text and "return 302 /maxkey/;" in text
for upstream in ("maxkey_auth", "maxkey_frontend", "maxkey_mgt", "maxkey_mgt_frontend"):
    assert f"set ${upstream} " in text, upstream
expected = {
    "location /sign/": "proxy_pass $maxkey_auth;",
    "location /maxkey/": "proxy_pass $maxkey_frontend;",
    "location /maxkey-mgt-api/": "proxy_pass $maxkey_mgt;",
    "location /maxkey-mgt/": "proxy_pass $maxkey_mgt_frontend;",
}
for location, proxy in expected.items():
    assert location in text, location
    assert proxy in text, proxy
assert not re.search(r"proxy_pass\s+\$[a-z_]+/", text), text
PY
  [ "$status" -eq 0 ]
}

@test "example app images are marked forbidden for releases" {
  grep -F "Application image references below are examples only and are forbidden in release archives." deployment/aws-compose/images.env
}
