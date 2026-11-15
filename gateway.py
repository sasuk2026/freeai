#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
FreeLLM Hub — 免 key 公益大模型聚合网关
================================================
把多个「匿名免 key、OpenAI 兼容」的公益推理端点聚合成一个统一地址，
任何支持 OpenAI 协议的客户端 / SDK / 软件只要改 base_url 即可调用。

· 不需要任何 API Key（上游本身免 key）
· GET  /v1/models              模型列表（OpenAI 格式，含 reasoning / safeguard 标注）
· POST /v1/chat/completions    对话补全（支持 stream / 自动故障转移 / 思考开关）
· GET  /health                 健康状态
· GET  /                       网页控制台
· GET  /catalog.json           完整实测数据

思考模式开关（客户端自定义参数，放在请求 body 顶层）：
    "think": true   → 强制开启思考（推理模型）
    "think": false  → 强制关闭思考（推理模型关闭思维链，直出答案）
    不传 / null     → 跟随上游默认行为
  关闭时会自动按模型家族翻译成上游认识的参数（enable_thinking /
  chat_template_kwargs / reasoning / thinking_config / reasoning_effort 等）。

部署：python3 gateway.py   （默认 0.0.0.0:$PORT，PORT 缺省 8000）
依赖：flask, requests
"""
import json, os, time, threading
from flask import Flask, request, Response, jsonify, stream_with_context
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CATALOG_PATH = os.path.join(BASE_DIR, "catalog.json")

app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")

# 可选：给网关自己加一道访问口令（环境变量），不设置则完全开放
ACCESS_TOKEN = os.environ.get("HUB_ACCESS_TOKEN", "")


@app.after_request
def add_cors(resp):
    # 允许任意客户端/网页跨域调用本网关
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@app.route("/v1/chat/completions", methods=["OPTIONS"])
@app.route("/v1/models", methods=["OPTIONS"])
def options_preflight():
    return Response(status=204)


HEADERS = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
# 连接超时（握手）与读取超时（首字节 + 流式读取）分开，避免慢模型被掐断
CONNECT_TIMEOUT = int(os.environ.get("HUB_CONNECT_TIMEOUT", "15"))
TIMEOUT = int(os.environ.get("HUB_TIMEOUT", "180"))
# 流式模式下，若上游超过这么久没推任何数据，主动发一条 SSE 注释心跳，防止代理层（Render/Cloudflare）无数据超时掐断
HEARTBEAT_IDLE = int(os.environ.get("HUB_HEARTBEAT_IDLE", "15"))


def load_catalog():
    with open(CATALOG_PATH, encoding="utf-8") as f:
        return json.load(f)


CATALOG = load_catalog()
INDEX = {e["id"]: e for e in CATALOG}

# 运行期健康表：id -> {last_ok, latency, fails}
HEALTH = {e["id"]: {"last_ok": bool(e.get("ok")), "latency": e.get("latency", 0), "fails": 0}
          for e in CATALOG}
LOCK = threading.Lock()


def check_auth():
    if not ACCESS_TOKEN:
        return True
    h = request.headers.get("Authorization", "")
    return h == "Bearer " + ACCESS_TOKEN or request.args.get("key") == ACCESS_TOKEN


def model_list():
    return [{"id": e["id"], "object": "model", "created": 1794268800,
             "owned_by": e["site"],
             "type": e.get("type"), "note": e.get("note", ""),
             "verified": bool(e.get("ok")),
             "reasoning": bool(e.get("reasoning")),
             "think_controllable": bool(e.get("think_controllable")),
             "safeguard": e.get("safeguard", "未知")}
            for e in CATALOG]


def build_chain(entry):
    """为该模型构造候选上游链：主上游 + 别名兜底 + 全局热备"""
    chain = [{"site": entry["site"], "base": entry["upstream_base"],
              "path": entry["upstream_path"], "model": entry["upstream_model"]}]
    for fb in entry.get("fallback", []):
        chain.append({"site": fb["site"], "base": fb["base"], "path": fb["path"], "model": fb["model"]})
    # 全局热备：任意请求失败后依次尝试这些已实测可用的通用上游
    for gid in ("openai-fast", "kilo-auto/free", "free-ai"):
        if gid in INDEX and gid != entry["id"]:
            ge = INDEX[gid]
            chain.append({"site": ge["site"], "base": ge["upstream_base"],
                          "path": ge["upstream_path"], "model": ge["upstream_model"]})
    # 去重
    seen, out = set(), []
    for c in chain:
        k = (c["base"], c["path"], c["model"])
        if k not in seen:
            seen.add(k); out.append(c)
    return out


BAD = ["budget", "rate limit", "rate_limit", "api key used", "quota", "insufficient",
       "unavailable", "does not exist", "missing authorization", "auth_required",
       "top up", "please raise", "sign in"]


def looks_bad(text):
    t = (text or "").lower()
    return any(b in t for b in BAD)


# ---------------------------------------------------------------------------
# 思考模式开关：把网关自定义参数 think 翻译成各上游认识的「开/关思考」参数
# ---------------------------------------------------------------------------
REASONING_KEYS = ('reasoning', '-r1', 'r1-', '-think', 'thinking', 'kimi-k', 'step-3',
                  'step-3.', 'glm-5-think', 'o1', 'o3', 'claude-3-7', 'claude-3.5-sonnet',
                  'deepseek-r', 'qwq', 'qwen3-thinking', 'nemotron-3-nano-omni',
                  'minimax-m2.7', 'dots-3', 'ernie', 'hunyuan', 'glm-5', 'kimi-k2',
                  'qwen3-max', 'qwen3.5', 'qwen3', 'gpt-oss', 'nemotron-3.5')


def is_reasoning(model_id):
    """粗略判断某模型是否为「推理 / 思考类」模型（有思维链）"""
    m = (model_id or "").lower()
    return any(k in m for k in REASONING_KEYS)


# 这些家族的推理模型确认支持 enable_thinking / chat_template_kwargs 关闭思考；
# gemini 用 thinking_config；其余推理模型（step / minimax / dots / 部分厂商）上游不一定提供关闭开关
_CTL = ('qwen3', 'deepseek-r', 'kimi-k', 'glm-5-think', 'qwq', 'gpt-oss')


def think_controllable(model_id):
    """该推理模型的思考是否可通过网关关闭（上游支持关思考）"""
    m = (model_id or "").lower()
    if any(k in m for k in _CTL):
        return True
    if "gemini" in m:
        return True
    return False


def apply_think(body, model_id, think):
    """把 think 开关翻译为上游参数，直接就地修改 body。think=None 则不干预。"""
    # 规范化：客户端可能传字符串 "true"/"false"
    if isinstance(think, str):
        s = think.strip().lower()
        think = True if s in ("true", "1", "yes", "on") else (False if s in ("false", "0", "no", "off") else None)
    if think is None:
        return
    m = (model_id or "").lower()
    if not is_reasoning(model_id):
        # 非推理模型没有思维链，思考开关无意义，原样放行（不污染 body）
        return
    ctrl = think_controllable(model_id)
    if think:
        # 开启思考（尽量用通用 + 特定参数）
        if ctrl:
            body["enable_thinking"] = True
            ct = dict(body.get("chat_template_kwargs") or {})
            ct["enable_thinking"] = True
            body["chat_template_kwargs"] = ct
        body["reasoning_effort"] = "medium"
    else:
        # 关闭思考：推理模型直出答案，不输出思维链
        if ctrl:
            body["enable_thinking"] = False
            ct = dict(body.get("chat_template_kwargs") or {})
            ct["enable_thinking"] = False
            body["chat_template_kwargs"] = ct
        if "gemini" in m:
            body["thinking_config"] = {"thinking_budget": 0, "include_thoughts": False}
        # 通用兜底（OpenAI o-series / OpenRouter reasoning）；对不可控模型尽力而为
        body["reasoning_effort"] = "none"
        body["reasoning"] = {"enabled": False}


def call_upstream(c, body):
    url = c["base"] + c["path"] if c["path"] else c["base"]
    payload = dict(body)
    payload["model"] = c["model"]
    t0 = time.time()
    r = requests.post(url, json=payload, headers=HEADERS,
                      timeout=(CONNECT_TIMEOUT, TIMEOUT),
                      stream=bool(body.get("stream")))
    return r, round(time.time() - t0, 2)


@app.route("/v1/models", methods=["GET"])
def v1_models():
    if not check_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"object": "list", "data": model_list()})


@app.route("/health", methods=["GET"])
@app.route("/healthz", methods=["GET"])
def health():
    with LOCK:
        snap = dict(HEALTH)
    ok = sum(1 for v in snap.values() if v["last_ok"])
    return jsonify({"status": "ok", "models": len(CATALOG), "healthy_recent": ok,
                    "detail": snap})


@app.route("/catalog.json", methods=["GET"])
def catalog_json():
    return jsonify(CATALOG)


@app.route("/v1/chat/completions", methods=["POST"])
def chat():
    if not check_auth():
        return jsonify({"error": {"message": "unauthorized", "type": "auth_error"}}), 401
    body = request.get_json(force=True, silent=True) or {}
    mid = body.get("model", "auto")
    entry = INDEX.get(mid) or INDEX.get("auto") or CATALOG[0]

    # 思考模式开关（网关自定义参数，取出后翻译，不作为参数名污染上游）
    think = body.pop("think", None)
    apply_think(body, entry["id"], think)

    # 流式：逐上游尝试；一旦某上游开始吐数据就转发；若该上游中途断开，自动切到下一个上游继续，
    #       期间用 SSE 注释心跳保活，避免代理层无数据超时掐断连接。
    if body.get("stream"):
        chain = build_chain(entry)

        def gen():
            last = time.time()
            for c in chain:
                try:
                    r, _ = call_upstream(c, body)
                    if r.status_code != 200:
                        continue
                    for chunk in r.iter_content(chunk_size=1024):
                        if chunk:
                            last = time.time()
                            yield chunk
                        elif time.time() - last > HEARTBEAT_IDLE:
                            yield b": keepalive\n\n"
                            last = time.time()
                    return  # 该上游正常结束（已收到 [DONE]），成功完成
                except Exception:
                    yield (": upstream %s dropped, switching...\n\n" % c["site"]).encode()
                    last = time.time()
                    continue
            yield b": all upstreams failed\n\n"

        return Response(stream_with_context(gen()),
                        content_type="text/event-stream",
                        headers={"Cache-Control": "no-cache",
                                 "X-Upstream": "multi",
                                 "X-Accel-Buffering": "no"})

    # 非流式：逐上游尝试，出字即返回；单上游失败自动换下一个
    errors = []
    for c in build_chain(entry):
        try:
            r, lat = call_upstream(c, body)
            if r.status_code != 200:
                errors.append(f'{c["site"]}:{r.status_code}')
                continue
            j = r.json()
            if j.get("error"):
                errors.append(f'{c["site"]}:{str(j["error"])[:80]}')
                continue
            ch = (j.get("choices") or [{}])[0]
            msg = ch.get("message") or {}
            content = msg.get("content") or msg.get("reasoning") or msg.get("reasoning_content")
            if not content or looks_bad(content):
                errors.append(f'{c["site"]}:empty_or_limited')
                continue
            j["model"] = mid                      # 对外保持用户填的模型名
            j["hub_upstream"] = c["site"]        # 标注真实上游，便于排障
            j["hub_latency"] = lat
            with LOCK:
                HEALTH[mid]["last_ok"] = True
                HEALTH[mid]["latency"] = lat
                HEALTH[mid]["fails"] = 0
            return jsonify(j)
        except Exception as ex:
            errors.append(f'{c["site"]}:{str(ex)[:60]}')
            continue

    with LOCK:
        HEALTH[mid]["last_ok"] = False
        HEALTH[mid]["fails"] += 1
    return jsonify({"error": {"message": "all upstreams failed", "type": "upstream_error",
                              "tried": errors}}), 502


@app.route("/")
def index():
    r = app.send_static_file("index.html")
    r.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return r


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    verified = sum(1 for e in CATALOG if e.get("ok"))
    print(f"[freeai] {len(CATALOG)} 模型 / {verified} 实测通过  ->  http://0.0.0.0:{port}/v1")
    app.run(host="0.0.0.0", port=port, threaded=True)
