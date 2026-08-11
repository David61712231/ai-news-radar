# 定时生成静态快照 → 自动部署到 GitHub Pages · 操作手册

> 本文档由「AI HOT 看板」从 CloudBase 迁移到 GitHub Pages 的实战经验沉淀而成。
> 目标：以后想把任意页面/项目做成「GitHub Actions 定时生成静态快照 → 自动部署到固定网址」的工作流，
> 直接照本手册操作即可，不用再从零摸索。
>
> 实战案例：AI HOT 仪表盘（`WadeLiuAstro/AI-HOT`），固定链接 `https://wadeliuastro.github.io/AI-HOT/`，
> 每天北京时间 08:00 自动更新。

---

## 1. 触发机制讲解

GitHub Actions 支持两类触发方式，本工作流同时启用：

### 1.1 定时触发 `on.schedule`

```yaml
on:
  schedule:
    - cron: "0 0 * * *"   # 每天 UTC 00:00 = 北京时间 08:00
```

要点（全部踩过坑）：

- **cron 固定为 UTC 时区**：GitHub 服务器统一用 UTC，**北京时间需要减 8 小时**。
  例如"每天北京时间 08:00"要写成 `0 0 * * *`，不是 `0 8 * * *`（那会是北京 16:00）。
- 标准 5 段式：`分 时 日 月 周`，最小间隔 5 分钟（`*/5 * * * *`）。
- schedule 触发时**没有 `github.event.inputs`**，如果工作流里有输入相关逻辑要兼容该情况。
- 60 天无活动仓库的 schedule 会被 GitHub 自动暂停；长跑任务建议配合手动触发兜底。

### 1.2 手动触发 `workflow_dispatch`

```yaml
on:
  workflow_dispatch:
    inputs:
      deploy:
        description: "是否部署到 GitHub Pages（取消勾选则只生成并提交快照）"
        type: boolean
        default: true
```

- 在仓库 **Actions 页 → 选中工作流 → 右上角 Run workflow** 按钮手动触发。
- 可定义输入参数（boolean / choice / string），部署条件可据此做分支。

### 1.3 二者区别

| 维度 | schedule | workflow_dispatch |
|---|---|---|
| 触发者 | GitHub 调度器（UTC 整点扫描） | 人（点击按钮） |
| 时间精度 | 可能有分钟级延迟，不保证准点 | 立即执行 |
| 事件输入 | 无 `inputs` | 有 `inputs`（表单填写） |
| 典型用途 | 每日定时更新 | 调试、紧急刷新、补跑 |

---

## 2. 完整 YAML 模板（可直接复用）

以下模板以本仓库 `update-snapshot.yml` 为 baseline，去掉 AI HOT 专属内容，保留通用骨架。
**复制后替换「生成快照」步骤为你自己的构建/生成命令即可。**

```yaml
# 定时生成静态快照 → 部署到 GitHub Pages
name: 定时更新并部署

on:
  schedule:
    # 每天北京时间 08:00 = UTC 00:00（cron 固定为 UTC 时区）
    - cron: "0 0 * * *"
  workflow_dispatch:
    inputs:
      deploy:
        description: "是否部署到 GitHub Pages（取消勾选则只生成并提交快照）"
        type: boolean
        default: true

# 硬性要求：
#   contents: write — 把生成的文件提交回仓库（缺了 push 会 403）
#   pages: write    — deploy-pages 部署 Pages 必需
#   id-token: write — deploy-pages 请求 OIDC token 必需
permissions:
  contents: write
  pages: write
  id-token: write

# 防止上一次运行未结束时下一次定时触发并发冲突
concurrency:
  group: static-site-snapshot
  cancel-in-progress: false

jobs:
  build-and-deploy:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}

    steps:
      - name: 检出仓库
        uses: actions/checkout@v4

      # ===== 这里替换为你的构建/生成命令 =====
      - name: 生成静态快照
        run: |
          echo "在此执行你的构建命令"
          # 例：python3 scripts/build_snapshot.py --out public/index.html
          # 例：npm run build && cp -r dist/* _site/
      # =======================================

      - name: 提交产物回仓库（若内容有变化）
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add public/   # 按需调整要跟踪的产物路径
          if git diff --cached --quiet; then
            echo "产物无变化，跳过提交"
          else
            git commit -m "chore: 更新静态快照 [skip ci]"
            # 关键：先 pull --rebase 再 push，避免与并发运行/他人提交冲突
            git pull --rebase origin main
            git push
          fi

      # ===== 以下四步是 GitHub Pages 官方部署链路（硬性流程） =====
      - name: 准备 Pages 产物（站点根目录）
        if: ${{ !cancelled() && (github.event_name == 'schedule' || inputs.deploy != 'false') }}
        run: |
          mkdir -p _site
          cp -r public/* _site/   # 把站点文件放进 _site，根目录必须有 index.html

      - name: 上传 Pages 产物
        if: ${{ !cancelled() && (github.event_name == 'schedule' || inputs.deploy != 'false') }}
        uses: actions/upload-pages-artifact@v3
        with:
          path: _site

      - name: 部署到 GitHub Pages
        if: ${{ !cancelled() && (github.event_name == 'schedule' || inputs.deploy != 'false') }}
        id: deployment
        uses: actions/deploy-pages@v4

      - name: 显示固定访问链接（Job Summary）
        if: ${{ steps.deployment.outputs.page_url != '' }}
        run: |
          echo "### ✅ 网站已更新，固定访问链接：" >> $GITHUB_STEP_SUMMARY
          echo "[打开网站](https://你的用户名.github.io/仓库名/)" >> $GITHUB_STEP_SUMMARY
```

