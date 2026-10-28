# MaxKey AWS Compose One-Click Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a versioned deployment bundle that installs MaxKey on a blank Ubuntu 24.04 AWS EC2 instance with one `sudo ./install.sh` command.

**Architecture:** CI or a trusted Linux/WSL2 build machine produces four immutable MaxKey images in AWS ECR and a checksummed deployment archive. The archive contains digest-pinned image references, an idempotent Bash installer, Docker Compose topology, hardened MySQL initialization, Redis/Nginx configuration, health checks, encrypted backup/restore scripts, and upgrade controls. The EC2 instance uses an ECR-read-only Instance Role and stores persistent state under `/srv/maxkey`.

**Tech Stack:** Bash 5, Docker Engine, Docker Compose v2, AWS CLI v2, AWS ECR/STS/IMDSv2, MySQL 8.4, Redis 7, Nginx, Bats, ShellCheck, Python 3 standard library, Java 21, Gradle 9.1, Angular 22, pnpm 12.

## Global Constraints

- Source branch is `saas-iam-4.2`.
- Source baseline includes commit `d7c9d9af1202726a1a54cd60c110dd917b9d93a6`.
- Target OS is Ubuntu Server 24.04 LTS on amd64 AWS EC2.
- Target EC2 capacity is 8 vCPU, 16 GB RAM, and 200 GB encrypted gp3.
- Runtime images must use ECR `repository@sha256:...` references from the release manifest; `latest`, untagged bases, and mutable runtime tags are forbidden.
- EC2 receives ECR credentials only through an Instance Role with `AmazonEC2ContainerRegistryReadOnly`.
- Off-host S3 backup, when enabled, adds a separate least-privilege policy limited to `s3:PutObject` and multipart-upload actions under one configured bucket prefix; no broad S3 policy is allowed.
- MySQL, Redis, application backends, and frontend containers must not expose host ports.
- Only Nginx exposes HTTP during the initial IP-based internal-validation phase.
- This bundle is not production-ready for OIDC until a follow-up release adds a domain, TLS on 443, and an HTTP-to-HTTPS redirect.
- OpenAPI is not part of the initial deployment.
- `SERVER_PERSISTENCE=2` uses Redis for token/session state.
- Secrets are generated on first install and stored only in `/srv/maxkey/.env` with mode `0600`.
- A repeated install must not regenerate secrets, rerun database initialization, or delete data.
- The original repository `deployment/docker/docker-compose.yml` is not a production input.
- Every task must leave an independently testable commit.
- Build and local verification commands run in Linux or WSL2; the target server never installs Gradle, Node.js, or pnpm.

---

## File Structure

The implementation creates this source layout:

```text
deployment/aws-compose/
├── .env.example
├── images.env
├── .gitignore
├── compose.yaml
├── install.sh
├── lib/
│   ├── common.sh
│   ├── aws.sh
│   ├── system.sh
│   └── stack.sh
├── mysql/
│   ├── conf.d/maxkey.cnf
│   └── init/01-hardening.sql.template
├── nginx/conf.d/maxkey.conf.template
├── redis/redis.conf
├── scripts/
│   ├── render_templates.py
│   ├── healthcheck.sh
│   ├── backup.sh
│   ├── restore.sh
│   ├── upgrade.sh
│   ├── build-images.sh
│   ├── publish-ecr.sh
│   ├── verify-image-scan.sh
│   └── package-release.sh
└── tests/
    ├── config-contract.bats
    ├── dockerfiles.bats
    ├── install.bats
    ├── operations.bats
    ├── release.bats
    ├── smoke.sh
    ├── test_render_templates.py
    └── fixtures/bin/
deployment/aws_compose_renderer.py
docs/runbooks/
└── maxkey-aws-compose.md
```

Responsibilities:

- `install.sh`: orchestration only.
- `lib/common.sh`: logging, failures, locks, retries, secret generation.
- `lib/aws.sh`: IMDSv2, STS, ECR discovery and login.
- `lib/system.sh`: OS validation and Docker/AWS CLI installation.
- `lib/stack.sh`: directory rendering and Compose lifecycle.
- `render_templates.py`: strict placeholder rendering.
- `compose.yaml`: runtime service topology.
- `scripts/*.sh`: operations that are deliberately separate from initial installation.

---

### Task 1: Externalize Public URLs and JWT Secrets

**Files:**
- Create: `deployment/aws-compose/tests/config-contract.bats`
- Modify: `maxkey-webs/maxkey-web-maxkey/src/main/resources/application-maxkey.properties`
- Modify: `maxkey-webs/maxkey-web-mgt/src/main/resources/application-maxkey-mgt.properties`

**Interfaces:**
- Consumes: environment variables provided by Compose.
- Produces: `SERVER_SCHEME`, `SERVER_HOST`, `SERVER_PUBLIC_URL`, `SERVER_AUTHZ_URL`, `SERVER_MGT_URL`, `SERVER_MGT_API_URL`, `LOGIN_CAS_SERVER`, `LOGIN_CAS_SERVICE`, `JWT_SECRET`, and `JWT_REFRESH_SECRET` configuration contract.

- [ ] **Step 1: Write the failing Bats configuration contract**

