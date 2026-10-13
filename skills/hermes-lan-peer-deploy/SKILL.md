---
name: hermes-lan-peer-deploy
description: Deploy a LAN Hermes peer sharing skills/memory.
---

# 局域网第二台 Hermes 部署为分布式 peer

目标：让第二台机与本机共用同一套 skill / 记忆 / 配置，并通过 A2A 把任务下发给它、拿回结果。

## 关键教训（先读）

- **install.ps1 默认装最新 main**，会与本机差成千上万个提交。它支持 `-Branch` / `-Commit` 钉版本——装之前先问用户是否要钉到本机同提交。不钉版本的后果不是理论风险：新版向公司 OpenAI 兼容端点发重复 `reasoning` 字段，直接 HTTP 400 `duplicate field 'reasoning'`（本机旧版没这行为）。
- **两机布局可能不同**：旧布局 = `<home>\hermes-agent\venv` + 内置 Python；新布局 = `<home>\tools\` 便携运行时 + `installs\` 环境。跨布局迁移不会自动对齐，症状是模型报错、插件路径不同。
- **钉版本的最稳做法是整树复制**：若两机用户名/路径完全相同，直接 robocopy `hermes-agent` 整树过去，venv 里的绝对路径天然有效，行为逐字节一致。比在新版安装器上回钉旧提交确定得多。
- **robocopy 走 UNC 必须用 PowerShell 脚本文件驱动**：bash 会吞掉前导双反斜杠，把 `\\host\C$\...` 写成 `C:\host\C$\...`（真的建出一个乱目录）。
- **复制后必须重写启动器**，否则命令仍走旧运行时：`<home>\bin\hermes.cmd`、`<home>\gateway-service\Hermes_Gateway.vbs`（计划任务实际调的是 .vbs，不是 .cmd）都要指向 `hermes-agent\venv\Scripts\python.exe`。
- **terminal 工具需要 git-bash，是新旧布局最容易漏的一项**：老布局把 git 放在 `<home>\git`（含 bin/bash.exe），新版放在 `<home>\tools\git-*\`。整树复制 hermes-agent 不会带 `<home>\git`，于是目标机 `terminal` 工具报 `RuntimeError: Git Bash not found`（read_file/search_files 也跟着炸）。修法：把本机 `<home>\git` 整目录复制过去 + 设 User 环境变量 `HERMES_GIT_BASH_PATH=<home>\git\bin\bash.exe`，然后**重启 gateway**（环境变量只对新进程生效）。验证不要只看文件存在，要真跑：`python.exe -c` 里 subprocess 调 bash -lc 'git --version'，再看 rc=0。
- **验证要先看真实宿主名**：A2A 回复里带上对端主机名/IP 才能排除"本机自问自答"；两端若是同构 Windows，对端报的 `uname -s` 会是 `MINGW64_NT-...` 而不是 `Linux`，别误判。

## 远程操作通道

不用装 SSH，也不装 WSL。免密 SMB + WMI 已足够：

1. 探测：`net use \\<ip>\C$` 免密、`schtasks /query /S <ip>` 免密、WMI 远程建进程免密（ReturnValue 0）。
2. 备份本机 WSMan TrustedHosts，启动 WinRM，设 `TrustedHosts=<目标IP>`；经 WMI 在目标机启用 WinRM。
3. 之后用免密 PSRemoting：`Invoke-Command -ComputerName <ip> -ScriptBlock {...}`。

**两种执行上下文，用途不同**：

| 方式 | 能跑 CLI | 能读 UNC 共享盘 | 用途 |
|---|---|---|---|
| `Invoke-Command` (PSRemoting) | 能 | **不能**（双跳限制） | 只读探测、注册任务、传文件 |
| 交互式用户计划任务 | 能 | **能**（等同真实用户会话） | 任何需要共享盘/CLI 的事 |

判定某能力在目标机是否可用，必须用**计划任务**测（WinRM 会话读不到 UNC 会给出假的失败）。计划任务模板：

```powershell
$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\path\script.ps1' -WorkingDirectory 'C:\Users\<u>'
$p = New-ScheduledTaskPrincipal -UserId '<DOMAIN>\<u>' -LogonType Interactive -RunLevel Highest
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName 'X' -Action $a -Principal $p -Settings $s -Force | Out-Null
Start-ScheduledTask -TaskName 'X'; Start-Sleep 45
```

脚本一律把结果写到文件再 `Get-Content`，不要依赖 stdout。执行策略默认 Restricted，必须 `-ExecutionPolicy Bypass`。

## 能力层：skills / memories 双向共用

用**符号链接**指向共享盘，不用 `skills.external_dirs`（后者在 WinRM 双跳下读不到 UNC，列表不生效）。

1. 本机建共享目录并暴露 SMB：`New-SmbShare -Name hermes-shared -Path D:\hermes-shared -FullAccess Everyone` + `icacls ... /grant Everyone:(OI)(CI)M`。
2. 复制本机 `skills` 到共享盘，校验一致（文件数 + MD5）。
3. 两端把 `skills` / `memories` 目录改名为 `<name>.local-bak.<ts>`，再 `cmd /c mklink /D <link> <target>`。目标机用 UNC（`\\<本机可达IP>\hermes-shared\...`）。
4. **双向验证**：本机写一个探针 skill → 目标机计划任务确认可见；反向再确认一次；删探针。

注意：目标机→本机走哪个 IP 要实测（本机多网卡时只有一个 IP 的 445 通）。

## 调度层：A2A

1. 目标机：`platforms.a2a.enabled=true`、`extra.port=9900`，并在 `.env` 写：
   ```
   A2A_HOST=0.0.0.0
   A2A_PEER_TOKENS=host-nb1:<64hex-token>
   A2A_TRUSTED_PEERS=host-nb1
   ```
   **不要用 `A2A_BEARER_TOKEN`**：它把身份回退成调用方 IP，IP 不在白名单就 403 `peer 'ip:x' not trusted`。具名 token 才有可信身份。
2. 本机 `config.yaml` 登记对端：`a2a_agents.<name>.{url, auth:{type:bearer,token}, timeout}`。
3. 端到端测：`curl -X POST http://<ip>:9900/ -H "Authorization: Bearer <token>" -d '{"jsonrpc":"2.0","id":"t","method":"message/send","params":{"message":{"role":"user","parts":[{"kind":"text","text":"hi"}],"messageId":"m1"}}}'`。成功 = HTTP 200 + `TASK_STATE_COMPLETED` 且含真实回复。

