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
299 条实测清单数据内嵌在前端 HTML 里，因此 Pages 站点无需后端也能浏览；只有「在线试一句」和
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

## 实测结论（2026-11-16）

| 项目 | 结果 |
|---|---|
| 可调用 ID（**不等于模型种类**） | **299** |
| 逐条真实发请求、真出字 | **298 / 299**（1 个 AI Horde 模型吐乱码，标「暂时不可用」） |
| **匿名免 key、零配置、互不相同的真模型** | **34 个**（AI Horde 33 + 阶跃星辰 Step 1），**已并入主清单**，带绿色「★ 免 Key 真模型」标签与专属系列 |
| **大参数真模型（≥20B，免 Key）** | **27 个**，最大 **550B**：550B×2 / 501B / 397B / 128B / 120B×3 / 72B / 70B…，网页「3.4 大参数真模型」专区集中置顶，卡片带参数量徽章，可按「只看大模型」筛选 |
| 免 key 直连的原生 OpenAI 兼容 ID | 39 个（跨站可能有重复底座） |
| 名字兼容映射（同一底座） | 218 个，**非独立模型** |
| 公益站点 | 8 家（6 家 OpenAI 兼容公益站 + AI Horde / Yqcloud） |
| 认证 | **全部匿名，无需任何 Key** |

> **本目录只收录「不需要任何 Key」的端点。** 需要注册才能领免费 Key 的站点（OpenRouter `:free`、Groq、Cerebras、Gemini、Api.Airforce、NVIDIA NIM…）真模型虽有数百个，但**一概不收** —— 都有 Key 了，这个网站就没有意义。此前收录的 77 个「免费额度真模型」清单、57 个 DeepSeek 真 ID 清单，连同 `catalog_free_tier.json` / `catalog_deepseek.json` 两个数据文件与网页分区，已按要求全部删除。
>
> **匿名免 key 真模型（本轮，34 个）：** 此前只统计「原生 OpenAI 兼容的匿名端点」，漏掉了 AI Horde / Yqcloud 这类「免 key 但非 OpenAI 协议、需网关代转」的匿名算力。
> · **AI Horde**（`https://aihorde.net/api/v2`）：志愿者分布式算力，用官方公开匿名标识 `0000000000`（硬编码在网关，非用户私有 Key）。全量 34 个在线模型实测 **33 个真出字**（本轮重试队列救活 128B Behemoth），服务端返回 `generation.model` 与指定名逐一对应（Qwen3 / Llama 3.2 / Gemma 4 / RWKV7 / Skyfall-31B / Stheno / Wicked-Tutu / Orion…），确为不同底座。
> · **Yqcloud**（`https://api.binjie.fun/api/generateStream`）：纯文本 POST，自报 "Step, StepFun"，17×23=391、Paris、写俳句全对。
> · 两者都不是 OpenAI 协议，已在 `gateway.py` 加 `aihorde` / `yqcloud` 适配器代做协议转换（异步队列轮询 / 纯文本 → 标准 `/v1/chat/completions`，含 stream 伪流式与心跳），**用户只填网关地址 + 模型 ID，零配置、无 Key**。这 34 个已并入 `catalog.json` / `catalog_web.json` / 网页主清单，可搜索、按系列分组、点开调用；`/v1/models` 共 299 条。端到端抽样 12 个不同底座 **11 个出字**（1 个队列排队超时如实报 502）。
> · 局限：志愿者算力节点动态上下线、队列可能排队；个别模型暂时取不到。
>
> **标准模型名兼容映射（218 个名字）：** 从各站真实清单取主流系列名，经 llmfreeapikey 兼容层实测出字。**注意：该站 /models 只返回 1 个模型 `free-ai`，且传入任意名字（含瞎编的）都返回同一底座的回答，所以这 218 条是名字、不是 218 个模型。** 别名只用于让只认标准名字的软件免改代码。
>
> **299 是可调用 ID 数，不是模型种类数**；带绿色标签的 34 个才是互不相同的免 key 真模型。每一条都真实发过对话请求并成功返回内容，没有编造地址、没把失效链接标成可用。网页里每条都标了类型和真实上游，并按系列分组展示。

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
| AI Horde | `https://aihorde.net/api/v2` | 志愿者匿名算力，34 个开源真模型，网关代转异步队列 |
| Yqcloud | `https://api.binjie.fun/api/generateStream` | 阶跃星辰 Step，免 key 纯文本 POST，网关代转 |

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
推到 `main` 分支 normally 会自动触发部署。**若推送后线上没更新**（自动部署偶尔不触发），
用 Render API 手动触发（服务 ID `srv-dbshjaui0phs73d5o14g`）：

```bash
curl -X POST -H "Authorization: Bearer $RENDER_API_KEY" \
  https://api.render.com/v1/services/srv-dbshjaui0phs73d5o14g/deploys
```

部署状态查 `GET /v1/services/<id>/deploys/<deployId>`，到 `live` 即生效（约 20-60 秒）。

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
| `catalog.json` / `catalog_web.json` | 299 条可调用 ID 的完整实测数据 / 网页展示数据（含 34 个匿名免 key 真模型、27 个大参数真模型 `params` 标注） |
| `catalog_anon_real.json` | 34 个匿名免 key 真模型原始清单（AI Horde 33 + StepFun 1），含 `adapter` 字段；已并入 `catalog.json` 主清单，本文件为原始清单备查 |
| `requirements.txt` / `render.yaml` / `Dockerfile` | 部署配置 |