```bash
#!/usr/bin/env bats

AUTH="maxkey-webs/maxkey-web-maxkey/src/main/resources/application-maxkey.properties"
MGT="maxkey-webs/maxkey-web-mgt/src/main/resources/application-maxkey-mgt.properties"

@test "all services require JWT secrets from the environment" {
  for file in "$AUTH" "$MGT"; do
    grep -E 'maxkey\.auth\.jwt\.secret[[:space:]]*=\$\{JWT_SECRET\}$' "$file"
    grep -E 'maxkey\.auth\.jwt\.refresh\.secret[[:space:]]*=\$\{JWT_REFRESH_SECRET\}$' "$file"
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
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
docker run --rm -v "$PWD:/code" -w /code bats/bats:1.11.0 deployment/aws-compose/tests/config-contract.bats
```

Expected: FAIL because the existing properties contain hard-coded JWT secrets and do not expose the public URL variables.

- [ ] **Step 3: Replace hard-coded configuration with environment placeholders**

Use this pattern in the authentication and management property files:

```properties
maxkey.server.scheme=${SERVER_SCHEME:http}
maxkey.server.basedomain=${SERVER_DOMAIN:maxkey.top}
maxkey.server.domain=${SERVER_HOST:sso.${maxkey.server.basedomain}}
maxkey.server.name=${SERVER_PUBLIC_URL:${maxkey.server.scheme}://${maxkey.server.domain}}
maxkey.auth.jwt.secret=${JWT_SECRET}
maxkey.auth.jwt.refresh.secret=${JWT_REFRESH_SECRET}
```

In the authentication service:

```properties
maxkey.server.uri=${SERVER_AUTHZ_URL:${maxkey.server.name}:${server.port}${server.servlet.context-path}}
maxkey.server.authz.uri=${SERVER_AUTHZ_URL:${maxkey.server.name}:${server.port}${server.servlet.context-path}}
maxkey.server.mgt.uri=${SERVER_MGT_URL:${maxkey.server.name}/maxkey-mgt/login}
```

In the management service:

```properties
maxkey.server.uri=${SERVER_MGT_API_URL:${maxkey.server.name}:${server.port}${server.servlet.context-path}}
maxkey.server.mgt.uri=${SERVER_MGT_API_URL:${maxkey.server.uri}}
maxkey.server.authz.uri=${SERVER_AUTHZ_URL:${maxkey.server.name}/sign}
maxkey.login.cas.serverUrlPrefix=${LOGIN_CAS_SERVER}
maxkey.login.cas.service=${LOGIN_CAS_SERVICE}
```

- [ ] **Step 4: Run the contract test**

Run the Bats command from Step 2.

Expected: PASS.

- [ ] **Step 5: Build both deployed backends and verify secrets have no defaults**

Run:

```bash
./gradlew :maxkey-webs:maxkey-web-maxkey:bootJar :maxkey-webs:maxkey-web-mgt:bootJar --no-daemon
```

Expected: BUILD SUCCESSFUL, and the Bats exact-match assertions prove there is no fallback secret if either environment variable is absent.

- [ ] **Step 6: Commit**

```bash
git add deployment/aws-compose/tests/config-contract.bats \
  maxkey-webs/maxkey-web-maxkey/src/main/resources/application-maxkey.properties \
  maxkey-webs/maxkey-web-mgt/src/main/resources/application-maxkey-mgt.properties
git commit -m "feat: externalize deployment URLs and JWT secrets"
```

---

### Task 2: Harden Backend Runtime Images

**Files:**
- Create: `deployment/aws-compose/tests/dockerfiles.bats`
- Modify: `maxkey-webs/maxkey-web-maxkey/Dockerfile`
- Modify: `maxkey-webs/maxkey-web-mgt/Dockerfile`
- Modify: `maxkey-web-frontend/maxkey-web-ng/Dockerfile`
- Modify: `maxkey-web-frontend/maxkey-web-mgt-ng/Dockerfile`

**Interfaces:**
- Consumes: bootJar and prebuilt Angular artifacts.
- Produces: two JRE 21 backend images with correctly applied JVM flags and health tooling, plus two Nginx frontend images whose base images are digest-pinned.

- [ ] **Step 1: Write Dockerfile contract tests**

```bash
#!/usr/bin/env bats

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
    grep -E '^FROM[[:space:]]+[^[:space:]]+@sha256:[0-9a-f]{64}$' "$file"
  done
}
```

- [ ] **Step 2: Run and verify failure**

```bash
docker run --rm -v "$PWD:/code" -w /code bats/bats:1.11.0 deployment/aws-compose/tests/dockerfiles.bats
```

Expected: FAIL on `sleep 60`, JVM option ordering, and curl.

- [ ] **Step 3: Update each backend Dockerfile**

Resolve a supported JRE 21 Alpine base with `docker buildx imagetools inspect` and make the first line a literal version-plus-digest reference. Then use this authentication image fragment, changing paths and jar names for mgt:

```dockerfile
RUN apk add --no-cache curl \
    && addgroup -S maxkey \
    && adduser -S -G maxkey maxkey
WORKDIR /maxkey
ARG JAR_FILE=build/libs/maxkey-boot-*-ga.jar
COPY --chown=maxkey:maxkey ${JAR_FILE} maxkey-boot.jar
USER maxkey
EXPOSE 9527
ENV TZ=Asia/Shanghai
ENV JAVA_OPTS="-Xms512m -Xmx2048m -Djava.security.egd=file:/dev/./urandom"
ENTRYPOINT ["sh", "-c", "exec java ${JAVA_OPTS} -jar maxkey-boot.jar"]
```