`.env` 里 UTF-8 **不要带 BOM**（PowerShell 5.1 的 `Set-Content -Encoding utf8` 会加 BOM）；BOM 会让 key 解析失败。

## gateway 开机自启

`hermes gateway install --force --no-start-now --start-on-login` → 计划任务 `Hermes_Gateway`（登录自启；比开机自启合适，因为要用户身份才能读 UNC 盘）。非交互必须给全参数，否则卡在交互提示。

启动验证不能只看 `Start-ScheduledTask` 的返回：要确认 9900 在监听 **且** 进程命令行确实指向 venv python。

## 下发任务给 peer（本机侧用法）

A2A 客户端工具在 `a2a` 工具集（本机 CLI 已启用），有 5 个：

| 工具 | 作用 | 关键参数 |
|---|---|---|
| `a2a_list` | 列出已配置 peer + 历史会话 + 指标 | 无 |
| `a2a_discover` | 抓取某 URL 的 Agent Card（先摸清对方能力） | `url` |
| `a2a_call` | 发任务并**阻塞取回**回复 | `agent`(peer 名或 URL), `message`, 可选 `context_id` |
| `a2a_orchestrate` | 按 capability 并发扇出到多个 peer | `capability`, `message`, `mode`=all/first/best |
| `a2a_history` | 回放某 context 的持久化会话 | `context_id` |

- 这些工具**配置门控**：只有 config 有 `a2a_agents`、或开了入站 A2A 平台时才注册；**会话中途新增的配置要到下次上下文压缩或新会话才生效**。a2a_list 显示"No peers configured"而 config 里明明有 peer，就是碰到了这个。
- 多轮对话：拿 `a2a_call` 回复里的 `context_id`，下次调用带上就能延续同一上下文。
- 长任务：本机不要直接用 `a2a_call` 同步等（工具调用有超时）；转用 CLI：`hermes peer add <名> --url <对被 API server> --key <KEY>` 然后 `hermes peer run <名> < task.txt` 拿 run id，`hermes peer status <名> <run_id>` 取结果（需对端开 api_server 平台）。

## 提交上游 issue/PR 前的去重清单

动手写 issue 前先搜一遍。把 PAT 放在环境变量 `GH_TOKEN` 里，用 `curl` 的 `--oauth2-bearer "$GH_TOKEN"`（不要在命令行里写明文 token，也不要把它写进 skill 正文）；搜索端点：`https://api.github.com/search/issues?q=repo:NousResearch/hermes-agent+<关键词>`。

| 现象 | 已有上游条目 |
|---|---|
| 带 `reasoning` 别名重放→ HTTP 400 duplicate field | #136486 (issue) + #136487 (PR) — 别再提 |
| Windows 计划任务登录自启静默失败（task 报成功但没起，pm/Python 3.14 无控制台上下文） | #136390 — 别再提 |
| 舞台化 store Git Bash 抢在 HERMES_GIT_BASH_PATH 前 + 健康检查只看 builtin | #127561 + #127601 (PR) — 别再提 |
| WinRM/PSRemoting 双跳读不到 UNC | 搜索结果无直接条目 — 有增量空间 |

结论：这次部署的坑基本都已被上游跟踪，**不值得重复开 issue**；若要贡献，唯一有空白的是"WSL/WinRM 双跳与交互式计划任务的选型"这类运维模式，更适合当作文档/skill 而非 bug issue。

新版模板的 config 有完整注释且 `_config_version` 更高，**不要**直接用旧 config 覆盖（会降级版本）。做法：文本级合并——从本机 config 提取缺失块（`custom_providers`、`model.api_key`、`mcp_servers`、`plugins.entries`、`display.status_bar`、`browser.cloud_provider`、`command_allowlist`、`tinyfish` 等）插进目标机 config，写回前先 `yaml.safe_load` 校验并逐项断言，失败就中止不改文件。复杂结构用 `hermes config set` 写不进去——必须文本级处理。