### Pages 官方 action 的硬性要求（缺一不可）

| 项目 | 说明 |
|---|---|
| `permissions.pages: write` | deploy-pages 创建部署记录必需 |
| `permissions.id-token: write` | deploy-pages 用 OIDC 换取部署凭证必需 |
| `environment: github-pages` | job 级 environment 名称固定为 `github-pages`（首次部署自动创建，无需手动建） |
| 产物根目录有 `index.html` | upload-pages-artifact 上传的目录里必须有 `index.html`，否则站点 404 |
| 仓库 Settings → Pages → Source = **GitHub Actions** | 未设置时 deploy-pages 会失败或站点不生效（见第 4、5 节） |

---

## 3. 快照生成环节（以 build_snapshot.py 为例）

### 3.1 用法

```bash
python3 scripts/build_snapshot.py --out public/index.html
# 可选参数：
#   --template  templates/index.template.html   模板路径（含 __DATA__ 占位符）
#   --api-base  https://aihot.virxact.com       数据源 API
#   --days      7                                周报窗口天数
```

工作方式：分页抓取 `GET /api/public/items`（翻页参数用 `cursor`，**不是** `nextCursor`——
后者会重复返回第一页，这是实战踩过的坑）→ 按北京时间组装日报（最近有内容的日期）/ 周报（近 7 天）
→ 六版块分组、全局编号、北京时间人话时间 → 用模板渲染出单文件 HTML（数据内嵌 `const DATA = {...}`）。

### 3.2 在 Ubuntu runner 上运行的注意事项

- **用 `python3` 执行**，不是 `python`（Ubuntu 上 `python` 可能不存在，`python3` 一定在）。
- **无需 Node / pnpm 环境**：只要生成脚本是纯 Python（本脚本仅用标准库），runner 自带 Python 3，
  不需要 setup-node，也不用 `npm install`。
- **不受 Windows 下非跨平台写法影响**：本项目 `package.json` 的脚本里
  `WRANGLER_LOG_PATH=... vinext dev` 这种 `KEY=value 命令` 前缀写法在 Windows PowerShell 会报错，
  但在 Ubuntu runner 的 bash 下**天然可用**。如果你的 workflow 根本不需要跑 npm 脚本（如本项目），
  直接绕开整个问题。
- 校验产物：建议在 workflow 里加一个校验步骤（本项目用 Python 断言日报/周报 total > 0 且 JSON 合法），
  抓取失败时让工作流红掉而不是默默部署空页面。

---

## 4. 部署方式对比：CloudBase vs GitHub Pages

本项目先后用了两种部署方式，对比如下：

| 维度 | CloudBase（旧，已弃用） | GitHub Pages（现用） |
|---|---|---|
| 固定链接 | `workbuddy-d6g376q7d19b69f8f-1457344826.tcloudbaseapp.com`（长随机串） | `https://wadeliuastro.github.io/AI-HOT/`（用户名.github.io/仓库名） |
| 密钥要求 | 需 3 个 GitHub Secrets：`TCB_SECRET_ID` / `TCB_SECRET_KEY` / `TCB_ENV_ID` | **零密钥**，只用内置 GITHUB_TOKEN |
| 权限声明 | `contents: write` | `contents: write` + `pages: write` + `id-token: write` |
| 部署命令 | `npm i -g @cloudbase/cli && tcb login --apiKeyId ... --apiKey ... && tcb hosting deploy public/index.html /index.html -e <envId>` | `actions/upload-pages-artifact@v3` + `actions/deploy-pages@v4`（官方 action） |
| 前置条件 | CloudBase 环境存在 + 腾讯云 API 密钥 | 仓库 Settings → Pages → Source 选 **GitHub Actions** |
| 国内访问速度 | 快（腾讯 CDN） | 一般（境外节点） |
| 适用场景 | 需要国内加速、已用腾讯云生态、需要自定义域名备案 | 追求零配置零成本、内容公开、可接受境外访问速度 |

### CloudBase 部署片段（如需回退参考）