Write the resolved literal digest into each Dockerfile. Remove the unused Node stages from both frontend Dockerfiles and use a versioned Nginx Alpine image pinned by literal digest.

- [ ] **Step 4: Run Dockerfile tests**

Expected: PASS.

- [ ] **Step 5: Build authentication and management images locally**

```bash
./gradlew :maxkey-webs:maxkey-web-maxkey:bootJar :maxkey-webs:maxkey-web-mgt:bootJar --no-daemon
docker build -t maxkey/auth:test maxkey-webs/maxkey-web-maxkey
docker build -t maxkey/mgt:test maxkey-webs/maxkey-web-mgt
```

Expected: both images build successfully.

- [ ] **Step 6: Commit**

```bash
git add deployment/aws-compose/tests/dockerfiles.bats \
  maxkey-webs/maxkey-web-maxkey/Dockerfile \
  maxkey-webs/maxkey-web-mgt/Dockerfile \
  maxkey-web-frontend/maxkey-web-ng/Dockerfile \
  maxkey-web-frontend/maxkey-web-mgt-ng/Dockerfile
git commit -m "fix: harden MaxKey runtime images"
```

---

### Task 3: Add the Production Compose Topology

**Files:**
- Create: `deployment/aws-compose/compose.yaml`
- Create: `deployment/aws-compose/.env.example`
- Create: `deployment/aws-compose/images.env`
- Create: `deployment/aws-compose/.gitignore`
- Create: `deployment/aws-compose/mysql/conf.d/maxkey.cnf`
- Create: `deployment/aws-compose/redis/redis.conf`
- Create: `deployment/aws-compose/nginx/conf.d/maxkey.conf.template`
- Extend: `deployment/aws-compose/tests/config-contract.bats`

**Interfaces:**
- Consumes: rendered `/srv/maxkey/.env`, ECR image references, persistent host directories.
- Produces: `mysql`, `redis`, `maxkey-auth`, `maxkey-mgt`, `maxkey-frontend`, `maxkey-mgt-frontend`, and `nginx` services.

- [ ] **Step 1: Add failing Compose security assertions**

Append:

```bash
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
  grep -F 'SERVER_PERSISTENCE: "2"' deployment/aws-compose/compose.yaml
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
  ! grep -Ei 'image:.*(:latest|:[[:space:]]*$)' deployment/aws-compose/compose.yaml
  run python3 - <<'PY'
import re, yaml
doc = yaml.safe_load(open("deployment/aws-compose/compose.yaml"))
for name, service in doc["services"].items():
    assert re.fullmatch(r"\$\{[A-Z0-9_]+_IMAGE\}", service["image"]), (name, service["image"])
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
```

The test container for this task must install PyYAML before Bats:

```bash
docker run --rm -v "$PWD:/code" -w /code python:3.13-alpine \
  sh -c 'pip install --no-cache-dir pyyaml==6.0.2 && apk add --no-cache bash bats && bats deployment/aws-compose/tests/config-contract.bats'
```

Expected: FAIL because `compose.yaml` does not exist.

- [ ] **Step 2: Create `.env.example`**

Include these keys with non-secret examples:

```dotenv
AWS_REGION=ap-southeast-1
ECR_REGISTRY=000000000000.dkr.ecr.ap-southeast-1.amazonaws.com
IMAGE_VERSION=4.2.0-company.1-d7c9d9af
PUBLIC_BASE_URL=http://192.0.2.10
SERVER_SCHEME=http
SERVER_HOST=192.0.2.10
SERVER_AUTHZ_URL=http://192.0.2.10/sign
SERVER_MGT_URL=http://192.0.2.10/maxkey-mgt/login
SERVER_MGT_API_URL=http://192.0.2.10/maxkey-mgt-api
LOGIN_CAS_SERVER=http://192.0.2.10/sign/authz/cas
LOGIN_CAS_SERVICE=http://192.0.2.10/maxkey-mgt/passport/trust/auth
DATABASE_NAME=maxkey
DATABASE_USER=maxkey_app
DATABASE_PWD=generated-at-install
MYSQL_ROOT_PASSWORD=generated-at-install
REDIS_PWD=generated-at-install
AUTH_JWT_SECRET=generated-at-install
AUTH_JWT_REFRESH_SECRET=generated-at-install
MGT_JWT_SECRET=generated-at-install
MGT_JWT_REFRESH_SECRET=generated-at-install
ADMIN_INITIAL_PASSWORD=written-to-root-only-file
BACKUP_AGE_RECIPIENT=age1example-recipient-only-not-a-private-key
BACKUP_S3_URI=
```

- [ ] **Step 3: Create digest-only image manifest**

`images.env` contains all seven runtime references (`AUTH_IMAGE`, `MGT_IMAGE`, both frontend images, `MYSQL_IMAGE`, `REDIS_IMAGE`, and `NGINX_IMAGE`) as literal `repository@sha256:...` values. During implementation, resolve and commit tested infrastructure digests; release packaging replaces the four application entries with the digests returned by ECR. No tag-only reference is accepted.

- [ ] **Step 4: Create Compose services**

The Compose file must:

- load every service image from the corresponding digest-only variable in `images.env`;
- mount `/srv/maxkey/mysql/data`, `/srv/maxkey/redis/data`, and rendered config paths;
- create `maxkey_edge` and `maxkey_data`, with `maxkey_data` set to `internal: true`;
- attach Nginx and all four application services to `maxkey_edge`; attach only both backends, MySQL, and Redis to `maxkey_data`;
- map only `${HTTP_BIND_IP:-0.0.0.0}:${HTTP_PORT:-80}:80` on Nginx;
- set `SERVER_PERSISTENCE: "2"` and Redis credentials on both backends;
- pass separate JWT secrets to auth and mgt containers;
- pass `MYSQL_DATABASE`, `MYSQL_USER`, `MYSQL_PASSWORD`, and `MYSQL_ROOT_PASSWORD` to MySQL so its entrypoint creates the database and non-root application account before loading `00-maxkey.sql`;
- pass `DATABASE_HOST=mysql`, `DATABASE_PORT=3306`, and the matching database name/non-root credentials to both backends; root credentials must never reach an application container;
- pass all public URL and management CAS variables from the configuration contract;
- set memory limits of 3 GB auth, 2 GB mgt, 4 GB MySQL, and 1 GB Redis;
- add MySQL `mysqladmin ping`, authenticated Redis `redis-cli ping`, and backend HTTP checks; auth checks `http://localhost:9527/sign/`, management checks `http://localhost:9526/maxkey-mgt-api/`, and backend checks accept HTTP 200–499 but reject connection errors and 5xx;
- use `depends_on.condition: service_healthy`;
- set Docker JSON log rotation to `10m` and 5 files.

- [ ] **Step 5: Create Nginx template**

Route:

```nginx
location /sign/ { proxy_pass http://maxkey-auth:9527/sign/; }
location /maxkey/ { proxy_pass http://maxkey-frontend:8527/maxkey/; }
location /maxkey-mgt-api/ { proxy_pass http://maxkey-mgt:9526/maxkey-mgt-api/; }
location /maxkey-mgt/ { proxy_pass http://maxkey-mgt-frontend:8526/maxkey-mgt/; }
```

Also set forwarded protocol/host headers, request IDs, timeouts, and deny access to dotfiles.

- [ ] **Step 6: Create MySQL and Redis configs**

MySQL config:

```ini
[mysqld]
character-set-server=utf8mb4
collation-server=utf8mb4_unicode_ci
default-time-zone=+08:00
skip-name-resolve=ON
max_connections=300
slow_query_log=ON
long_query_time=1
```

Redis config:

```conf
bind 0.0.0.0
protected-mode yes
appendonly yes
appendfsync everysec
maxmemory 768mb
maxmemory-policy noeviction
```

The Compose command supplies `--requirepass "$REDIS_PWD"` so the password is not committed.

- [ ] **Step 7: Run contract tests and Compose validation**

```bash
docker compose --env-file deployment/aws-compose/.env.example \
  --env-file deployment/aws-compose/images.env \
  -f deployment/aws-compose/compose.yaml config >/tmp/maxkey-compose.rendered.yaml
docker run --rm -v "$PWD:/code" -w /code python:3.13-alpine \
  sh -c 'pip install --no-cache-dir pyyaml==6.0.2 && apk add --no-cache bash bats && bats deployment/aws-compose/tests/config-contract.bats'
```

Expected: Compose config exits 0 and all tests pass.

- [ ] **Step 8: Commit**

```bash
git add deployment/aws-compose
git commit -m "feat: add production Docker Compose topology"
```

---

### Task 4: Render Secrets and Harden Initial Database Data

**Files:**
- Create: `deployment/aws-compose/scripts/render_templates.py`
- Create: `deployment/aws-compose/tests/test_render_templates.py`
- Create: `deployment/aws-compose/mysql/init/01-hardening.sql.template`

**Interfaces:**
- Consumes: source MaxKey SQL, generated bcrypt hash, `PUBLIC_BASE_URL`.
- Produces: `/srv/maxkey/mysql/init/00-maxkey.sql`, `01-hardening.sql`, `.env`, and Nginx config with no unresolved placeholders.

- [ ] **Step 1: Write strict renderer tests**

```python
import pathlib
import tempfile
import unittest

from deployment.aws_compose_renderer import render_text


class RenderTextTest(unittest.TestCase):
    def test_replaces_all_declared_values(self):
        self.assertEqual(
            render_text("__PUBLIC_BASE_URL__/x", {"PUBLIC_BASE_URL": "http://10.0.0.5"}),
            "http://10.0.0.5/x",
        )

    def test_rejects_unresolved_placeholder(self):
        with self.assertRaises(ValueError):
            render_text("__MISSING__", {})
```

Because hyphens are not importable Python package names, implement the importable helper at:

```text
deployment/aws_compose_renderer.py
```

and keep `deployment/aws-compose/scripts/render_templates.py` as its CLI wrapper.

- [ ] **Step 2: Run and verify failure**

```bash
python3 -m unittest discover -s deployment/aws-compose/tests -p 'test_*.py' -v
```

Expected: FAIL because the renderer does not exist.

- [ ] **Step 3: Implement `render_text`**

```python
import re

PLACEHOLDER = re.compile(r"__([A-Z0-9_]+)__")


def render_text(template: str, values: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise ValueError(f"unresolved placeholder: {key}")
        return values[key]

    rendered = PLACEHOLDER.sub(replace, template)
    if PLACEHOLDER.search(rendered):
        raise ValueError("rendered output still contains placeholders")
    return rendered
```

- [ ] **Step 4: Create hardened SQL template**

