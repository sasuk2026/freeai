#!/usr/bin/env python3.11
# -*- coding: utf-8 -*-
"""
FreeLLM Hub — 免 key 公益大模型聚合网关
================================================
把多个「匿名免 key、OpenAI 兼容」的公益推理端点聚合成一个统一地址，
任何支持 OpenAI 协议的客户端 / SDK / 软件只要改 base_url 即可调用。

· 不需要任何 API Key（上游本身免 key）
· GET  /v1/models              模型列表（OpenAI 格式）
· POST /v1/chat/completions    对话补全（支持 stream / 自动故障转移）
· GET  /health                 健康状态
· GET  /                       网页控制台
· GET  /catalog.json           完整实测数据

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
TIMEOUT = int(os.environ.get("HUB_TIMEOUT", "60"))


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
             "verified": bool(e.get("ok"))}
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


def call_upstream(c, body):
    url = c["base"] + c["path"] if c["path"] else c["base"]
    payload = dict(body)
    payload["model"] = c["model"]
    t0 = time.time()
    r = requests.post(url, json=payload, headers=HEADERS, timeout=TIMEOUT, stream=bool(body.get("stream")))
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

    # 流式：直接透传第一个可用上游的 SSE
    if body.get("stream"):
        for c in build_chain(entry):
            try:
                r, _ = call_upstream(c, body)
                if r.status_code != 200:
                    continue
                def gen():
                    for chunk in r.iter_content(chunk_size=512):
                        if chunk:
                            yield chunk
                return Response(stream_with_context(gen()),
                                content_type=r.headers.get("Content-Type", "text/event-stream"),
                                headers={"Cache-Control": "no-cache", "X-Upstream": c["site"]})
            except Exception:
                continue
        return jsonify({"error": {"message": "all upstreams failed", "type": "upstream_error"}}), 502

    # 非流式：逐上游尝试，出字即返回
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