```yaml
- name: 安装 CloudBase CLI
  run: npm install -g @cloudbase/cli
- name: 部署到 CloudBase 静态托管
  run: |
    tcb login --apiKeyId "${{ secrets.TCB_SECRET_ID }}" --apiKey "${{ secrets.TCB_SECRET_KEY }}"
    tcb hosting deploy public/index.html /index.html -e "${{ secrets.TCB_ENV_ID }}"
```

---

## 5. 踩坑清单与排查方法

| # | 坑 | 现象 | 原因 | 解决 |
|---|---|---|---|---|
| 1 | **cron 时区** | 定时任务在错误时间执行 | cron 固定 UTC，北京时间需减 8 小时 | 写 `0 0 * * *` 表示北京 08:00 |
| 2 | **GITHUB_TOKEN 权限不足** | `git push` 报 403 / `remote: Permission denied` | token 默认只读 | workflow 顶部显式声明 `permissions: contents: write` |
| 3 | **workflow_dispatch 复选框坑** | 工作流显示"成功"但实际**没部署**（部署四步全 SKIPPED） | 手动触发时 `deploy` 复选框未勾选，条件 `inputs.deploy == 'true'` 不成立 | 条件改写成 `inputs.deploy != 'false'`（只有显式取消才跳过）；并在 Action 页面勾选后运行 |
| 4 | **git push 冲突** | 提交快照步骤失败：`! [rejected] main -> main (fetch first)` | 快照提交与远端/并发运行的提交撞车（non-fast-forward） | 提交后先 `git pull --rebase origin main` 再 `git push` |
| 5 | **Pages 未启用 → 404** | 部署记录存在、工作流绿，但链接 404 | 仓库 Settings → Pages 的 Source 没设为 GitHub Actions（或从未启用 Pages） | Settings → Pages → Source 选 **GitHub Actions**；首次部署后等待 1-2 分钟 CDN 生效 |
| 6 | **页面显示"成功"的假象** | 想确认是否真部署，别只看 job 颜色 | 部署步骤可能被 if 条件跳过 | 展开步骤看 `data-conclusion="skipped"`；或看「部署到 GitHub Pages」步骤是否有日志 |
| 7 | 产物缺 index.html | Pages 站点 404 | 上传目录根没有 index.html | 确保 `_site/` 根目录有 `index.html`（子路径 `/仓库名/` 下亦生效） |
| 8 | `re.sub` 替换 JSON 损坏 | 生成的 HTML 里 JSON 解析失败 | `re.sub` 会把替换串中的 `\n` 解释成真实换行 | 用 `str.replace`（或 lambda 替换）代替 `re.sub` 做占位符替换 |
| 9 | API 翻页参数名 | 抓到的数据只有第一页（重复） | 接口实际参数是 `cursor`，`nextCursor` 会重复返回第一页 | 以实测为准，用 `cursor` 翻页 |

### 排查方法（Actions 页面定位问题）

1. **看步骤颜色**：红 = 失败（点开看日志）；灰/SKIPPED = 被 if 条件跳过（说明条件没满足，不是真执行了）。
2. **看 job 汇总**：Job Summary 会显示 workflow 里写入的链接/结论。
3. **失败步骤展开看原始输出**：`git push` 类错误会给出具体 reject 原因。
4. **对比预期产物**：失败后检查 `public/index.html` 是否被提交回仓库（仓库 → commits 页看 `chore:` 提交）。
5. **Pages 状态确认**：Settings → Pages 页面，部署成功后应出现 "Your site is live at ..." 提示。

---

## 6. 验证链路（部署后按此顺序确认）

1. **Actions 运行成功**：仓库 Actions 页，最新一次运行 11 个步骤全绿（含「部署到 GitHub Pages」有日志，不是 SKIPPED）。
2. **打开固定链接**：`https://你的用户名.github.io/仓库名/`，确认页面正常显示、数据日期为最新；
   首次部署后若 404，等 1-2 分钟再刷（CDN 生效）。
3. **确认快照已提交回仓库**：仓库 Commits 页应有 `chore: 更新静态快照 [skip ci]` 的自动提交
   （内容无变化时会跳过提交，属正常）。
4. **确认 workflow 摘要链接**：运行记录页底部 Job Summary 应显示「✅ 网站已更新，固定访问链接」+
   蓝色可点击链接，与页面实际 URL 一致。
5. **（可选）验证数据正确性**：日报/周报切换、统计数字与内容条数一致。

---

## 附：本项目文件索引

| 文件 | 作用 |
|---|---|
| `.github/workflows/update-snapshot.yml` | 定时 + 手动触发，生成快照并部署到 Pages |
| `scripts/build_snapshot.py` | 纯 Python 快照生成器（抓 API → 渲染 HTML） |
| `templates/index.template.html` | 页面模板（含 `const DATA = __DATA__;` 占位符） |
| `public/index.html` | 生成的最终快照（提交回仓库，供部署与归档） |
| `DEPLOY.md` | 部署说明（含 CloudBase 历史存档） |
