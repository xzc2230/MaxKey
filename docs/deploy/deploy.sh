#!/usr/bin/env bash
# 在仓库根目录构建并启动中间件、MaxKey 前后端、Chatwoot 和客服入口。
# 用法：bash docs/deploy/deploy.sh [--rebuild]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$ROOT/docs/deploy/.env"
EXAMPLE="$ROOT/docs/deploy/.env.example"
COMPOSE_FILE="$ROOT/docs/deploy/compose.yaml"
SQL_SRC="$ROOT/sql/v4.2.0/maxkey.sql"
SQL_DEST="$ROOT/docs/deploy/mysql/init/maxkey.sql"
REBUILD=0

if [[ "${1:-}" == "--rebuild" ]]; then
  REBUILD=1
elif [[ -n "${1:-}" ]]; then
  echo "用法: bash docs/deploy/deploy.sh [--rebuild]" >&2
  exit 2
fi

cd "$ROOT"

if [[ ! -f "$COMPOSE_FILE" || ! -f "$EXAMPLE" || ! -f "$SQL_SRC" ]]; then
  echo "请在仓库根目录执行。需要 docs/deploy/compose.yaml、.env.example 和 sql/v4.2.0/maxkey.sql。" >&2
  exit 1
fi

if [[ ! -w "$ROOT" ]]; then
  echo "仓库目录不可写。先执行: sudo chown -R $USER:$USER $ROOT" >&2
  exit 1
fi

if [[ "$(timedatectl show -p Timezone --value 2>/dev/null || true)" != "Asia/Shanghai" ]]; then
  sudo timedatectl set-timezone Asia/Shanghai
fi

export DEBIAN_FRONTEND=noninteractive
sudo apt-get update
sudo apt-get install -y ca-certificates curl xz-utils python3 openjdk-21-jdk docker.io docker-compose-v2
sudo systemctl enable --now docker

if ! id -nG "$USER" | grep -qw docker; then
  sudo usermod -aG docker "$USER" || true
  echo "已把 $USER 加入 docker 组。本次命令仍使用 sudo，重新登录后可以直接运行 docker。"
fi

if docker info >/dev/null 2>&1; then
  DOCKER=(docker)
else
  DOCKER=(sudo docker)
fi

install_node24() {
  if command -v node >/dev/null 2>&1 && [[ "$(node -v 2>/dev/null || true)" == v24.* ]]; then
    echo "Node $(node -v)"
    return
  fi
  local ver="24.20.0"
  curl -fsSL "https://nodejs.org/dist/v${ver}/node-v${ver}-linux-x64.tar.xz" -o "/tmp/node-v${ver}.tar.xz"
  sudo tar -xJf "/tmp/node-v${ver}.tar.xz" -C /usr/local --strip-components=1
  rm -f "/tmp/node-v${ver}.tar.xz"
  hash -r
  node -v | grep -q '^v24\.'
}

install_pnpm() {
  if command -v pnpm >/dev/null 2>&1 && [[ "$(pnpm -v 2>/dev/null || true)" == "12.6.0" ]]; then
    echo "pnpm $(pnpm -v)"
    return
  fi
  if command -v corepack >/dev/null 2>&1; then
    sudo corepack enable || true
    if sudo corepack prepare pnpm@12.6.0 --activate; then
      hash -r
      return
    fi
  fi
  sudo npm install -g pnpm@12.6.0
  hash -r
}

compose() {
  "${DOCKER[@]}" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@"
}

image_exists() {
  "${DOCKER[@]}" image inspect "$1" >/dev/null 2>&1
}