```sql
START TRANSACTION;

UPDATE mxk_userinfo
SET PASSWORD = CONCAT('{bcrypt}', '__MAXKEY_ADMIN_BCRYPT__'),
    DECIPHERABLE = '',
    PASSWORDSETTYPE = 1,
    STATUS = 1,
    ISLOCKED = 1,
    BADPASSWORDCOUNT = 0
WHERE ID = '1';

UPDATE mxk_userinfo SET STATUS = 2 WHERE ID <> '1';

UPDATE mxk_apps
SET STATUS = CASE WHEN ID = '622076759805923328' THEN 1 ELSE 0 END;

UPDATE mxk_apps
SET LOGINURL = '__PUBLIC_BASE_URL__/maxkey-mgt'
WHERE ID = '622076759805923328';

UPDATE mxk_apps_cas_details
SET SERVICE = '__PUBLIC_BASE_URL__/maxkey-mgt/passport/trust/auth',
    CALLBACKURL = '__PUBLIC_BASE_URL__/maxkey-mgt/passport/trust/auth'
WHERE ID = '622076759805923328';

CREATE TABLE IF NOT EXISTS maxkey_deployment_state (
    STATE_KEY VARCHAR(64) PRIMARY KEY,
    STATE_VALUE VARCHAR(255) NOT NULL,
    UPDATED_AT TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

INSERT INTO maxkey_deployment_state (STATE_KEY, STATE_VALUE)
VALUES ('initial_hardening', 'complete')
ON DUPLICATE KEY UPDATE STATE_VALUE = VALUES(STATE_VALUE);

COMMIT;
```

- [ ] **Step 5: Add renderer tests for SQL safety**

Test that rendered SQL:

- contains `{bcrypt}$2`;
- contains no `__...__`;
- deactivates every non-admin seeded user;
- keeps only management app `622076759805923328` active;
- rewrites management CAS URLs.
- writes the `initial_hardening=complete` marker only after all hardening statements.

- [ ] **Step 6: Run tests**

```bash
python3 -m unittest discover -s deployment/aws-compose/tests -p 'test_*.py' -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add deployment/aws_compose_renderer.py deployment/aws-compose/scripts/render_templates.py \
  deployment/aws-compose/tests/test_render_templates.py \
  deployment/aws-compose/mysql/init/01-hardening.sql.template
git commit -m "feat: harden MaxKey database initialization"
```

---

### Task 5: Implement the Idempotent AWS Installer

**Files:**
- Create: `deployment/aws-compose/install.sh`
- Create: `deployment/aws-compose/lib/common.sh`
- Create: `deployment/aws-compose/lib/aws.sh`
- Create: `deployment/aws-compose/lib/system.sh`
- Create: `deployment/aws-compose/lib/stack.sh`
- Create: `deployment/aws-compose/tests/install.bats`
- Create: `deployment/aws-compose/tests/fixtures/bin/*`

**Interfaces:**
- Consumes: Ubuntu 24.04 EC2, ECR ReadOnly Instance Role, verified deployment archive with digest-only `images.env`.
- Produces: a running `/srv/maxkey` stack and exit codes 0/10/20/30/40/50.

- [ ] **Step 1: Write unit tests for common helpers**

Cover:

- non-root execution returns 10;
- unsupported OS returns 10;
- insufficient CPU, memory, or free disk returns 10;
- an occupied required host port returns 10;
- missing or unsupported Docker/Compose versions return 10 when installation cannot correct them;
- missing Instance Role returns 20;
- existing `.env` is preserved;
- existing MySQL data is never reinitialized;
- a fresh install creates one 24-character alphanumeric administrator password, writes it once to `/root/maxkey-initial-credentials.txt` with mode `0600`, and derives a bcrypt cost-12 hash with `htpasswd -bnBC 12`;
- a retry before successful database initialization reuses the existing initial administrator password; a retry after the hardening marker exists neither needs nor recreates the credentials file;
- nonempty MySQL data without a valid `initial_hardening=complete` database marker prevents application startup with exit 30;
- generated service secrets use at least 64 lowercase hex characters and round-trip unchanged through `.env` and `docker compose config`;
- a lock prevents concurrent installation;
- `latest` image version is rejected.
- a missing, malformed, or mismatched `manifest.sha256` aborts before installer libraries are sourced or files copied;
- every image in `images.env` is a literal digest reference matching the checksummed release manifest.

Use fixture commands injected at the front of `PATH` so tests never call real IMDS, apt, Docker, or AWS.
Use table-driven fixtures to assert the documented `0/10/20/30/40/50` exit-code mapping.

- [ ] **Step 2: Run and verify failure**

```bash
docker run --rm -v "$PWD:/code" -w /code bats/bats:1.11.0 deployment/aws-compose/tests/install.bats
```

Expected: FAIL because installer libraries do not exist.

- [ ] **Step 3: Implement `lib/common.sh`**

Export:

```bash
log_info
log_error
die EXIT_CODE MESSAGE
verify_manifest MANIFEST ROOT
require_root
acquire_lock
retry ATTEMPTS DELAY COMMAND...
random_secret LENGTH
write_secret_file PATH CONTENT
```

Use `flock /run/lock/maxkey-install.lock` and `umask 077`. `random_secret` must emit lowercase hex only so values are safe in Compose dotenv, shell arguments, JDBC, Redis, and SQL contexts; it must never emit `$`, `#`, quotes, whitespace, backslashes, or newlines.

- [ ] **Step 4: Implement `lib/aws.sh`**

Export:

```bash
imds_token
aws_region
aws_account_id
ecr_registry
ecr_login
```

