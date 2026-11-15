# freeai · 免 key 公益大模型聚合网关（OpenAI 兼容）

把散落在各处的**公开、匿名免 key、OpenAI 兼容**的公益大模型端点，聚合成一个统一地址。
不用注册、不用自己的 API Key，改一个 `base_url` 就能在任意 OpenAI 客户端里调用。

## 已上线 🚀 · 前后端分离

| 层 | 承载 | 地址 |
|---|---|---|
| 前端网页控制台（根域名，推荐） | **GitHub Pages 用户级站点**（仓库 `sasuk2026.github.io`，根目录 `index.html`） | **https://sasuk2026.github.io/** |
| 前端网页控制台（项目页，同内容） | **GitHub Pages**（本仓库 `docs/`） | **https://sasuk2026.github.io/freeai/** |
| 后端 API 网关 | **Render** Web Service `freeai` | **https://freeai-f9v0.onrender.com** |
| OpenAI 调用地址 (Base URL) | 由后端提供，前端面板自动填入 | **`https://freeai-f9v0.onrender.com/v1`** |
| API Key（密钥） | 上游免 key，任意非空值均可 | **`sk-freeai`** |

前端是单文件静态页，通过 CORS 跨域调用 Render 后端；**模型调用全部由 freeAI 后端转发**到各公益上游。
100 条实测清单数据内嵌在前端 HTML 里，因此 Pages 站点无需后端也能浏览；只有「在线试一句」和
真正的 OpenAI 客户端调用需要后端在线。后端仓库 `sasuk2026/freeai`，Render 自动部署已开启。

> Render 免费层闲置约 15 分钟会休眠，冷启动需 30-60 秒，首次在线测试可能要重试一次。

```python
from openai import OpenAI
client = OpenAI(base_url="https://freeai-f9v0.onrender.com/v1", api_key="sk-freeai")
r = client.chat.completions.create(
    model="gpt-4o",  # 或网页清单里任意模型 ID（点击可复制）
    messages=[{"role": "user", "content": "你好"}],
)
print(r.choices[0].message.content)
```

网页顶部有「OpenAI 接入凭证」面板，Base URL 与 API Key 都能一键复制，并给出
Python / cURL / Node.js / 客户端软件 四种调用示例（地址与密钥自动填成后端线上值）。
**在别的地方调用时，填的是 Render 后端地址，不是 github.io 前端地址。**

## 实测结论（2026-11-15）

| 项目 | 结果 |
|---|---|
| 可调用 ID（**不等于模型种类**） | **264** |
| 逐条真实发请求、真出字 | **264 / 264** |
| 其中免 key 直连的原生模型 ID | 39 个（跨站可能有重复底座） |
| 其中名字兼容映射（同一底座） | 218 个，**非独立模型** |
| 另收录免费额度真模型（需注册免费 Key） | **77 个**（见 `catalog_free_tier.json` 与网页独立分区） |
| 公益站点 | 6 家（Pollinations / OVHcloud / LLM7.io / Kilo Gateway / BlockRun.ai / llmfreeapikey） |
| 认证 | 上方 264 条全部匿名，无需 Key；免费额度分区需自备该站免费 Key |

> **全网复查（2026-11-15）：** 重新拉取并实测 27 个公开推理服务的 `/models` 清单，合计约 3,800 个真实模型 ID（Api.Airforce 781、Requesty 750、Vercel AI Gateway 507、Pollinations 441、Nous 398、UnoRouter 316、OrcaRouter 216、DeepInfra 178…）。免费不是一个口径，分三层：
> ① **匿名免 key 直连** —— 6 家公益站，约 30 个独立模型，本目录标「独立模型」，填任意 Key 即可调；
> ② **免费额度但需注册免费 Key** —— OpenRouter `:free`、Api.Airforce、Pollinations 新域名、Kilo、BlockRun、NVIDIA NIM、Groq、Cerebras、Gemini、Mistral、Cloudflare、Ollama Cloud、Zhipu 等。已从各站 `/models` 里按官方免费标记（`tier:free` / `isFree` / `pricing=0` / `billing_mode=free`）筛出 96 条、跨站去重 **77 个真模型**，排除 embedding / 图像 / TTS 与社区自定义条目，收录在 `catalog_free_tier.json` 与网页「免费额度真模型」独立分区。**需自备该站免费 Key，本网关不代管，未逐条匿名实测**；无 Key 实测 27 站全部 401/402/429；
> ③ **标准模型名兼容映射** —— 从上述真实清单取主流系列名，经 llmfreeapikey 兼容层逐条实测出字，本目录标「兼容别名」。**注意：该站 /models 只返回 1 个模型 `free-ai`，且传入任意名字（含不存在的名字）都返回同一底座的回答，所以这 218 条是名字、不是 218 个模型。**
>
> 第 ① 层只有几十个，不是找得不够，而是无限免费推理本身不可持续。**264 是可调用 ID 数，不是模型种类数**；严格按不同底层模型计数只有 39 个原生 ID（且跨站可能重复）。
> **每一条都真实发过一次对话请求并成功返回内容**，没有编造地址、没把失效链接标成可用。
> 网页里每条都标了类型（独立模型 / 官方别名 / 兼容别名 / 自动路由）和真实上游，并按系列分组展示。