prepare_env() {
  ENV_FILE="$ENV_FILE" EXAMPLE="$EXAMPLE" python3 - <<'PY'
import os
import pathlib
import re
import secrets
import sys

env_path = pathlib.Path(os.environ["ENV_FILE"])
example = pathlib.Path(os.environ["EXAMPLE"])
text = example.read_text(encoding="utf-8")
if env_path.exists():
    text = env_path.read_text(encoding="utf-8")
text = text.replace("\r\n", "\n").replace("\r", "\n")
if not text.endswith("\n"):
    text += "\n"

def get(key):
    match = re.search(rf"^{re.escape(key)}=(.*)$", text, re.M)
    return match.group(1) if match else None

def setk(key, value):
    global text
    line = f"{key}={value}"
    if re.search(rf"^{re.escape(key)}=", text, re.M):
        text = re.sub(rf"^{re.escape(key)}=.*$", line, text, count=1, flags=re.M)
    else:
        text += line + "\n"

def ensure(key, factory):
    if get(key):
        print(f"保留 {key}")
        return
    setk(key, factory())
    print(f"已生成 {key}")

ensure("MAXKEY_DATABASE_ROOT_PASSWORD", lambda: secrets.token_hex(24))
ensure("MAXKEY_DATABASE_PASSWORD", lambda: secrets.token_hex(24))
ensure("MAXKEY_REDIS_PASSWORD", lambda: secrets.token_hex(24))
ensure("MAXKEY_JWT_SECRET", lambda: secrets.token_urlsafe(64))
ensure("MAXKEY_JWT_REFRESH_SECRET", lambda: secrets.token_urlsafe(64))
ensure("MAXKEY_CLIENT_SECRET", lambda: secrets.token_hex(16))
ensure("CHATWOOT_POSTGRES_PASSWORD", lambda: secrets.token_hex(24))
ensure("CHATWOOT_REDIS_PASSWORD", lambda: secrets.token_hex(24))
ensure("CHATWOOT_SECRET_KEY_BASE", lambda: secrets.token_hex(64))
ensure("CHATWOOT_ACTIVE_RECORD_PRIMARY_KEY", lambda: secrets.token_hex(32))
ensure("CHATWOOT_ACTIVE_RECORD_DETERMINISTIC_KEY", lambda: secrets.token_hex(32))
ensure("CHATWOOT_ACTIVE_RECORD_SALT", lambda: secrets.token_hex(32))
ensure("CHATWOOT_AGENT_PASSWORD", lambda: secrets.token_hex(18))
if not get("MAXKEY_CLIENT_ID"):
    setk("MAXKEY_CLIENT_ID", "agent-bridge")
    print("已写入 MAXKEY_CLIENT_ID=agent-bridge")
else:
    print("保留 MAXKEY_CLIENT_ID")

required = [
    "MAXKEY_DATABASE_ROOT_PASSWORD",
    "MAXKEY_DATABASE_PASSWORD",
    "MAXKEY_REDIS_PASSWORD",
    "MAXKEY_JWT_SECRET",
    "MAXKEY_JWT_REFRESH_SECRET",
    "MAXKEY_CLIENT_ID",
    "MAXKEY_CLIENT_SECRET",
    "CHATWOOT_POSTGRES_PASSWORD",
    "CHATWOOT_REDIS_PASSWORD",
    "CHATWOOT_SECRET_KEY_BASE",
    "CHATWOOT_ACTIVE_RECORD_PRIMARY_KEY",
    "CHATWOOT_ACTIVE_RECORD_DETERMINISTIC_KEY",
    "CHATWOOT_ACTIVE_RECORD_SALT",
    "CHATWOOT_AGENT_PASSWORD",
    "MAXKEY_AUTH_IMAGE",
    "MAXKEY_MGT_IMAGE",
    "MAXKEY_AUTH_WEB_IMAGE",
    "MAXKEY_MGT_WEB_IMAGE",
    "CHATWOOT_IMAGE",
    "SSO_HOST",
    "ADMIN_HOST",
    "CHAT_HOST",
    "AGENT_HOST",
]
missing = [key for key in required if not get(key)]
if missing:
    print("仍缺少: " + ", ".join(missing), file=sys.stderr)
    sys.exit(1)

env_path.write_text(text, encoding="utf-8")
os.chmod(env_path, 0o600)
PY
}

warn_dns() {
  local pub="$1" host ips
  for host in "$SSO_HOST" "$ADMIN_HOST" "$CHAT_HOST" "$AGENT_HOST"; do
    ips="$(getent ahostsv4 "$host" 2>/dev/null | awk '{print $1}' | sort -u | paste -sd, - || true)"
    echo "域名 $host -> ${ips:-无记录}"
    case ",${ips}," in
      *",${pub},"*) ;;
      *)
        echo "警告: $host 没有直接解析到本机公网 IP ${pub}。把四条 A 记录指到这台机器，并关闭 CDN 代理。代理开着时，源站未监听会出现 521。" >&2
        ;;
    esac
  done
}

ports_busy() {
  ss -lnt | awk 'NR>1 && $4 ~ /:(80|443)$/ {found=1} END {exit !found}'
}

ensure_web_ports() {
  if ! ports_busy; then
    return
  fi
  if compose ps --status running --format '{{.Service}}' 2>/dev/null | grep -qx nginx; then
    return
  fi
  echo "80 或 443 已被其他程序占用。先停掉占用这两个端口的程序，再继续。" >&2
  ss -lntup | awk 'NR==1 || /:(80|443)$/' >&2 || true
  exit 1
}

wait_http() {
  local name="$1"
  shift
  local i code
  for i in $(seq 1 120); do
    if ! code="$("$@" 2>/dev/null)"; then
      code=""
    fi
    code="${code//$'\r'/}"
    code="${code##*$'\n'}"
    if [[ "$code" =~ ^[234][0-9][0-9]$ ]]; then
      echo "$name 就绪: HTTP $code"
      return 0
    fi
    sleep 5
  done
  echo "$name 在 10 分钟内没有返回 HTTP 状态。" >&2
  return 1
}

