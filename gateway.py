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
# 连接超时（握手）与单上游读取超时分开。
# 关键：单上游读取超时必须短——公益站挂起时若傻等 180s，客户端(常 60s)早就断开了。
# 缩短后可在客户端超时前快速切到下一个上游。
CONNECT_TIMEOUT = int(os.environ.get("HUB_CONNECT_TIMEOUT", "8"))
READ_TIMEOUT = int(os.environ.get("HUB_READ_TIMEOUT", "22"))      # 单上游首字节/每次读取上限
STREAM_READ_TIMEOUT = int(os.environ.get("HUB_STREAM_READ_TIMEOUT", "25"))  # 流式首字节/读取上限(有心跳保活,不必太长)
CHAIN_BUDGET = int(os.environ.get("HUB_CHAIN_BUDGET", "48"))      # 整条链总时间预算(秒)，保证及时给答复
# 严格模式(设 1 开启，默认关闭)：连「技术救急兜底」都禁用，宁可失败也绝不串模型。
# 默认关闭 = 稳定优先：仅在【技术失败】时才跨站点救急；【内容拒答】一律如实返回、绝不切换。
# 这样既不会「换模型也没用/全灭」，也不会「所有敏感请求收敛成同一句 I'm sorry」。
STRICT_MODEL = os.environ.get("HUB_STRICT_MODEL", "0") == "1"
# 流式模式下，若上游超过这么久没推任何数据，主动发一条 SSE 注释心跳，防止代理层（Render/Cloudflare）无数据超时掐断
HEARTBEAT_IDLE = int(os.environ.get("HUB_HEARTBEAT_IDLE", "15"))
# 流式「内容空闲收尾」：已吐出正文后，若这么久没再吐新正文，就主动结束该上游、
# 用已有内容交付。专治上游吐一句就挂起只发心跳的假活连接——否则会一直卡到客户端超时。
CONTENT_IDLE = int(os.environ.get("HUB_CONTENT_IDLE", "6"))

# 可安全切换/重试的「技术性失败」HTTP 状态：模型并没有给出回答，换端点不会串掉用户意图。
# 注意：200 + 拒答文本(I'm sorry / can't comply)【不在此列】——那是模型自己的回答，必须如实返回。
RETRY_STATUS = {402, 408, 409, 425, 429, 500, 502, 503, 504}
# 单端点对瞬时限流的重试次数（402/429 常是瞬时额度，短退避后重试即过）
UPSTREAM_RETRIES = int(os.environ.get("HUB_UPSTREAM_RETRIES", "2"))
# 跨站点「技术救急」端点：仅当所选模型的全部自身上游都【技术失败】时才启用。
# 刻意分散在 4 个不同站点 + 选开源/弱护栏底座，避免限流集中，也避免收敛到带强护栏的商业底座。
HOTSPARE_IDS = ("openai-fast",                   # pollination  实测并发最稳,首选救火
                "gpt-oss",                        # pollinations  弱
                "kilo-auto/free",                 # kilo          实测稳
                "Meta-Llama-3_3-70B-Instruct",   # ovhcloud      开源 Llama
                "glm-5.3-flash")                 # llm7
# 注意：曾把 blockrun 的 nvidia/nemotron-3-ultra-550b 放进救急层，但它又慢又容易
# 限流(单跳可达 50s+)，自己就是拖垮整条链的元凶，已移除。救急层只留快而稳的站点。


def load_catalog():
    with open(CATALOG_PATH, encoding="utf-8") as f:
        base = json.load(f)
    # 合并「匿名免 key 真模型」（AI Horde / Yqcloud 等非 OpenAI 协议端点，由网关代转）
    anon_path = os.path.join(BASE_DIR, "catalog_anon_real.json")
    if os.path.exists(anon_path):
        try:
            with open(anon_path, encoding="utf-8") as f:
                anon = json.load(f)
            seen = {e["id"] for e in base}
            for e in anon:
                if e["id"] not in seen:
                    base.append(e)
        except Exception as ex:
            print("[freeai] anon catalog load failed:", ex)
    return base


CATALOG = load_catalog()
INDEX = {e["id"]: e for e in CATALOG}

# 运行期健康表：id -> {last_ok, latency, fails}
HEALTH = {e["id"]: {"last_ok": bool(e.get("ok")), "latency": e.get("latency", 0), "fails": 0}
          for e in CATALOG}
LOCK = threading.Lock()

# 诊断环形缓冲：记录最近若干次请求的逐跳过程，供 /diag 读取排障（内存中，重启即清）
DIAG = []
DIAG_LOCK = threading.Lock()
DIAG_MAX = 60

def diag_add(rec):
    try:
        with DIAG_LOCK:
            DIAG.append(rec)
            if len(DIAG) > DIAG_MAX:
                del DIAG[:len(DIAG) - DIAG_MAX]
    except Exception:
        pass


