# 🌐 Proxy Subscription Aggregator

> **全自动、零成本**的节点订阅聚合与管理系统 | 基于 GitHub Actions 运行

![GitHub Workflow Status](https://img.shields.io/github/actions/workflow/status/your-username/your-repo/auto-update.yml?style=flat-square)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg?style=flat-square)

---

## ✨ 功能特性

| 功能 | 说明 |
|------|------|
| 🤖 **自动抓取** | 定时拉取多个私有订阅链接（通过 Secrets 隐私保护） |
| 🔄 **智能去重** | 基于 `server:port:protocol` 指纹自动剔除重复节点 |
| 💀 **死线过滤** | TCP 握手存活测试，自动过滤无法连通的死节点 |
| 🛠️ **多格式输出** | Clash (`.yaml`) / Sing-box (`.json`) / 原始文本 (`.txt`) |
| 🚀 **静态发布** | 自动推送到 `gh-pages` 分支，生成永久不变的订阅链接 |
| 💰 **零成本** | 完全运行在 GitHub 免费额度内，无需服务器 |

---

## 📁 项目结构

```
your-repo/
├── .github/
│   └── workflows/
│       └── auto-update.yml      # ⚡ GitHub Actions 工作流配置
├── scripts/
│   └── fetch_and_convert.py     # 🔧 核心脚本（抓取/去重/测速/转换）
├── README.md                    # 📖 本文件
└── gh-pages/                    # 📦 自动生成的订阅文件（无需手动创建）
    ├── clash.yaml               # Clash / Clash Verge / mihomo 订阅
    ├── singbox.json             # Sing-box 订阅
    └── raw.txt                  # 原始节点列表（通用格式）
```

---

## 🚀 快速开始（3 步搞定）

### 第 1 步：Fork 或创建仓库

1. 点击页面右上角 **[Use this template]** → **Create a new repository**
2. 或者直接 **Fork** 这个仓库到你的账号下
3. 仓库名随意取，比如 `proxy-sub`

> ⚠️ **重要**：仓库必须设为 **Public（公开）**，否则 GitHub Pages 无法生成访问链接。
> 
> 但别担心！你的**订阅链接仍然安全**，因为它们存储在 GitHub Secrets 里，不会公开暴露。

### 第 2 步：配置 Secrets（保护你的隐私链接）

这是最关键的一步！你的订阅链接会以**加密环境变量**的形式存储，任何人（包括仓库协作者）都看不到明文。

#### 操作步骤：

1. 进入你 Fork 后的仓库页面
2. 点击 **Settings（设置）** → 左侧菜单 **Secrets and variables** → **Actions**
3. 点击 **New repository secret** 按钮
4. 在 **Name** 栏填入：`SUB_URLS`（必须完全一致，全大写）
5. 在 **Value** 栏填入你的所有订阅链接，格式如下：

```
https://example.com/sub1,https://example.com/sub2,https://example.com/sub3
```

**支持多种分隔方式（任选一种）：**
- **逗号分隔**：`链接1,链接2,链接3`
- **换行分隔**（每行一个）：
  ```
  https://example.com/sub1
  https://example.com/sub2
  https://example.com/sub3
  ```
- **空格分隔**：`链接1 链接2 链接3`

6. 点击 **Add secret** 保存

#### 可选配置（Repository Variables，非 Secret）

在同一个页面，切换到 **Variables** 标签页：

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `ENABLE_ALIVE_TEST` | `true` | 是否启用存活测试。设为 `false` 可跳过测试（更快但不过滤死节点） |

#### 截图示意：

```
┌─────────────────────────────────────────────┐
│  New secret                                 │
├─────────────────────────────────────────────┤
│  Name        [SUB_URLS           ]          │
│                                             │
│  Secret      [                        ]     │
│              [ 你的订阅链接粘贴在这里... ]     │
│              [                        ]     │
│                                             │
│  [Add secret]                               │
└─────────────────────────────────────────────┘
```

### 第 3 步：手动触发第一次运行

1. 进入仓库的 **Actions** 标签页
2. 在左侧选择 **Proxy Subscription Aggregator** 工作流
3. 点击右侧 **Run workflow** 按钮
4. 选择 **main** 分支，点击绿色 **Run workflow** 按钮
5. 等待约 1~3 分钟运行完成（⚠️ 首次可能稍慢）

运行成功后，你的订阅链接就可以使用了！

---

## 📡 获取你的订阅链接

部署成功后，你将获得以下**永久不变**的静态链接（把 `your-username` 和 `your-repo` 替换为你的实际信息）：

| 格式 | 订阅链接 | 适用工具 |
|------|----------|----------|
| **Clash** | `https://your-username.github.io/your-repo/clash.yaml` | Clash Verge / mihomo / Clash for Windows |
| **Sing-box** | `https://your-username.github.io/your-repo/singbox.json` | sing-box / SFI / SFA |
| **原始文本** | `https://your-username.github.io/your-repo/raw.txt` | Shadowrocket / Quantumult X / Surge 等 |

### 导入示例：

**Clash Verge / mihomo：**
```
设置 → 订阅 → 新建 → 粘贴上面的 clash.yaml 链接
```

**Sing-box (SFA / SFI)：**
```
设置 → 订阅 → 粘贴上面的 singbox.json 链接
```

**Shadowrocket (小火箭)：**
```
设置 → 类型选 Subscribe → URL 粘贴 raw.txt 链接
```

---

## ⏰ 定时任务说明

工作流默认配置为 **每 6 小时自动运行一次**：

```yaml
schedule:
  - cron: '0 */6 * * *'   # UTC 时间
```

对应北京时间：**06:00 / 12:00 / 18:00 / 00:00**

> ⚠️ **注意**：GitHub Actions 的 Cron 触发有 **最多 ~1 小时的延迟容忍**，这是 GitHub 的已知行为，不是 bug。如果你需要立即更新，可以手动触发。

### 修改定时频率

编辑 `.github/workflows/auto-update.yml` 文件中的 `cron` 表达式：

| 频率 | Cron 表达式 | 说明 |
|------|-------------|------|
| 每 2 小时 | `0 */2 * * *` | 较频繁 |
| 每 6 小时 | `0 */6 * * *` | **默认值**（推荐） |
| 每 12 小时 | `0 */12 * * *` | 较保守 |
| 每天 08:00 | `0 0 * * *` | 北京时间 08:00（UTC 00:00） |

---

## 🔧 高级配置与自定义

### 修改存活测试参数

编辑 `scripts/fetch_and_convert.py` 顶部的配置区：

```python
TEST_TIMEOUT = 5          # 单节点超时(秒) — 慢节点可调大至 10
TEST_CONCURRENCY = 20     # 并发线程数 — GitHub Actions 建议不超过 30
```

### 支持的协议

| 协议 | URI 前缀 | Clash 支持 | Sing-box 支持 |
|------|----------|------------|---------------|
| VMess | `vmess://` | ✅ | ✅ |
| VLESS | `vless://` | ✅ | ✅ |
| Shadowsocks | `ss://` | ✅ | ✅ |
| Trojan | `trojan://` | ✅ | ✅ |
| Hysteria2 | `hysteria2://` | ✅ | ✅ |
| SSR | `ssr://` | 仅导出 | 仅导出 |

### 手动本地调试

如果你想在自己的电脑上测试脚本：

```bash
# 安装依赖
pip install pyyaml requests

# 设置环境变量后运行
export SUB_URLS="https://your-sub-link1,https://your-sub-link2"
python3 scripts/fetch_and_convert.py

# 输出文件在 /tmp/proxy_output/ 目录下
```

---

## ❓ 常见问题 FAQ

<details>
<summary><b>🔒 我的订阅链接会泄露吗？</b></summary>

**不会。** 订阅链接存储在 GitHub Secrets 中，这是一个加密的键值存储系统：
- 明文只在 Actions 运行时存在于内存中
- 日志中不会打印完整链接（只显示域名）
- 仓库历史记录、Fork 者都无法查看 Secrets
- 即使仓库是 Public 的，Secrets 也是安全的

</details>

<details>
<summary><b>⏱️ 运行时间太长怎么办？</b></summary>

如果节点数量很多（100+），存活测试可能会耗时较长：
1. 将 `ENABLE_ALIVE_TEST` 变量设为 `false` 关闭存活测试
2. 或者在脚本中增大 `TEST_CONCURRENCY` 到 30~50
3. 或增大 `TEST_TIMEOUT` 减少等待时间

</details>

<details>
<summary><b>📊 如何查看运行日志？</b></summary>

1. 进入仓库 → **Actions** 标签页
2. 点击最近一次运行的 workflow
3. 点击 **aggregate** job
4. 即可看到完整的抓取/去重/测试日志

</details>

<details>
<summary><b>🔄 如何强制立即更新？</b></summary>

进入 **Actions** 页面 → 选择工作流 → **Run workflow** → 点击绿色按钮即可立即触发一次运行。

</details>

<details>
<summary><b>❌ 抓取失败的常见原因</b></summary>

- **链接已过期**：检查订阅是否还在有效期内
- **需要特殊 Header**：部分机场需要 User-Agent 或 Token
- **IP 被限制**：少数机场限制非国内 IP 访问（GitHub Actions 是海外 IP）
- **Cloudflare 验证**：启用了 CF 保护的链接可能无法直接抓取

</details>

---

## 📊 工作流程图

```
┌─────────────┐
│  Cron 定时   │  每 6 小时 / 手动触发
│  或手动触发  │
└──────┬──────┘
       ▼
┌─────────────┐
│ 读取 Secrets │  从加密变量获取订阅链接
│  SUB_URLS    │
└──────┬──────┘
       ▼
┌─────────────┐
│  HTTP 抓取   │  并发请求所有订阅源
│  Base64解码  │  自动识别编码格式
└──────┬──────┘
       ▼
┌─────────────┐
│  节点解析    │  提取 vmess/vless/ss/trojan 等
│  智能去重    │  server+port+protocol 指纹去重
└──────┬──────┘
       ▼
┌─────────────┐
│  TCP 存活测试 │  并发握手检测 (可选)
│  过滤死节点  │  只保留能连通的节点
└──────┬──────┘
       ▼
┌──────────────┬──────────────┬──────────────┐
│  Clash YAML  │  Sing-box JSON│  Raw Text    │
│  (.yaml)     │  (.json)     │  (.txt)      │
└──────┬───────┴──────┬───────┴──────┬───────┘
       ▼              ▼              ▼
┌──────────────────────────────────────────┐
│         推送到 gh-pages 分支             │
│                                         │
│  https://xxx.github.io/repo/clash.yaml  │
│  https://xxx.github.io/repo/singbox.json│
│  https://xxx.github.io/repo/raw.txt     │
└──────────────────────────────────────────┘
```

---

## 🛡️ 安全说明

- ✅ 所有敏感信息存储于 GitHub **Encrypted Secrets**
- ✅ 运行日志不打印完整链接
- ✅ 仓库可为 Public 而 Secrets 不暴露
- ✅ 使用官方 `peaceiris/actions-gh-pages` 安全发布
- ✅ 无需自建服务器，无额外成本

---

## 📄 License

MIT License © 2024 Free to use and modify.

---

## 🙏 致谢

- [Clash Meta/mihomo](https://github.com/MetaCubeX/mihomo) — 强大的代理内核
- [sing-box](https://github.com/SagerNet/sing-box) — 下一代代理平台
- [peaceiris/actions-gh-pages](https://github.com/peaceiris/actions-gh-pages) — GitHub Pages 自动部署
- [GitHub Actions](https://github.com/features/actions) — 免费 CI/CD 平台

---

<div align="center">

**如果这个项目对你有帮助，请给一个 ⭐ Star！**

🎉 **全自动 · 零成本 · 隐私安全** 🎉

</div>