build_backend() {
  local tag="$1" dir="$2"
  if [[ "$REBUILD" == 0 ]] && image_exists "$tag"; then
    echo "镜像已存在: $tag"
    return
  fi
  "${DOCKER[@]}" build -t "$tag" "$dir"
}

build_frontend() {
  local tag="$1" dir="$2"
  if [[ "$REBUILD" == 0 ]] && image_exists "$tag"; then
    echo "镜像已存在: $tag"
    return
  fi
  (
    cd "$dir"
    HUSKY=0 CI=true NODE_OPTIONS="--max-old-space-size=4096" pnpm install --frozen-lockfile
    HUSKY=0 CI=true NODE_OPTIONS="--max-old-space-size=4096" pnpm run build:prod
  )
  if [[ ! -d "$dir/dist/browser" ]]; then
    echo "没有生成 $dir/dist/browser，前端镜像无法构建。" >&2
    exit 1
  fi
  "${DOCKER[@]}" build -t "$tag" "$dir"
}

bootstrap_chatwoot() {
  local tmp
  if [[ -n "${CHATWOOT_ACCOUNT_ID}" && -n "${CHATWOOT_PLATFORM_TOKEN}" && -n "${CHATWOOT_WEBSITE_TOKEN}" ]]; then
    echo "Chatwoot 账号、Platform Token 和网站收件箱 Token 已写入 .env"
    return
  fi
  tmp="$(mktemp)"
  chmod 600 "$tmp"
  compose exec -T \
    -e AGENT_PASSWORD="$CHATWOOT_AGENT_PASSWORD" \
    chatwoot-rails bundle exec rails runner '
password = ENV.fetch("AGENT_PASSWORD")
account = Account.find_by(name: "客服") || Account.create!(name: "客服", locale: "zh_CN")
admin = User.from_email("agent-admin@tdafadfaww.com") || User.new(
  name: "客服管理员",
  display_name: "客服管理员",
  email: "agent-admin@tdafadfaww.com",
  password: password,
  password_confirmation: password
)
admin.skip_confirmation!
admin.save!
AccountUser.find_or_create_by!(account: account, user: admin) { |membership| membership.role = :administrator }
platform_app = PlatformApp.find_by(name: "MaxKey Bridge") || PlatformApp.create!(name: "MaxKey Bridge")
platform_app.platform_app_permissibles.find_or_create_by!(permissible: account)
channel = Channel::WebWidget.find_by(account: account) || Channel::WebWidget.create!(
  account: account,
  website_url: "http://chat.tdafadfaww.com",
  welcome_title: "在线咨询",
  welcome_tagline: "请留下你的问题"
)
channel.inbox || Inbox.create!(account: account, name: "网站咨询", channel: channel)
puts "ACCOUNT_ID=#{account.id}"
puts "WEBSITE_TOKEN=#{channel.website_token}"
puts "HMAC_TOKEN=#{channel.hmac_token}"
puts "PLATFORM_TOKEN=#{platform_app.access_token.token}"
' | tee "$tmp"

  out="$tmp" ENV_FILE="$ENV_FILE" python3 - <<'PY'
import os
import pathlib
import re
import sys

output = pathlib.Path(os.environ["out"]).read_text(encoding="utf-8")
values = dict(re.findall(r"^(ACCOUNT_ID|WEBSITE_TOKEN|PLATFORM_TOKEN|HMAC_TOKEN)=(.*)$", output, re.M))
needed = ("ACCOUNT_ID", "WEBSITE_TOKEN", "PLATFORM_TOKEN")
missing = [key for key in needed if not values.get(key)]
if missing:
    print("Chatwoot 初始化没有返回: " + ", ".join(missing), file=sys.stderr)
    sys.exit(1)
env_path = pathlib.Path(os.environ["ENV_FILE"])
text = env_path.read_text(encoding="utf-8")
mapping = {
    "CHATWOOT_ACCOUNT_ID": values["ACCOUNT_ID"],
    "CHATWOOT_WEBSITE_TOKEN": values["WEBSITE_TOKEN"],
    "CHATWOOT_PLATFORM_TOKEN": values["PLATFORM_TOKEN"],
}
for key, value in mapping.items():
    line = f"{key}={value}"
    if re.search(rf"^{re.escape(key)}=", text, re.M):
        text = re.sub(rf"^{re.escape(key)}=.*$", line, text, count=1, flags=re.M)
    else:
        text += line + "\n"
env_path.write_text(text, encoding="utf-8")
os.chmod(env_path, 0o600)
print("已写回 CHATWOOT_ACCOUNT_ID、CHATWOOT_WEBSITE_TOKEN、CHATWOOT_PLATFORM_TOKEN")
hmac = values.get("HMAC_TOKEN", "")
if hmac:
    print("HMAC_TOKEN 只抄到业务系统服务端，不要写入 .env 或页面:")
    print(hmac)
PY
  rm -f "$tmp"
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
}