def check_auth():
    if not ACCESS_TOKEN:
        return True
    h = request.headers.get("Authorization", "")
    return h == "Bearer " + ACCESS_TOKEN or request.args.get("key") == ACCESS_TOKEN


def model_list():
    # 暂时不可用的模型（disabled=true）不对外暴露，避免客户端调到死上游
    out = []
    for e in CATALOG:
        if e.get("disabled"):
            continue
        m = {"id": e["id"], "object": "model", "created": 1794268800,
             "owned_by": e["site"],
             "type": e.get("type"), "note": e.get("note", ""),
             "verified": bool(e.get("ok")),
             "reasoning": bool(e.get("reasoning")),
             "think_controllable": bool(e.get("think_controllable")),
             "safeguard": e.get("safeguard", "未知"),
             "adapter": e.get("adapter")}
        # 参数量（按官方模型名标注，仅真模型有；名字兼容别名不标，避免假名字冒充大模型）
        if e.get("params"):
            m["params"] = e["params"]
        out.append(m)
    return out


def build_chain(entry):
    """为该模型构造候选上游链，分两层：

    · 自身层(relief=False)：主上游 + 同模型备用端点。选什么模型就用什么模型，
      一旦这些端点给出【任何有效回答】(哪怕是模型自己的拒答 I'm sorry)，立即
      如实返回，绝不往后切 —— 这保证「选啥是啥」且不会把敏感请求收敛到统一底座。
    · 救急层(relief=True)：仅当自身层全部【技术失败】(402/429/5xx/超时/断连/空流)
      时才按顺序走到。刻意分散到 4 个不同站点，专治「某个公益站整体限流导致
      换什么模型都全灭」。救急只在「模型压根没回答」时启用，不扭曲内容。

    设 HUB_STRICT_MODEL=1 可完全禁用救急层（宁可失败也绝不串模型）。
    """
    target = entry["upstream_model"]   # 用户所选模型在上游的真实名字
    own = [{"site": entry["site"], "base": entry["upstream_base"],
            "path": entry["upstream_path"], "model": target, "relief": False}]
    # 1) 同一模型的备用上游（最优兜底，不串模型）
    for fb in entry.get("fallback", []):
        if fb.get("model") == target:
            own.append({"site": fb["site"], "base": fb["base"], "path": fb["path"],
                        "model": fb["model"], "relief": False})
    # 2) 同模型的其它已实测站点（历史探测确认能跑这个名字的）
    for gid in ("gpt-oss-20b", "gpt-oss-120b", "Qwen2.5-VL-72B-Instruct", "Qwen3.5-397B-A17B"):
        if gid in INDEX and gid != entry["id"] and INDEX[gid].get("upstream_model") == target:
            ge = INDEX[gid]
            own.append({"site": ge["site"], "base": ge["upstream_base"],
                        "path": ge["upstream_path"], "model": ge["upstream_model"], "relief": False})

    def dedup(chain):
        seen, out = set(), []
        for c in chain:
            k = (c["base"], c["path"], c["model"])
            if k not in seen:
                seen.add(k); out.append(c)
        return out

    own = dedup(own)
    if STRICT_MODEL:
        return own

    # 3) 跨站点技术救急：分散在 4 个不同站点、开源/弱护栏底座，避免限流集中与统一拒答。
    #    按模型 id 稳定打散「救急起点」，让不同模型的救急第一站不同 —— 这样即使多个
    #    模型的自身上游同时技术失败，也不会齐刷刷落到同一个救急端点吐同样的拒答。
    relief = []
    order = list(HOTSPARE_IDS)
    if order:
        rot = sum(ord(ch) for ch in entry["id"]) % len(order)
        order = order[rot:] + order[:rot]
    for gid in order:
        ge = INDEX.get(gid)
        if ge and not ge.get("disabled"):
            relief.append({"site": ge["site"], "base": ge["upstream_base"],
                           "path": ge["upstream_path"], "model": ge["upstream_model"], "relief": True})
    # 4) catalog 里其它已实测可用的跨模型 fallback（更靠后），
    #    但剔除 openai-fast / openrouter/free 这类「通用护栏底座」——它们正是过去
    #    「所有敏感请求收敛成同一句 I'm sorry」的元凶，绝不进救急层。
    GENERIC = ("openai-fast", "openrouter/free", "free-ai", "kilo-auto/free", "auto", "free")
    for fb in entry.get("fallback", []):
        m = fb.get("model")
        if m and m != target and m not in GENERIC:
            relief.append({"site": fb["site"], "base": fb["base"], "path": fb["path"],
                           "model": m, "relief": True})
    # 救急层去重，且不与自身层重复
    seen = {(c["base"], c["path"], c["model"]) for c in own}
    out = list(own)
    for c in dedup(relief):
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
    m = (model_id or "").lower()
    if not is_reasoning(model_id):
        # 非推理模型没有思维链，思考开关无意义，原样放行（不污染 body）
        return
    # 推理模型(max_tokens 预算)：无论开关思考，都要给思维链留出额度。
    # 公益上游对 blockrun 550B/120B 这类大推理模型，思维链动辄几百上千 token；
    # 客户端 max_tokens 常只有几十~二百，全被思维链吃光 → 正文吐不出 → finish:length
    # → 网关判空流切链 → 多轮 Agent 高频调用下就是「全部不可用」。
    # 抬到至少 512(封顶 2048)，既保证正文吐得出，又不会无限拉长拖过客户端超时。
    try:
        _mt = int(body.get("max_tokens") or 0)
    except (TypeError, ValueError):
        _mt = 0
    if 0 < _mt < 512:
        body["max_tokens"] = 512
    if think is None:
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
        # max_tokens 预算已在函数开头对所有推理模型统一抬升，此处不再重复。


