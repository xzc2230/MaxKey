import http from "node:http";
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { URL } from "node:url";

function loadEnv(path) {
  const env = {};
  if (!existsSync(path)) {
    return env;
  }
  for (const line of readFileSync(path, "utf8").split(/\r?\n/)) {
    if (!line || line.startsWith("#") || !line.includes("=")) {
      continue;
    }
    const index = line.indexOf("=");
    env[line.slice(0, index)] = line.slice(index + 1);
  }
  return env;
}

const fileEnv = loadEnv(new URL("./bridge.env", import.meta.url));

function setting(name, fallback) {
  const value = process.env[name] || fileEnv[name] || fallback;
  if (!value) {
    throw new Error(`Missing ${name}`);
  }
  return value;
}

const port = Number(setting("PORT", "3011"));
const bindHost = setting("BIND_HOST", "127.0.0.1");
const publicBase = setting("PUBLIC_BASE", "http://127.0.0.1:3011").replace(/\/$/, "");
const issuer = setting("MAXKEY_ISSUER", "http://127.0.0.1:9527/sign").replace(/\/$/, "");
const publicIssuer = setting("MAXKEY_PUBLIC_ISSUER", issuer).replace(/\/$/, "");
const redirectUri = `${publicBase}/callback`;
const instId = setting("MAXKEY_INST_ID", "1");
const dataDir = process.env.DATA_DIR || fileEnv.DATA_DIR;
if (dataDir) {
  mkdirSync(dataDir, { recursive: true });
}
const mapPath = dataDir ? `${dataDir}/agents.json` : new URL("./agents.json", import.meta.url);
const agents = existsSync(mapPath) ? JSON.parse(readFileSync(mapPath, "utf8")) : {};

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
}

function required(name) {
  return setting(name);
}

function saveAgents() {
  writeFileSync(mapPath, JSON.stringify(agents, null, 2));
}

function page(status, title, body) {
  return {
    status,
    headers: { "content-type": "text/html; charset=utf-8" },
    body: `<!doctype html><meta charset="utf-8"><title>${title}</title><body style="font-family:sans-serif;padding:32px"><h1>${title}</h1><p>${body}</p></body>`
  };
}

async function chatwoot(path, options = {}) {
  const apiBase = setting("CHATWOOT_API_BASE", required("CHATWOOT_BASE")).replace(/\/$/, "");
  const response = await fetch(`${apiBase}${path}`, {
    ...options,
    headers: {
      accept: "application/json",
      "content-type": "application/json",
      api_access_token: required("CHATWOOT_PLATFORM_TOKEN"),
      ...(options.headers || {})
    }
  });
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { raw: text };
  }
  if (!response.ok) {
    throw new Error(`${response.status} ${path} ${text}`);
  }
  return data;
}

async function ensureAgent(profile) {
  const sub = String(profile.sub || profile.id || profile.userId || profile.username);
  const email = profile.email || `${sub}@agents.local`;
  const name = profile.name || profile.displayName || profile.username || `agent-${sub}`;
  if (!agents[sub]) {
    const created = await chatwoot("/platform/api/v1/users", {
      method: "POST",
      body: JSON.stringify({
        name,
        display_name: name,
        email,
        password: required("CHATWOOT_AGENT_PASSWORD"),
        custom_attributes: { maxkey_sub: sub, institution: String(profile.institution || "") }
      })
    });
    agents[sub] = { id: created.id, email };
    saveAgents();
  }
  const userId = agents[sub].id;
  const accountId = required("CHATWOOT_ACCOUNT_ID");
  await chatwoot(`/platform/api/v1/accounts/${accountId}/account_users`, {
    method: "POST",
    body: JSON.stringify({ user_id: userId, role: "administrator" })
  }).catch((error) => {
    if (!String(error.message).includes("422") && !String(error.message).includes("already")) {
      throw error;
    }
  });
  const login = await chatwoot(`/platform/api/v1/users/${userId}/login`);
  if (!login.url) {
    throw new Error("Chatwoot did not return an agent login URL");
  }
  return login.url;
}