echo "1/8 准备 Docker、JDK 21、Node 24 和 pnpm 12.6.0"
install_node24
install_pnpm

echo "2/8 准备 docs/deploy/.env 和建库脚本"
prepare_env
install -m 644 "$SQL_SRC" "$SQL_DEST"
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

public_ip="$(curl -4 -fsS --max-time 5 https://checkip.amazonaws.com || true)"
public_ip="${public_ip//$'\n'/}"
if [[ -n "$public_ip" ]]; then
  echo "本机公网 IP: $public_ip"
  warn_dns "$public_ip"
else
  echo "没有读到本机公网 IP，跳过域名检查。"
fi

if "${DOCKER[@]}" volume inspect maxkey-saas_maxkey_mysql >/dev/null 2>&1; then
  echo "MaxKey MySQL 数据卷已存在。建库脚本只在数据卷第一次创建时导入。"
fi

ensure_web_ports

echo "3/8 构建 MaxKey 后端镜像"
if [[ "$REBUILD" == 1 ]] || ! image_exists "$MAXKEY_AUTH_IMAGE" || ! image_exists "$MAXKEY_MGT_IMAGE"; then
  chmod +x "$ROOT/gradlew"
  "$ROOT/gradlew" --no-daemon \
    :maxkey-webs:maxkey-web-maxkey:bootJar \
    :maxkey-webs:maxkey-web-mgt:bootJar
fi
build_backend "$MAXKEY_AUTH_IMAGE" "$ROOT/maxkey-webs/maxkey-web-maxkey"
build_backend "$MAXKEY_MGT_IMAGE" "$ROOT/maxkey-webs/maxkey-web-mgt"

echo "4/8 构建两个前端镜像"
build_frontend "$MAXKEY_AUTH_WEB_IMAGE" "$ROOT/maxkey-web-frontend/maxkey-web-ng"
build_frontend "$MAXKEY_MGT_WEB_IMAGE" "$ROOT/maxkey-web-frontend/maxkey-web-mgt-ng"

echo "5/8 启动 MySQL、Redis、Postgres 和 Chatwoot"
compose up -d --wait --wait-timeout 300 \
  maxkey-mysql maxkey-redis chatwoot-postgres chatwoot-redis
compose up -d chatwoot-rails chatwoot-sidekiq
wait_http "Chatwoot" \
  "${DOCKER[@]}" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T chatwoot-rails \
  ruby -e 'require "net/http"; u=URI("http://127.0.0.1:3000/api"); Net::HTTP.start(u.host, u.port, open_timeout: 3, read_timeout: 5) {|http| puts http.get(u.request_uri).code}' \
  || {
    compose logs --tail 120 chatwoot-rails >&2 || true
    exit 1
  }

echo "6/8 创建 Chatwoot 账号和网站收件箱"
bootstrap_chatwoot

echo "7/8 启动 MaxKey 后端和两个前端"
compose up -d maxkey-auth maxkey-mgt maxkey-auth-web maxkey-mgt-web
wait_http "MaxKey 认证服务" \
  "${DOCKER[@]}" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T maxkey-auth \
  curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:9527/sign/ \
  || {
    compose logs --tail 120 maxkey-auth >&2 || true
    exit 1
  }

echo "8/8 启动客服入口和 Nginx"
ensure_web_ports
compose up -d --build agent-bridge
compose up -d
compose ps

cat <<EOF

部署命令已完成。接下来在浏览器里做这三件事：

1. 打开 http://${ADMIN_HOST}/maxkey-mgt/ ，用 admin / maxkey 登录，马上修改密码。
2. 注册 OIDC 客户端：协议 OAuth 2.0，授权类型 authorization_code，
   回调 http://${AGENT_HOST}/callback ，范围 openid profile email read ，机构 ${MAXKEY_INST_ID}。
   客户端 ID 是 ${MAXKEY_CLIENT_ID}。明文密钥在 docs/deploy/.env 的 MAXKEY_CLIENT_SECRET。
3. 注册完成后重启客服入口：
   cd $ROOT
   ${DOCKER[*]} compose --env-file docs/deploy/.env -f docs/deploy/compose.yaml up -d agent-bridge

然后按 docs/服务器部署.md 验收四个地址。代码有变更时执行 bash docs/deploy/deploy.sh --rebuild。
EOF