IMDS requests must include an IMDSv2 token and use short connection timeouts. `ecr_login` must use:

```bash
aws ecr get-login-password --region "$region" \
  | docker login --username AWS --password-stdin "$registry"
```

- [ ] **Step 5: Implement `lib/system.sh`**

Export:

```bash
validate_ubuntu_2404_amd64
validate_capacity
install_base_packages
install_docker
enable_docker
```

Install Docker from Docker's signed official APT repository, not `curl | sh`.
Install `age`, `apache2-utils`, AWS CLI v2, `jq`, `curl`, and certificate tooling from verified Ubuntu packages or vendor archives with pinned checksum verification.

- [ ] **Step 6: Implement `lib/stack.sh`**

Export:

```bash
prepare_directories
generate_env_once
generate_admin_bootstrap_once
render_stack_files
pull_images
start_data_services
wait_for_data_services
validate_database_hardening
start_application_services
run_healthcheck
validate_image_digests
record_image_digests
```

`generate_env_once` must return without changing any existing secret. On first install it also generates one age X25519 identity, writes only its public recipient to `.env`, and writes the private recovery identity to `/root/maxkey-backup-age-key.txt` with mode `0600`; the runbook requires copying that identity to an offline secrets store and deleting the server copy after a restore drill.

`generate_admin_bootstrap_once` runs only for an empty database. It creates or reuses `/root/maxkey-initial-credentials.txt`, generates a 24-character password from a dotenv/SQL-safe alphanumeric alphabet, extracts only the bcrypt hash from `htpasswd -bnBC 12`, and passes that hash directly to the strict renderer without writing it to `.env` or a command line. File creation must be atomic and mode `0600`. If data exists without the hardening marker, it fails closed instead of changing the admin password. Once the hardening marker exists, a deleted credentials file is never recreated.

`validate_database_hardening` must query the completion marker and independently verify the admin bcrypt prefix, disabled non-admin users, disabled non-management apps, and rewritten management callback URLs. A nonempty database that fails validation is never reinitialized automatically and no application container may start.

- [ ] **Step 7: Implement `install.sh` orchestration**

Before sourcing any packaged library, the script must use the host `sha256sum` to validate every entry in `manifest.sha256`; fail closed on a missing manifest, extra unapproved executable, malformed line, missing file, or checksum mismatch. It must then:

```bash
set -Eeuo pipefail
umask 077
```

Then source the four libraries and execute the approved installation steps. Preflight validates exact OS/architecture, at least 8 CPU, 16 GB RAM, required free disk, occupied ports, Docker Engine, and Compose v2. Validate every expected ECR digest before startup rather than merely recording the digest after pull. Add an `ERR` trap that prints the failing command, exit code, and diagnostics path without deleting data.

- [ ] **Step 8: Run Bats and ShellCheck**

```bash
docker run --rm -v "$PWD:/mnt" koalaman/shellcheck:v0.10.0 \
  deployment/aws-compose/install.sh deployment/aws-compose/lib/*.sh
docker run --rm -v "$PWD:/code" -w /code bats/bats:1.11.0 \
  deployment/aws-compose/tests/install.bats
```

Expected: zero ShellCheck findings and all Bats tests pass.

- [ ] **Step 9: Run dry-run installer test**

Add `MAXKEY_INSTALL_DRY_RUN=1`; run:

```bash
sudo MAXKEY_INSTALL_DRY_RUN=1 deployment/aws-compose/install.sh
```

Expected: preflight and render steps complete, no package, AWS, Docker, or filesystem mutation outside a temporary test root.

- [ ] **Step 10: Commit**

```bash
git add deployment/aws-compose/install.sh deployment/aws-compose/lib deployment/aws-compose/tests
git commit -m "feat: add idempotent AWS Compose installer"
```

---

### Task 6: Add Health, Backup, Restore, and Upgrade Operations

**Files:**
- Create: `deployment/aws-compose/scripts/healthcheck.sh`
- Create: `deployment/aws-compose/scripts/backup.sh`
- Create: `deployment/aws-compose/scripts/restore.sh`
- Create: `deployment/aws-compose/scripts/upgrade.sh`
- Create: `deployment/aws-compose/tests/operations.bats`

**Interfaces:**
- Consumes: `/srv/maxkey/.env`, running Compose stack, backup directory.
- Produces: machine-readable health result, encrypted database backups, guarded restore, and version-controlled upgrade.

- [ ] **Step 1: Write failing operations tests**

Cover:

- health fails when any required container is unhealthy;
- health checks `/maxkey/`, `/sign/`, `/maxkey-mgt/`, `/maxkey-mgt-api/`;
- backup uses `mysqldump --single-transaction --routines --triggers`;
- backup writes SHA-256;
- backup encrypts both the database and deployment/configuration bundle with the configured age recipient;
- no plaintext `.sql`, `.env`, JWT secret, or age private identity remains in the backup directory;
- `--upload` requires `BACKUP_S3_URI` and copies only encrypted artifacts plus checksums off-host;
- restore rejects a missing or mismatched checksum;
- restore requires an explicit age identity file and rejects failed decryption;
- restore requires `--confirm-restore`;
- upgrade rejects `latest`;
- upgrade runs backup before any migration or container replacement.
- failed upgrade restores the previous digest manifest, runs `docker compose pull` and `up -d`, and verifies rollback health.

- [ ] **Step 2: Run and verify failure**