async function handle(requestUrl) {
  const url = new URL(requestUrl, `${publicBase}/`);
  if (url.pathname === "/") {
    const authorize = new URL(`${publicIssuer}/authz/oauth/v20/authorize`);
    authorize.searchParams.set("response_type", "code");
    authorize.searchParams.set("client_id", required("MAXKEY_CLIENT_ID"));
    authorize.searchParams.set("redirect_uri", redirectUri);
    authorize.searchParams.set("scope", "openid profile email read");
    return { status: 302, headers: { location: authorize.toString() }, body: "" };
  }
  if (url.pathname === "/visitor" || url.pathname === "/customer") {
    const token = required("CHATWOOT_WEBSITE_TOKEN");
    const base = required("CHATWOOT_BASE");
    const identified = url.pathname === "/customer";
    const customerId = (url.searchParams.get("id") || "customer-1001").slice(0, 64);
    const customerName = (url.searchParams.get("name") || "张三").slice(0, 64);
    const sourceSystem = (url.searchParams.get("system") || "order-system").slice(0, 64);
    const identifier = `${sourceSystem}:${customerId}`;
    const identityScript = identified
      ? `window.addEventListener("chatwoot:ready",function(){window.$chatwoot.setUser(${JSON.stringify(identifier)},{name:${JSON.stringify(customerName)},email:${JSON.stringify(`${customerId}@customer.test`)}});window.$chatwoot.setCustomAttributes({source_system:${JSON.stringify(sourceSystem)},customer_id:${JSON.stringify(customerId)}});});`
      : "";
    const heading = identified ? `${escapeHtml(sourceSystem)} 已登录客户` : "访客咨询";
    const detail = identified
      ? `当前客户 ${escapeHtml(customerName)}（${escapeHtml(identifier)}）。右下角打开聊天窗口，这条会话会带着业务客户身份进入客服收件箱。`
      : "右下角打开聊天窗口，给客服发一条消息。";
    const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>${heading}</title></head><body><h1>${heading}</h1><p>${detail}</p>
<script>
  window.chatwootSettings = { position: "right", locale: "zh_CN", type: "standard" };
  ${identityScript}
  (function(d,t){var g=d.createElement(t),s=d.getElementsByTagName(t)[0];g.src="${base}/packs/js/sdk.js";g.async=true;s.parentNode.insertBefore(g,s);g.onload=function(){window.chatwootSDK.run({websiteToken:"${token}",baseUrl:"${base}"})}})(document,"script");
</script></body></html>`;
    return { status: 200, headers: { "content-type": "text/html; charset=utf-8" }, body: html };
  }
  if (url.pathname === "/callback") {
    const code = url.searchParams.get("code");
    if (!code) {
      return page(400, "登录未完成", url.searchParams.get("error_description") || url.searchParams.get("error") || "MaxKey 没有返回授权码");
    }
    const body = new URLSearchParams({
      grant_type: "authorization_code",
      code,
      redirect_uri: redirectUri,
      client_id: required("MAXKEY_CLIENT_ID"),
      client_secret: required("MAXKEY_CLIENT_SECRET")
    });
    const tokenResponse = await fetch(`${issuer}/authz/oauth/v20/token`, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body
    });
    const tokenText = await tokenResponse.text();
    if (!tokenResponse.ok) {
      return page(502, "换取令牌失败", tokenText);
    }
    const token = JSON.parse(tokenText);
    const accessToken = token.access_token || token.value;
    if (!accessToken) {
      return page(502, "换取令牌失败", "令牌响应里没有 access_token");
    }
    const infoResponse = await fetch(`${issuer}/api/connect/v10/userinfo`, {
      headers: { authorization: `Bearer ${accessToken}` }
    });
    const infoText = await infoResponse.text();
    if (!infoResponse.ok) {
      return page(502, "读取客服身份失败", infoText);
    }
    const profile = JSON.parse(infoText);
    if (profile.error) {
      return page(502, "读取客服身份失败", infoText);
    }
    if (String(profile.institution || "") !== instId) {
      return page(403, "租户不匹配", `当前入口只接受 MaxKey 机构 ${escapeHtml(instId)}，实际机构是 ${escapeHtml(profile.institution || "空")}。`);
    }
    const destination = await ensureAgent(profile);
    return { status: 302, headers: { location: destination }, body: "" };
  }
  return page(404, "未找到页面", "打开 <a href=\"/\">客服入口</a> 或 <a href=\"/visitor\">访客入口</a>");
}

const server = http.createServer((req, res) => {
  handle(req.url).then((result) => {
    res.writeHead(result.status, result.headers);
    res.end(result.body);
  }).catch((error) => {
    res.writeHead(500, { "content-type": "text/plain; charset=utf-8" });
    res.end(String(error && error.stack || error));
  });
});

server.listen(port, bindHost, () => {
  console.log(`bridge listening on http://${bindHost}:${port}`);
});