def normalize_think(val):
    """把客户端可能传的各种写法统一成 True/False/None：
       JSON 布尔、字符串 'true'/'false'、数字 1/0 都认。"""
    if val is None or val is True or val is False:
        return val
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ("true", "1", "yes", "on"):
            return True
        if s in ("false", "0", "no", "off"):
            return False
        return None
    if isinstance(val, (int, float)):
        return bool(val)
    return None


def detect_think(body, explicit=None):
    """从客户端请求里推断「关/开思考」意图，兼容各类 APP 的标准写法。

    返回 True=强制思考 / False=强制关闭 / None=不干预。
    优先级：网关自定义 think > OpenAI reasoning_effort > enable_thinking >
            chat_template_kwargs.enable_thinking > reasoning.enabled >
            Google thinking_config.thinking_budget。
    只有明确表达「关」或「开」才返回值，否则 None（保持模型默认行为）。
    """
    # 1) 网关自有参数最高优先级
    if explicit is not None:
        v = normalize_think(explicit)
        if v is not None:
            return v

    # 1.5) 灵犀(kdocs lingxi)风格：顶层 thinking 字段
    #      取值 auto/enabled/disabled（也可能布尔/字符串 true/false）。
    #      这是「关思考关不掉 + 两百多模型全不可用」的真凶：过去既没识别也没
    #      清理，thinking:"disabled" 被原样透传给不认该字段的上游 → 全部失败。
    if "thinking" in body:
        tv = body.get("thinking")
        ts = str(tv).strip().lower() if tv is not None else ""
        if ts in ("disabled", "off", "none", "false", "0", "disable", "close"):
            return False
        if ts in ("enabled", "on", "true", "1", "enable", "open"):
            return True
        # "auto" 或其它：不干预，跟随模型默认
        v = normalize_think(tv)
        if v is not None:
            return v

    # 2) OpenAI / OpenRouter 风格：reasoning_effort
    if "reasoning_effort" in body:
        val = body.get("reasoning_effort")
        s = str(val).strip().lower() if val is not None else ""
        if s in ("none", "off", "minimal", "0", "false", "disable", "disabled"):
            return False
        if s in ("low", "medium", "high", "xhigh", "auto", "on", "enable", "enabled"):
            return True

    # 3) 常见的 enable_thinking 顶层布尔
    if "enable_thinking" in body:
        v = normalize_think(body.get("enable_thinking"))
        if v is not None:
            return v

    # 4) Qwen / vLLM 风格：chat_template_kwargs.enable_thinking
    ctk = body.get("chat_template_kwargs")
    if isinstance(ctk, dict) and "enable_thinking" in ctk:
        v = normalize_think(ctk.get("enable_thinking"))
        if v is not None:
            return v

    # 5) reasoning: {"enabled": bool}
    rs = body.get("reasoning")
    if isinstance(rs, dict) and "enabled" in rs:
        v = normalize_think(rs.get("enabled"))
        if v is not None:
            return v
    # 也兼容 reasoning 直接是布尔
    if isinstance(rs, bool):
        return rs

    # 6) Google Gemini 风格：thinking_config.thinking_budget == 0 表示关
    tc = body.get("thinking_config")
    if isinstance(tc, dict):
        if "include_thoughts" in tc:
            v = normalize_think(tc.get("include_thoughts"))
            if v is not None:
                return v
        if "thinking_budget" in tc:
            try:
                if int(tc.get("thinking_budget")) == 0:
                    return False
            except (ValueError, TypeError):
                pass

    return None