```bash
docker run --rm -v "$PWD:/code" -w /code bats/bats:1.11.0 \
  deployment/aws-compose/tests/operations.bats
```

Expected: FAIL because scripts do not exist.

- [ ] **Step 3: Implement health check**

Return:

- 0 when all services and HTTP paths are healthy;
- 1 when a required container is down;
- 2 when an HTTP endpoint fails;
- 3 when MySQL or Redis fails.

Never print secrets or full tokens.

- [ ] **Step 4: Implement backup**

Write:

```text
/srv/maxkey/backup/YYYY/MM/DD/maxkey-HHMMSS.sql.gz.age
/srv/maxkey/backup/YYYY/MM/DD/maxkey-HHMMSS.sql.gz.age.sha256
/srv/maxkey/backup/YYYY/MM/DD/deployment-HHMMSS.tar.gz.age
/srv/maxkey/backup/YYYY/MM/DD/deployment-HHMMSS.tar.gz.age.sha256
```

The deployment bundle contains Compose/Nginx configuration, hardened SQL, `.env`, and the current image digest manifest, but is encrypted before it is moved into the backup directory. Use age public-key encryption; never use a passphrase in a command line. Delete plaintext staging data on every exit. `--upload` sends only encrypted artifacts and checksums to `BACKUP_S3_URI` with AWS CLI. Delete local backups older than 14 days only after the new encrypted backup and checksum succeed.

- [ ] **Step 5: Implement restore**

Require:

```bash
sudo ./scripts/restore.sh --confirm-restore \
  --identity /path/to/offline-age-identity.txt BACKUP.sql.gz.age
```

Before import, stop application containers but keep MySQL running. Verify checksum before decryption, create a pre-restore encrypted backup, decrypt into a mode-`0600` temporary file, import, securely remove the temporary file, start applications, and run health checks.

- [ ] **Step 6: Implement upgrade**

Require an immutable `--version`. Sequence:

1. validate a release `images.env` containing exact ECR digests;
2. backup;
3. stop writes;
4. apply reviewed SQL from `migrations/<from>-to-<to>.sql` if present;
5. atomically replace the active image digest manifest while preserving the previous manifest;
6. pull and restart;
7. run health checks;
8. on health failure restore the previous digest manifest, run `docker compose pull` and `docker compose up -d`, then require rollback health to pass; report rollback failure as a separate fatal condition;
9. never auto-restore a database after an incompatible migration.

- [ ] **Step 7: Run Bats and ShellCheck**

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add deployment/aws-compose/scripts deployment/aws-compose/tests/operations.bats
git commit -m "feat: add MaxKey deployment operations"
```

---

### Task 7: Build, Publish, and Package Immutable Releases

**Files:**
- Create: `deployment/aws-compose/scripts/build-images.sh`
- Create: `deployment/aws-compose/scripts/publish-ecr.sh`
- Create: `deployment/aws-compose/scripts/package-release.sh`
- Create: `deployment/aws-compose/scripts/verify-image-scan.sh`
- Create: `deployment/aws-compose/tests/release.bats`

**Interfaces:**
- Consumes: clean Git checkout, Java 21, Docker, pnpm 12, AWS publisher credentials.
- Produces: four ECR images, digest manifest, deployment tarball, external SHA-256 file.

- [ ] **Step 1: Write failing release tests**

Assert:

- dirty Git trees are rejected;
- version is derived from explicit `--version`, not branch name;
- four image names are built;
- ECR tags never use `latest`;
- both frontend Dockerfiles have no unused Node stage and every deployed base is digest-pinned;
- package includes source `sql/v4.2.0/maxkey.sql` as `mysql/init/00-maxkey.sql`;
- packaged `images.env` contains the four ECR application digests and tested infrastructure digests;
- package manifest verification fails for a changed, missing, or extra staged file;
- package includes no `.env`;
- archive and `.sha256` are produced.

- [ ] **Step 2: Implement backend and frontend build**

Backend:

```bash
./gradlew :maxkey-webs:maxkey-web-maxkey:bootJar \
  :maxkey-webs:maxkey-web-mgt:bootJar --no-daemon
```

Frontend, in each frontend directory:

```bash
corepack enable
pnpm install --frozen-lockfile
pnpm run build:prod
```

Build the four Docker images with the same immutable version.
Before building, run `dockerfiles.bats` so mutable frontend or backend base images cannot enter a release.

- [ ] **Step 3: Implement ECR publication**

The script:

- obtains Account ID and Region from AWS CLI;
- creates missing ECR repositories only when the publisher role allows it;
- otherwise requires all four repositories to exist;
- enables tag immutability and scan-on-push;
- pushes immutable tags;
- records `repoDigest` for every image;
- waits for all four enhanced/basic scan results and fails publication on any CRITICAL or HIGH finding unless a versioned, expiring exception file explicitly names the CVE and image digest.

- [ ] **Step 4: Implement package creation**

Stage only approved deployment files. Copy:

```text
sql/v4.2.0/maxkey.sql
```

to:

```text
mysql/init/00-maxkey.sql
```

Replace the four application entries in staged `images.env` with `repository@sha256:...` references from the publish result and validate every image entry. Generate `manifest.sha256` over every staged file except the manifest itself, verify it once in staging, create the tarball, then create the tarball `.sha256`. Release tests must tamper with one staged file and prove inner verification fails.

- [ ] **Step 5: Run tests and inspect archive**

```bash
docker run --rm -v "$PWD:/code" -w /code bats/bats:1.11.0 \
  deployment/aws-compose/tests/release.bats
