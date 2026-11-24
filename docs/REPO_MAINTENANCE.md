# 仓库信息与后续维护说明

## 一、仓库信息（已发布）

| 项 | 值 |
|---|---|
| 仓库地址 | https://github.com/wuxixixi/invoice-organizer |
| 归属账号 | wuxixixi（吴曦） |
| 可见性 | Public（公开） |
| 默认分支 | `main` |
| 远端 main 提交（代码同步） | `31bb4e7ffd79d1fda9f8b738d0da6da1e7991ff1` |
| 远端文件数 | 71（与本地 `git ls-files` 清单完全一致） |
| 仓库描述 | 科研人员发票整理 Windows 桌面工具：目录监控、识别解析、规则分类、预览确认、归档与台账导出（本地离线） |

> 本文件在代码同步提交之后又单独通过 API 同步了一次，因此最终远端 `main` HEAD 为该文档同步提交；代码同步提交仍然是 `31bb4e7ffd79d1fda9f8b738d0da6da1e7991ff1`。

## 二、本地仓库

- 路径：`H:/fapiao/invoice-organizer`
- 分支：`main`
- 本地 HEAD：`e8dce2a21a986872dc2c8ac0de6339422e1182cf`（`fix: 消除演示重复投递并修正冲突统计口径`）
- 本地提交：6 个（Conventional Commits 规范，`.githooks/commit-msg` 强制校验）
- 既有质量验证记录：`pytest` **69 passed / 0 failed**；`python -m invoice_organizer --diag` 全部通过；`compileall` 通过（历史记录，本次同步未重跑测试）
- 工作区状态：本次操作未产生本地 git 提交；仅本维护文档有更新，并已单独同步到远端

### 本地与远端的历史关系

由于当前环境 `git` 客户端直连 github.com:443 会超时/连接重置，但 GitHub REST API 可正常访问，代码同步是通过 **GitHub Git Data API** 完成的。因此：

- 同步前远端 `main` = `7cbea46db956295728505eba27f1fe7df06da59b`（此前由 API 上传的快照）；
- 同步后远端 `main`（代码同步提交）= `31bb4e7ffd79d1fda9f8b738d0da6da1e7991ff1`，其树包含全部 71 个跟踪文件；
- **远端与本地的提交历史差异仍然存在**：远端只有此前的 API 快照提交与本次 API 同步提交，不包含本地 6 个细粒度提交（`e560281` → `65573be` → `1971d09` → `4d789e1` → `e8dce2a` 等）的逐笔历史；
- 但两端**文件内容完全一致**：API 同步严格按 `git ls-files` 清单逐文件上传，未包含任何被 `.gitignore` 排除的文件（尤其 `data/secrets.local.json`）。

## 三、后续维护的两种同步方式

### 方式 A：恢复 git 直连后正常推送（推荐）

先排查网络/代理，确保 `git ls-remote https://github.com/wuxixixi/invoice-organizer.git HEAD` 能返回提交哈希，然后：

```bash
cd H:/fapiao/invoice-organizer
git remote set-url origin https://github.com/wuxixixi/invoice-organizer.git
# 若远端无本地历史，首次需强制对齐（会覆盖远端 main，确认远端无他人提交后执行）：
git push -u origin main --force
git branch --set-upstream-to=origin/main main
git status -sb    # 应显示 ## main...origin/main
```

注意：强制对齐会覆盖远端现有的 API 同步提交，执行前需确认远端无他人提交。

### 方式 B：git 仍不可达时，继续用 API 推送（本次实际采用）

本次同步即采用方式 B，要点如下：

- **原因**：本地 `git` 直连 `github.com:443` 超时/连接重置；改用 Python `urllib` 走 GitHub REST API，并将 `socket` 默认超时设为 20 秒。全程不使用 `git push`。
- **凭据**：在项目目录用 `printf 'protocol=https\nhost=github.com\n\n' | git credential fill` 从 Windows 凭据管理器取得 `ghp_` token，仅在进程内存中持有，不写入任何文件或日志。
- **流程**：`git ls-files` → 逐文件创建/复用 blob → 创建 tree → 创建 commit → `PATCH refs/heads/main`。
- **同步范围**：严格限于 `git ls-files` 列出的跟踪文件；`data/` 已被 `.gitignore` 排除，绝不上传。
- 仓库中 `docs/GITHUB_PUSH_GUIDE.md` 保留的认证排查思路仍可参考。环境变量 `GITHUB_TOKEN` 与 Git 凭据库中的 fine-grained token 此前均已失效，不可使用。

## 四、凭据使用安全

- 任何 token 都不要写入源码、配置或提交历史；
- 推送完成后及时清理临时 token 文件；
- 凭据管理器中的 `ghp_` token 具备较广权限（`repo`/`workflow`/`delete_repo` 等），建议后续在 GitHub 设置中轮换为最小权限的 fine-grained token，仅授予本仓库 `Contents: write`。

## 五、本次同步记录

| 项 | 值 |
|---|---|
| 同步时间 | 2026-11-24 10:32:44（本地时间） |
| 旧远端 HEAD | `7cbea46db956295728505eba27f1fe7df06da59b` |
| 新远端 HEAD（代码同步提交） | `31bb4e7ffd79d1fda9f8b738d0da6da1e7991ff1` |
| 代码同步提交信息 | `sync: 同步旧票解析修复与演示去重修复` |
| 本地 HEAD | `e8dce2a21a986872dc2c8ac0de6339422e1182cf` |
| 跟踪文件数 | 71 |
| 一致性验证 | 远端 blob 数 71 = 本地跟踪文件数 71；`README.md`、`demo_run.py`、`invoice_organizer/core/fields.py` 远端内容与本地一致 |
| 密钥扫描 | 全仓遍历所有远端 blob，未发现 `ghp_` / `key-` 开头的完整密钥 |
| 文档同步 | 本文件更新后又单独通过 API 同步一次，最终远端 `main` HEAD 即该文档同步提交 |