def final_usage_chunk(mid, stream_id=None, pt=1, ct=1):
    """生成严格客户端(灵犀等)可接受的「终态 usage」chunk —— 完全遵循 OpenAI 规范：
      · choices 必须是【空数组】[]：usage chunk 不携带任何 choice/finish_reason，
        这是官方 streaming 规范(usage 单独一块、choices 恒为空)。灵犀这类严格
        校验器见到 usage 块里塞了 finish_reason 会判 'invalid terminal usage'。
      · total_tokens > 0：0 会被判 omitted/invalid。
      · id 复用本流 stream_id，保证与前面 content chunk 一致。
      · 该 chunk 之后紧跟唯一一个 data: [DONE]。"""
    try:
        pt = max(1, int(pt or 0))
    except (TypeError, ValueError):
        pt = 1
    try:
        ct = max(1, int(ct or 0))
    except (TypeError, ValueError):
        ct = 1
    u = {"id": stream_id or ("chatcmpl-" + str(int(time.time() * 1000))),
         "object": "chat.completion.chunk",
         "created": int(time.time()),
         "model": mid,
         "choices": [],
         "usage": {"prompt_tokens": pt, "completion_tokens": ct,
                   "total_tokens": pt + ct}}
    return b"data: " + json.dumps(u, ensure_ascii=False).encode() + b"\n\n"


def sse_strip(line_bytes, strip_reasoning, model_alias=None):
    """处理一行 SSE：
    · strip_reasoning=True 时剥掉 delta 里的 reasoning 字段（关思考）。
    · model_alias 给定时，把上游返回的 model 字段改写成用户所选的模型名，
      避免兜底到别的上游后客户端看到「路由到别的模型」。
    · 上游自带 usage 一律吞掉，统一由网关在收尾生成规范 usage（total>0、id 一致）。"""
    s = line_bytes.strip()
    if not s:
        return None
    if s.startswith(b"data:"):
        payload = s[5:].strip()
        if payload == b"[DONE]":
            # 上游自带的 DONE 一律吞掉，最终由网关统一输出唯一一个规范 DONE，
            # 避免「双 DONE」让严格客户端(如灵犀)提前结束解析、误报 usage_missing。
            return None
        # 需要改写(关思考/改模型名)或检测到 usage 行时，解析后规范化
        need = strip_reasoning or model_alias or (b"usage" in payload)
        if need:
            try:
                o = json.loads(payload)
                if model_alias:
                    o["model"] = model_alias
                if strip_reasoning:
                    for ch in o.get("choices", []):
                        d = ch.get("delta", {})
                        for k in ("reasoning", "reasoning_content", "reasoning_details"):
                            d.pop(k, None)
                # 上游 usage 相关 chunk 全部吞掉：形状/数值/id 由网关统一接管，
                # 杜绝 total_tokens=0、空 choices、id 不一致等导致的 usage_missing。
                if o.get("usage"):
                    return None
                return b"data: " + json.dumps(o, ensure_ascii=False).encode() + b"\n\n"
            except Exception:
                return s + b"\n\n"
        return s + b"\n\n"
    return s + b"\n\n"


def sse_has_content(line_bytes):
    """判断一行 SSE 是否吐出了【非空 content】。用于区分：
    · 有效回答(含模型自己的拒答 I'm sorry / can't comply) → True，必须如实返回、绝不切换上游
    · 空流 / 只有 reasoning / 只有 role 首块 / 报错 JSON → False，视为技术失败可切换
    注意：拒答文本也是 content，会被判 True —— 这正是「不把敏感请求收敛到统一底座」的关键。"""
    s = line_bytes.strip()
    if not s.startswith(b"data:"):
        return False
    p = s[5:].strip()
    if not p or p == b"[DONE]":
        return False
    try:
        o = json.loads(p)
    except Exception:
        return False
    if o.get("error"):
        return False
    for ch in o.get("choices", []) or []:
        c = (ch.get("delta", {}) or {}).get("content")
        if c:
            return True
    return False


def call_upstream(c, body, read_timeout=None, retries=None):
    """调用单个上游。对瞬时限流(402/429/5xx)做短退避重试——公益站 402 常是
    瞬时额度，重试即过；这是消除「换模型也没用」的第一道。注意只对【HTTP 状态】
    重试，绝不对「200 但内容是拒答」重试（那是模型的回答，必须如实返回）。
    retries 可覆盖重试次数：流式要快速 failover，用 0（不重试，靠切上游顶）。"""
    url = c["base"] + c["path"] if c["path"] else c["base"]
    payload = dict(body)
    payload["model"] = c["model"]
    rt = read_timeout if read_timeout else (STREAM_READ_TIMEOUT if body.get("stream") else READ_TIMEOUT)
    t0 = time.time()
    last = None
    n_retry = UPSTREAM_RETRIES if retries is None else retries
    for attempt in range(n_retry + 1):
        last = requests.post(url, json=payload, headers=HEADERS,
                             timeout=(CONNECT_TIMEOUT, rt),
                             stream=bool(body.get("stream")))
        if last.status_code in RETRY_STATUS and attempt < n_retry:
            try:
                last.close()
            except Exception:
                pass
            time.sleep(0.4 * (attempt + 1))   # 0.4s / 0.8s 短退避
            continue
        break
    return last, round(time.time() - t0, 2)