tar -tzf dist/maxkey-deploy-4.2.0-company.1.tar.gz
```

Expected: all tests pass; archive contains no secrets, build outputs, `.git`, or `.env`.

- [ ] **Step 6: Commit**

```bash
git add deployment/aws-compose/scripts deployment/aws-compose/tests/release.bats
git commit -m "feat: package immutable MaxKey deployment releases"
```

---

### Task 8: Add the Operator Runbook and End-to-End Acceptance

**Files:**
- Create: `docs/runbooks/maxkey-aws-compose.md`
- Create: `deployment/aws-compose/tests/smoke.sh`
- Modify: `docs/superpowers/specs/2026-10-28-maxkey-single-host-deployment-design.md`

**Interfaces:**
- Consumes: built release archive and a fresh Ubuntu 24.04 EC2 test instance.
- Produces: repeatable operator instructions and signed-off deployment evidence.

- [ ] **Step 1: Write the runbook**

Document exact AWS preparation:

- Ubuntu 24.04 AMI;
- 8 vCPU / 16 GB;
- 200 GB encrypted gp3;
- EIP;
- Security Group rules that restrict SSH and temporary HTTP 80 to named trusted source CIDRs;
- ECR ReadOnly Instance Role;
- optional backup policy scoped to `s3:PutObject`, multipart-upload completion/abort, and one `BACKUP_S3_URI` prefix; it becomes mandatory before off-host-backup acceptance and must not grant bucket-wide read or delete;
- four ECR repositories;
- archive checksum verification;
- one-command installation;
- first login and deletion of `/root/maxkey-initial-credentials.txt`;
- export of `/root/maxkey-backup-age-key.txt` to an offline secrets store, a restore drill, and deletion of the server copy;
- encrypted off-host backup upload to the configured S3 URI;
- backup, restore, upgrade, and diagnostics commands.

State prominently that IP/HTTP is for internal validation only. Formal OIDC integration and public production traffic are blocked until a domain, trusted TLS certificate, Nginx 443 listener, secure-cookie review, and HTTP-to-HTTPS redirect are delivered and accepted.
Include the exact least-privilege S3 policy template and explain that `AmazonEC2ContainerRegistryReadOnly` alone cannot upload backups.

- [ ] **Step 2: Implement smoke test**

The script must verify:

```text
GET /maxkey/              -> 200
GET /sign/                -> 200 or expected redirect
GET /maxkey-mgt/          -> 200
GET /maxkey-mgt-api/      -> expected authenticated response
MySQL SELECT 1            -> success
Redis authenticated PING  -> PONG
```

It must also verify that host ports 3306, 6379, 9525, 9526, 9527, 8526, and 8527 are not listening.

- [ ] **Step 3: Test on a fresh EC2 instance**

Execute:

```bash
sha256sum -c maxkey-deploy-4.2.0-company.1.tar.gz.sha256
tar -xzf maxkey-deploy-4.2.0-company.1.tar.gz
cd maxkey-deploy-4.2.0-company.1
sudo ./install.sh
sudo /srv/maxkey/scripts/healthcheck.sh
```

Expected:

- installation exits 0;
- all required services are healthy;
- only approved host ports listen;
- initial admin login succeeds;
- forced password change succeeds;
- encrypted backup artifacts contain no plaintext secrets and an off-host copy succeeds;
- a second `sudo ./install.sh` exits 0 without changing secrets or database data.

- [ ] **Step 4: Exercise backup and restore**

Create a test identity, back up, delete it, restore, and verify it returns. Record command output in the release evidence.

- [ ] **Step 5: Exercise failed upgrade rollback**

Attempt an upgrade to a deliberately unhealthy test image. Verify health check failure restores the previous image version while preserving MySQL.

- [ ] **Step 6: Update design status**

Append implementation evidence to the design document:

- tested EC2 AMI ID;
- release version;
- image digests;
- image scan results and approved exceptions, if any;
- smoke-test timestamp;
- restore-test timestamp;
- known limitations.

- [ ] **Step 7: Commit**

```bash
git add docs/runbooks/maxkey-aws-compose.md deployment/aws-compose/tests/smoke.sh \
  docs/superpowers/specs/2026-10-28-maxkey-single-host-deployment-design.md
git commit -m "docs: add AWS MaxKey deployment runbook"
```

---

## Final Verification

Run locally or on the trusted build host:

```bash
docker run --rm -v "$PWD:/mnt" koalaman/shellcheck:v0.10.0 \
  deployment/aws-compose/install.sh \
  deployment/aws-compose/lib/*.sh \
  deployment/aws-compose/scripts/*.sh

docker run --rm -v "$PWD:/code" -w /code python:3.13-alpine \
  sh -c 'pip install --no-cache-dir pyyaml==6.0.2 && \
    apk add --no-cache bash bats git tar && \
    bats deployment/aws-compose/tests && \
    python3 -m unittest discover -s deployment/aws-compose/tests -p "test_*.py" -v'

docker compose --env-file deployment/aws-compose/.env.example \
  --env-file deployment/aws-compose/images.env \
  -f deployment/aws-compose/compose.yaml config

git diff --check
```

Then run the Task 8 fresh-EC2 installation, repeat-install, backup/restore, and failed-upgrade tests. Internal-validation acceptance requires all checks to pass and recording the exact ECR image digests; production approval remains blocked until the domain/TLS follow-up is implemented and accepted.
