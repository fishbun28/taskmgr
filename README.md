# Taskmgr

个人定时任务管理器，基于 **systemd timers** 的轻量级封装。

不想每次写 crontab，也不想维护分散在各处的脚本？Taskmgr 让你用一条命令集中管理所有定时任务，底层交给 systemd 处理调度、日志和故障恢复。

---

## 特性

- **一行命令管理**：添加、删除、查看状态、查日志，全部一个 CLI 搞定
- **三种 Schedule 语法**：支持预设（daily/hourly）、cron 表达式（`0 2 * * *`）、原生 systemd OnCalendar
- **零常驻进程**：依赖 systemd 调度，不额外占用内存
- **开箱即用的日志**：自动接入 `journalctl`，支持 `--follow` 实时追踪
- **用户级运行**：无需 root，任务在 user session 下执行
- **断电/重启自动恢复**：systemd 原生保障

---

## 安装

需要 Python 3.9+，以及支持 systemd 的 Linux 发行版。

```bash
cd ~/WorkDir/Code/taskmgr
uv tool install -e .
# 或 pip install -e .
```

安装后全局可用：
```bash
taskmgr --help
```

---

## 快速开始

### 1. 添加任务

```bash
# 每天凌晨 2 点执行
taskmgr add "清理下载" --schedule "daily" --exec "fish ~/scripts/clean.fish"

# 用 cron 表达式：每 6 小时执行
taskmgr add "备份笔记" --schedule "0 */6 * * *" --exec "rsync -a ~/Notes/ ~/Backups/"

# 原生 systemd OnCalendar：每周一 9 点
taskmgr add "周报" --schedule "Mon *-*-* 09:00:00" --exec "python ~/scripts/weekly.py"
```

### 2. 查看任务

```bash
taskmgr list
```

输出示例：

```
                              Taskmgr Tasks
┏━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Name      ┃ Schedule   ┃ Command                  ┃ Status ┃ Next Run              ┃
┡━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━┩
│ 清理下载  │ daily      │ fish ~/scripts/clean.fish│ active │ Tue 2026-05-20 00:00:00 CST │
│ 备份笔记  │ *-*-* 00/6:│ rsync -a ~/Notes/        │ active │ Tue 2026-05-19 18:00:00 CST │
└───────────┴────────────┴──────────────────────────┴────────┴─────────────────────────────┘
```

### 3. 手动触发与查看日志

```bash
# 立刻执行一次
taskmgr run "清理下载"

# 查看最近日志
taskmgr logs "清理下载"

# 实时追踪日志
taskmgr logs "备份笔记" --follow
```

### 4. 启用/禁用/删除

```bash
taskmgr disable "清理下载"   # 暂停调度
taskmgr enable "清理下载"    # 恢复调度
taskmgr remove "清理下载"    # 彻底删除
```

---

## Schedule 语法对照

| 需求 | 写法示例 |
|------|----------|
| 每分钟 | `*-*-* *:*:00` 或 cron: `* * * * *` |
| 每小时整点 | `hourly` 或 cron: `0 * * * *` |
| 每天凌晨 2 点 | `daily` + `taskmgr add ... --exec "..."`（daily 固定为 00:00）或 cron: `0 2 * * *` |
| 每周一 9 点 | `Mon *-*-* 09:00:00` |
| 每月 1 号 | `monthly` 或 `*-01-01 00:00:00` |
| 每 5 分钟 | cron: `*/5 * * * *` |
| 每 6 小时 | cron: `0 */6 * * *` |

> **注意**：cron 转换目前仅支持简单格式（`*`、固定数字、`* /N` step），复杂的范围/组合（如 `1-5`、`,` 列表、weekday + day-of-month 同时指定）会提示你直接使用 systemd OnCalendar 语法。

---

## 技术原理

Taskmgr 本身只负责**生成配置**和**管理元数据**，真正的调度完全交给 systemd：

```
你输入的命令
    ↓
taskmgr 生成 systemd user unit
    ~/.config/systemd/user/taskmgr-<name>.service
    ~/.config/systemd/user/taskmgr-<name>.timer
    ↓
调用 systemctl --user enable --now taskmgr-<name>.timer
    ↓
systemd 负责：调度执行 / 日志采集 / 失败状态 / 重启恢复
```

元数据保存在 `~/.config/taskmgr/tasks.json`，方便 `taskmgr list` 做富文本展示。

---

## 文件结构

```
~/.config/taskmgr/tasks.json          # 任务元数据
~/.config/systemd/user/taskmgr-*.service  # 生成的 service unit
~/.config/systemd/user/taskmgr-*.timer    # 生成的 timer unit
```

---

## 局限

- **Linux only**：依赖 systemd，macOS / Windows 不适用
- **Cron 转换有限**：复杂 cron 表达式需要手动写成 systemd OnCalendar
- **User session 运行**：如果你需要用户退出后任务仍执行，需额外运行 `loginctl enable-linger $USER`

---

## 卸载

```bash
uv tool uninstall taskmgr
# 手动清理生成的 unit 文件
rm ~/.config/systemd/user/taskmgr-*.{service,timer}
rm -rf ~/.config/taskmgr
```