# ---------------------------------------------------------------------------
# 匿名免 key「非 OpenAI 协议」真模型适配器
#   这些端点背后是真实、互不相同的底层模型，但协议不是 /v1/chat/completions：
#     · aihorde : AI Horde 志愿者算力，异步提交 + 轮询；apikey 用官方公开匿名
#                 标识 0000000000（非用户私有 Key，无需注册、无需自备）
#     · yqcloud : binjie 镜像，纯文本 POST，背后是阶跃星辰 Step 真模型
#   网关代做协议转换 + 匿名鉴权，用户填一个 model ID 即可，零配置。
# ---------------------------------------------------------------------------
ANON_API_KEY = "0000000000"
HORDE_API = "https://aihorde.net/api/v2"
YQCLOUD_URL = "https://api.binjie.fun/api/generateStream"
HORDE_BUDGET = int(os.environ.get("HUB_HORDE_BUDGET", "58"))
HORDE_POLL = float(os.environ.get("HUB_HORDE_POLL", "2.2"))


def _messages_to_prompt(messages):
    parts = []
    for m in messages or []:
        role = m.get("role", "user")
        c = m.get("content") or ""
        if isinstance(c, list):
            c = " ".join(str(x.get("text", "")) for x in c if isinstance(x, dict))
        c = str(c)
        if role == "system":
            parts.append("### System:\n" + c)
        elif role == "assistant":
            parts.append("### Response:\n" + c)
        else:
            parts.append("### Instruction:\n" + c)
    if not parts:
        parts.append("### Instruction:\nHello")
    return "\n\n".join(parts) + "\n\n### Response:\n"


def _last_user_text(messages):
    for m in reversed(messages or []):
        if m.get("role") == "user":
            c = m.get("content") or ""
            if isinstance(c, list):
                c = " ".join(str(x.get("text", "")) for x in c if isinstance(x, dict))
            return str(c)
    return _messages_to_prompt(messages)


def _clean_generation(text):
    text = (text or "").strip()
    for marker in ("### Instruction:", "### Response:", "### Note:",
                   "### Question:", "### System:"):
        i = text.find(marker, 1)
        if i > 0:
            text = text[:i]
    return text.strip()


def _call_yqcloud(entry, messages):
    payload = {
        "prompt": _last_user_text(messages),
        "userId": "#/chat/%d" % int(time.time() * 1000),
        "network": True, "system": "", "withoutContext": False, "stream": False,
    }
    h = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json",
         "accept": "application/json, text/plain, */*",
         "origin": "https://chat9.yqcloud.top", "referer": "https://chat9.yqcloud.top/"}
    r = requests.post(YQCLOUD_URL, json=payload, headers=h,
                      timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    if r.status_code != 200:
        return None, "yqcloud:%d" % r.status_code
    text = r.text.strip()
    if not text or "no permission" in text.lower():
        return None, "yqcloud:empty_or_denied"
    return text, "step (StepFun)"


def _call_horde(entry, messages, max_tokens):
    model = entry["upstream_model"]
    try:
        mt = max(32, min(int(max_tokens or 96), 512))
    except (TypeError, ValueError):
        mt = 96
    payload = {
        "prompt": _messages_to_prompt(messages),
        "params": {"max_length": mt, "max_context_length": 2048,
                   "temperature": 0.7, "top_p": 0.9},
        "models": [model],
    }
    h = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json",
         "apikey": ANON_API_KEY}
    wid = None
    for attempt in range(4):
        try:
            r = requests.post(HORDE_API + "/generate/text/async", json=payload,
                              headers=h, timeout=(CONNECT_TIMEOUT, 15))
        except Exception as ex:
            return None, "horde_submit:" + str(ex)[:50]
        if r.status_code in (200, 202):
            wid = r.json().get("id")
            break
        if r.status_code == 429:
            time.sleep(0.7)
            continue
        return None, "horde_submit:%d" % r.status_code
    if not wid:
        return None, "horde_submit:no_id"
    deadline = time.time() + HORDE_BUDGET
    while time.time() < deadline:
        time.sleep(HORDE_POLL)
        try:
            s = requests.get(HORDE_API + "/generate/text/status/" + wid,
                             headers={"User-Agent": "Mozilla/5.0"},
                             timeout=(CONNECT_TIMEOUT, 10)).json()
        except Exception:
            continue
        if s.get("faulted"):
            return None, "horde:faulted"
        if s.get("done"):
            gens = s.get("generations") or []
            if not gens:
                return None, "horde:no_generation"
            text = _clean_generation(gens[0].get("text"))
            if not text:
                return None, "horde:empty"
            return text, gens[0].get("model") or model
    return None, "horde:queue_timeout"