兼容映射的作用：很多软件只允许填 `gpt-4o`、`claude-3-5-sonnet` 这类标准名字，
网关把这些名字映射到实测可用的公益底座，于是不改代码也能直接用。

## 收录的公益站点

| 站点 | Base URL | 特点 |
|---|---|---|
| Pollinations | `https://text.pollinations.ai/openai` | 柏林开源公益，匿名层，GPT-OSS 20B |
| OVHcloud AI Endpoints | `https://oai.endpoints.kepler.ai.cloud.ovh.net/v1` | 欧盟托管 / GDPR，Qwen3.5 397B、GPT-OSS 120B 等 |
| LLM7.io | `https://api.llm7.io/v1` | 匿名 turbo 档，DeepSeek V4 Flash、GLM 等 |
| Kilo Gateway | `https://api.kilo.ai/api/gateway` | 免费池 13 个模型 + 自动路由，Nemotron 550B 等 |
| llmfreeapikey | `https://api.llmfreeapikey.com/v1` | 纯免 key、CORS 全开、接受任意模型名 |

## 快速开始（本地）

```bash
pip install -r requirements.txt
python3 gateway.py            # 默认 http://0.0.0.0:8000
```

浏览器打开 `http://localhost:8000` 是控制台网页；`http://localhost:8000/v1` 是对外 API 地址。

## 在任何地方调用

Cherry Studio / NextChat / LobeChat / ChatBox / Cursor / Cline 等：
API 地址填 `https://freeai-f9v0.onrender.com/v1`，API Key 随便填非空字符串（如 `sk-freeai`），
协议选 OpenAI，模型从网页清单挑（点击模型 ID 即可复制名字）。

```bash
curl https://freeai-f9v0.onrender.com/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"你好"}]}'
```

接口清单：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/models` | 模型列表（OpenAI 格式，含实测标记） |
| POST | `/v1/chat/completions` | 对话补全，支持 `stream`、`messages` 多轮、自动故障转移 |
| GET | `/health` `/healthz` | 运行期健康状态 |
| GET | `/catalog.json` | 完整实测数据 |
| GET | `/` | 网页控制台 |

## 部署

仓库已带 `render.yaml` 和 `Dockerfile`，Render 上以 Docker Web Service 方式运行，
推到 `main` 分支会自动触发部署。

```bash
docker build -t freeai .
docker run -p 8000:8000 freeai
```

**安全提示**
- 不要把任何密钥写进仓库。若想给自己的网关加一道访问口令，在平台控制台设置环境变量
  `HUB_ACCESS_TOKEN`，之后客户端的 API Key 就填这个值；不设置则完全开放。
- 上游是第三方公益服务，请遵守各家条款；免费层限速按 IP，别跑生产高并发。
- 部分上游会记录 prompt 用于改进模型，不要发送公司代码、合同、个人隐私等敏感内容。
- 免费池模型会轮换、共享配额会耗尽。网关已内置自动故障转移（主上游失败自动换下一个），
  网页清单的「实测」标记是探测时刻的状态，之后可用 `/health` 观察运行期可用性。

## 文件说明

| 文件 | 用途 |
|---|---|
| `gateway.py` | 聚合网关（Flask），统一 /v1 协议 + 故障转移 + CORS |
| `index.html` | 网页控制台：接入凭证面板、卡片式模型清单、一键复制、在线测试 |
| `catalog.json` / `catalog_web.json` | 264 条可调用 ID 的完整实测数据 / 网页展示数据 |
| `requirements.txt` / `render.yaml` / `Dockerfile` | 部署配置 |
