# freeai · 免 key 公益大模型聚合网关（OpenAI 兼容）

把散落在各处的**公开、匿名免 key、OpenAI 兼容**的公益大模型端点，聚合成一个统一地址。
不用注册、不用自己的 API Key，改一个 `base_url` 就能在任意 OpenAI 客户端里调用。

## 已上线 🚀

| 项目 | 值 |
|---|---|
| 网站 / 控制台 | **https://freeai-f9v0.onrender.com** |
| OpenAI 调用地址 (Base URL) | **`https://freeai-f9v0.onrender.com/v1`** |
| API Key（密钥） | **`sk-freeai`**（上游免 key，任意非空值均可） |
| 部署 | Render Web Service `freeai`，源仓库 `sasuk2026/freeai`，自动部署已开启 |

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
Python / cURL / Node.js / 客户端软件 四种调用示例（地址与密钥自动填成线上值）。

## 实测结论（2026-11-15）

| 项目 | 结果 |
|---|---|
| 可调用模型条目 | **100** |
| 逐条真实发请求、真出字 | **100 / 100** |
| 公益站点 | 5 家（Pollinations / OVHcloud / LLM7.io / Kilo Gateway / llmfreeapikey） |
| 其中真正不同的独立模型 | 约 24 个 |
| 认证 | 全部匿名，无需 Key |

> 说明：全网「匿名免 key + 长期稳定」的公益大模型端点数量有限，凑不出 100 个互不相同的模型。
> 因此清单 = 各站实测能出字的原生 ID + 官方别名 + 标准模型名兼容映射 + 自动路由。
> **每一条都真实发过一次对话请求并成功返回内容**，没有编造地址、没把失效链接标成可用。
> 网页里每条都标了类型（独立模型 / 官方别名 / 兼容别名 / 自动路由）和真实上游。

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
| `catalog.json` / `catalog_web.json` | 100 条模型的完整实测数据 / 网页展示数据 |
| `requirements.txt` / `render.yaml` / `Dockerfile` | 部署配置 |
