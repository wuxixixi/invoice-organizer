# 仓库信息与后续维护说明

## 一、仓库信息（已发布）

| 项 | 值 |
|---|---|
| 仓库地址 | https://github.com/wuxixixi/invoice-organizer |
| 归属账号 | wuxixixi（吴曦） |
| 可见性 | Public（公开） |
| 默认分支 | `main` |
| 远端 main 提交 | `61950f0938fdeb0a2b98c671c031835731e7cfd6` |
| 远端文件数 | 60（含 README、源码、测试、PRD/架构/QA 文档） |
| 仓库描述 | 科研人员发票整理 Windows 桌面工具：目录监控、识别解析、规则分类、预览确认、归档与台账导出（本地离线） |

## 二、本地仓库

- 路径：`H:/fapiao/invoice-organizer`
- 分支：`main`
- 本地提交：6 个（Conventional Commits 规范，`.githooks/commit-msg` 强制校验）
- 质量验证：`pytest` **69 passed / 0 failed**；`python -m invoice_organizer --diag` 全部通过；`compileall` 通过
- 工作区状态：clean

### 本地与远端的历史关系

由于当前环境 `git` 客户端无法直连 GitHub 443（连接超时），但 GitHub REST API 可正常访问，初始代码是**通过 GitHub Git Data API 上传**的。因此：

- 远端 `main` = 2 个提交（初始化 + 全量实现快照），包含全部 60 个文件；
- 本地 `main` = 6 个细粒度规范提交（docs → feat → docs → fix → fix → docs），文件内容与远端**完全一致**；
- 远端不含本地的逐笔提交历史，但代码内容无差异。

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

### 方式 B：git 仍不可达时，继续用 API 推送

仓库中 `docs/GITHUB_PUSH_GUIDE.md` 保留的认证排查思路仍可参考。核心凭据来自 Windows 凭据管理器目标 `git:https://github.com`（用户 `wuxixixi`，token 具备 `repo` 权限）；环境变量 `GITHUB_TOKEN` 与 Git 凭据库中的 fine-grained token 均已失效，不可使用。

用 API 同步单个文件（示例）：

```python
# PUT /repos/wuxixixi/invoice-organizer/contents/<path>
# {"message":"fix: ...","content":"<base64>","sha":"<原文件blob sha>","branch":"main"}
```

## 四、凭据使用安全

- 任何 token 都不要写入源码、配置或提交历史；
- 推送完成后及时清理临时 token 文件；
- 凭据管理器中的 `ghp_` token 具备较广权限（`repo`/`workflow`/`delete_repo` 等），建议后续在 GitHub 设置中轮换为最小权限的 fine-grained token，仅授予本仓库 `Contents: write`。