def _anon_result(entry, body):
    msgs = body.get("messages") or []
    if entry.get("adapter") == "yqcloud":
        return _call_yqcloud(entry, msgs)
    return _call_horde(entry, msgs, body.get("max_tokens"))


def _anon_completion(mid, text, site, real_model, prompt_text=""):
    pt = max(1, len(str(prompt_text)) // 3)
    ct = max(1, len(text or "") )
    return {"id": "chatcmpl-anon", "object": "chat.completion",
            "created": int(time.time()), "model": mid,
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": pt, "completion_tokens": ct,
                      "total_tokens": pt + ct},
            "hub_upstream": site, "hub_real_model": real_model}


def _serve_adapter(entry, body, mid):
    site = entry.get("site", entry.get("adapter", "anon"))
    # 流式：后台线程跑真实调用，主线程持续发 SSE 心跳，拿到结果后一次性交付
    if body.get("stream"):
        def gen():
            box = {}
            def work():
                box["text"], box["model"] = _anon_result(entry, body)
            th = threading.Thread(target=work, daemon=True)
            th.start()
            last = time.time()
            while th.is_alive():
                if time.time() - last > HEARTBEAT_IDLE:
                    yield b": keepalive\n\n"
                    last = time.time()
                time.sleep(0.5)
            text = box.get("text")
            if not text:
                err = {"id": "hub_err", "object": "chat.completion.chunk", "model": mid,
                       "choices": [{"index": 0,
                                    "delta": {"content": "[匿名上游暂时不可用，请稍后重试或换一个模型]"},
                                    "finish_reason": "stop"}]}
                yield b"data: " + json.dumps(err, ensure_ascii=False).encode() + b"\n\n"
                yield final_usage_chunk(mid, "chatcmpl-anon", 1, 24)
                yield b"data: [DONE]\n\n"
                return
            head = {"id": "chatcmpl-anon", "object": "chat.completion.chunk",
                    "created": int(time.time()), "model": mid,
                    "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}
            yield b"data: " + json.dumps(head, ensure_ascii=False).encode() + b"\n\n"
            bodyc = {"id": "chatcmpl-anon", "object": "chat.completion.chunk",
                     "created": int(time.time()), "model": mid,
                     "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}
            yield b"data: " + json.dumps(bodyc, ensure_ascii=False).encode() + b"\n\n"
            tail = {"id": "chatcmpl-anon", "object": "chat.completion.chunk",
                    "created": int(time.time()), "model": mid,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            yield b"data: " + json.dumps(tail, ensure_ascii=False).encode() + b"\n\n"
            yield final_usage_chunk(mid, "chatcmpl-anon",
                                    max(1, len(str(body.get("messages", ""))) // 3),
                                    max(1, len(text)))
            yield b"data: [DONE]\n\n"
        return Response(stream_with_context(gen()), content_type="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Upstream": site,
                                 "X-Accel-Buffering": "no"})

    # 非流式
    t0 = time.time()
    try:
        text, real_model = _anon_result(entry, body)
    except Exception as ex:
        text, real_model = None, str(ex)[:60]
    if not text:
        with LOCK:
            HEALTH[mid]["last_ok"] = False
            HEALTH[mid]["fails"] += 1
        return jsonify({"error": {"message": "anon upstream failed",
                                  "type": "upstream_error",
                                  "detail": str(real_model)}}), 502
    lat = round(time.time() - t0, 2)
    with LOCK:
        HEALTH[mid]["last_ok"] = True
        HEALTH[mid]["latency"] = lat
        HEALTH[mid]["fails"] = 0
    return jsonify(_anon_completion(mid, text, site, real_model,
                                   body.get("messages", "")))



@app.route("/v1/models", methods=["GET"])
def v1_models():
    if not check_auth():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"object": "list", "data": model_list()})


@app.route("/diag", methods=["GET"])
def diag():
    """诊断端点：查看最近若干次请求的逐跳过程（无需鉴权，仅本机排障用）。"""
    with DIAG_LOCK:
        snap = list(DIAG)
    return jsonify({"count": len(snap), "records": snap[-30:]})


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

    # 诊断记录：记录本次请求的模型/思考参数/max_tokens/消息规模，逐跳过程由 gen 追加
    _diag = {"ts": time.strftime("%H:%M:%S"), "model": mid, "stream": bool(body.get("stream")),
             "max_tokens": body.get("max_tokens"), "msg_len": len(str(body.get("messages", ""))),
             "think": None, "hops": [], "result": None}
    diag_add(_diag)

    # 暂时不可用的模型：明确告知，避免客户端无谓重试死上游
    if entry.get("disabled"):
        return jsonify({"error": {"message": f"model '{mid}' is temporarily unavailable",
                                  "type": "model_unavailable"}}), 503

    # 思考模式开关：兼容网关自定义 think 与各类 APP 的标准参数
    # （think / thinking[灵犀] / reasoning_effort / enable_thinking /
    #   chat_template_kwargs / reasoning / thinking_config）
    explicit_think = body.pop("think", None)
    # 灵犀的 thinking 也走 detect_think 识别（必须在 pop 之前让 detect 看到）
    think = detect_think(body, explicit_think)
    # 清掉 APP 带来的标准思考参数，避免原样透传污染上游；统一由 apply_think 翻译。
    # thinking 必须清理：灵犀的 thinking:"disabled" 若透传给不认它的上游会导致全失败。
    for _k in ("thinking", "reasoning_effort", "enable_thinking", "reasoning",
               "thinking_config", "include_thoughts", "chat_template_kwargs",
               "stream_options"):
        body.pop(_k, None)
    apply_think(body, entry["id"], think)
    _diag["think"] = think
    _diag["max_tokens"] = body.get("max_tokens")   # 抬升后的实际值
    # 客户端明确「关思考」时，网关在转发层剥掉上游的 reasoning 字段，
    # 保证「关」在任何上游都真能隐藏思维链（部分上游不认 enable_thinking）。
    # 用规范化后的值判断，避免 APP 传字符串 "false" 时被漏判为「不剥离」。
    strip_reasoning = (think is False)

    # 匿名免 key、非 OpenAI 协议的真模型（AI Horde / Yqcloud）：由网关代做协议转换
    if entry.get("adapter"):
        return _serve_adapter(entry, body, mid)

    # 流式：逐上游尝试；一旦某上游开始吐数据就转发；若该上游中途断开，自动切到下一个上游继续，
    #       期间用 SSE 注释心跳保活，避免代理层无数据超时掐断连接。
    if body.get("stream"):
        chain = build_chain(entry)

        def gen():
            state = {"id": None, "pt": 0, "ct": 0}   # 本流的 id / 估算 token，供终态 usage 用
            # 流式链总预算：公益上游并发限流时，绝不能在单条链上无限切下去。
            # 到达预算仍未拿到正文就尽早收尾，避免客户端(灵犀 60s)先超时导致 usage_missing。
            chain_deadline = time.time() + CHAIN_BUDGET

            def note(out):
                """从转发的 chunk 抓取 stream_id 并累计输出字符，usage 数值才有意义。"""
                if not out or not out.startswith(b"data:"):
                    return
                try:
                    o = json.loads(out[5:].strip())
                except Exception:
                    return
                if state["id"] is None and str(o.get("id", "")).startswith("chatcmpl"):
                    state["id"] = o["id"]
                for ch in (o.get("choices") or []):
                    d = ch.get("delta") or {}
                    c = d.get("content") or d.get("reasoning") or d.get("reasoning_content")
                    if isinstance(c, str):
                        state["ct"] += len(c)

            def finish():
                """统一收尾：规范的 usage(total>0, id 与本流一致) + 唯一 DONE。
                无论正常完成、断流、还是全失败，都必然走这里 —— 严格客户端不会再报
                usage_missing。"""
                pt = max(1, state["pt"] or len(str(body.get("messages", ""))) // 3)
                ct = max(1, state["ct"])
                return [final_usage_chunk(mid, state["id"], pt, ct), b"data: [DONE]\n\n"]

            for c in chain:
                # 预算耗尽：不再切新上游，尽早收尾（否则客户端先超时）。
                if time.time() >= chain_deadline:
                    yield (": chain budget reached\n\n").encode()
                    break
                last = time.time()
                buf = b""
                got_content = False       # 该上游是否吐出过有效回答(含拒答)
                try:
                    # 流式不重试(retries=0)：限流时立刻切下一个，比在单个上游退避等待更快拿到正文。
                    r, hop_lat = call_upstream(c, body, retries=0)
                    if r.status_code != 200:      # 技术失败(402/429/5xx) → 切下一个(含救急层)
                        _diag["hops"].append(f'{c["site"]}:{r.status_code}({hop_lat}s)')
                        try:
                            r.close()
                        except Exception:
                            pass
                        continue
                    # 真流式：收到一行就转发一行，绝不攒着、绝不提前掐断。
                    # 历史教训：pending 缓冲把流式憋成"卡住→一次性吐"，CONTENT_IDLE
                    # 把正常句间停顿当挂起掐断成"只吐一段"——两个都是自作主张的坏机制，已移除。
                    for chunk in r.iter_content(chunk_size=256):
                        if chunk:
                            last = time.time()
                            buf += chunk
                            while b"\n" in buf:
                                line, buf = buf.split(b"\n", 1)
                                if sse_has_content(line):
                                    got_content = True   # 拒答文本也算有效回答，绝不切换
                                out = sse_strip(line, strip_reasoning, mid)
                                if out:
                                    note(out)
                                    yield out            # 即时转发，真流式
                        elif time.time() - last > HEARTBEAT_IDLE:
                            yield b": keepalive\n\n"
                            last = time.time()
                    if buf.strip():
                        if sse_has_content(buf):
                            got_content = True
                        out = sse_strip(buf, strip_reasoning, mid)
                        if out:
                            note(out)
                            yield out
                    if got_content:
                        _diag["hops"].append(f'{c["site"]}:OK(ct={state["ct"]})')
                        _diag["result"] = "ok:" + c["site"]
                        for p in finish():
                            yield p
                        return
                    # 200 但空流/只有 reasoning/报错 JSON：切下一个
                    _diag["hops"].append(f'{c["site"]}:EMPTY')
                    yield (": upstream %s empty, switching...\n\n" % c["site"]).encode()
                    continue
                except Exception as _ex:
                    # 中途断流：若已吐出过内容就当作完成(避免拼接两个上游的输出)，否则切换
                    if got_content:
                        _diag["hops"].append(f'{c["site"]}:DROP_OK(ct={state["ct"]})')
                        _diag["result"] = "drop_ok:" + c["site"]
                        for p in finish():
                            yield p
                        return
                    _diag["hops"].append(f'{c["site"]}:DROP({str(_ex)[:30]})')
                    yield (": upstream %s dropped, switching...\n\n" % c["site"]).encode()
                    buf = b""
                    continue
            # 自身层 + 救急层全部技术失败：可读提示 + 规范 usage + 唯一 DONE
            _diag["result"] = "ALL_FAILED"
            err = {"id": "hub_err", "object": "chat.completion.chunk", "model": mid,
                   "choices": [{"index": 0,
                                "delta": {"content": "[上游暂时全部不可用，请稍后重试或换一个模型]"},
                                "finish_reason": "stop"}]}
            state["ct"] += len("[上游暂时全部不可用，请稍后重试或换一个模型]")
            yield b"data: " + json.dumps(err, ensure_ascii=False).encode() + b"\n\n"
            for p in finish():
                yield p

        return Response(stream_with_context(gen()),
                        content_type="text/event-stream",
                        headers={"Cache-Control": "no-cache",
                                 "X-Upstream": "multi",
                                 "X-Accel-Buffering": "no"})

    # 非流式：逐上游尝试，出字即返回；单上游失败自动换下一个。
    # 整条链有总时间预算，越靠后的上游越快放弃，保证在客户端超时前给出答复。
    errors = []
    deadline = time.time() + CHAIN_BUDGET
    for c in build_chain(entry):
        left = deadline - time.time()
        if left <= 1:
            errors.append("chain_budget_exhausted")
            break
        # 非流式也用不重试 + 短超时：公益上游限流时重试 3 次×22s=66s，客户端(灵犀/Rikkahub
        # 常 60s)早已超时 → 表现成「思考一下就一直转圈/不可用」。快速切上游比死等更可靠。
        rt = max(3, min(12, int(left)))
        try:
            r, lat = call_upstream(c, body, read_timeout=rt, retries=0)
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
            if strip_reasoning:  # 关思考：剥掉非流式响应里的思维链
                for k in ("reasoning", "reasoning_content", "reasoning_details"):
                    msg.pop(k, None)
            j["model"] = mid                      # 对外保持用户填的模型名
            j["hub_upstream"] = c["site"]        # 标注真实上游，便于排障
            j["hub_latency"] = lat
            # usage 兜底：上游缺失或全 0 时按内容补一个非零 usage，避免严格客户端
            # 判 'invalid/omitted terminal usage'（与流式同口径）。
            u = j.get("usage")
            if not isinstance(u, dict) or not (u.get("total_tokens") or 0):
                pt = max(1, len(str(body.get("messages", ""))) // 3)
                ct = max(1, len(str(content)))
                j["usage"] = {"prompt_tokens": pt, "completion_tokens": ct,
                              "total_tokens": pt + ct}
            with LOCK:
                HEALTH[mid]["last_ok"] = True
                HEALTH[mid]["latency"] = lat
                HEALTH[mid]["fails"] = 0
            _diag["result"] = "ok:" + c["site"]
            return jsonify(j)
        except Exception as ex:
            errors.append(f'{c["site"]}:{str(ex)[:60]}')
            _diag["hops"].append(f'{c["site"]}:EX({str(ex)[:30]})')
            continue

    with LOCK:
        HEALTH[mid]["last_ok"] = False
        HEALTH[mid]["fails"] += 1
    _diag["result"] = "ALL_FAILED"
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
