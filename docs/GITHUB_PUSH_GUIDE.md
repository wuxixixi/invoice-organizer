# GitHub 仓库创建与推送指南

本地代码、提交历史与质量验证均已完成，工作区干净。本项目的所有本地交付已就绪，仅 GitHub 发布需要一次**人工认证**（这是 GitHub 的客观安全要求，无法由程序自动跳过）。

## 一、当前状态

- 本地仓库：`H:/fapiao/invoice-organizer`（git init，分支 `main`）
- 提交数：5（ Conventional Commits 规范，由 `.githooks/commit-msg` 强制校验）
- 质量验证：`pytest` 69 passed / 0 failed；`python -m invoice_organizer --diag` 自检全通过；`compileall` 通过
- 敏感信息扫描：源码中未发现硬编码 token / 私钥

## 二、为什么需要人工介入

环境中所有 GitHub 凭据均已失效：
- 环境变量 `GITHUB_TOKEN` → API 返回 401 Bad credentials
- Git 凭据库中的 token → 401
- SSH 密钥 `~/.ssh/id_ed25519` → `Permission denied (publickey)`（公钥未注册到 GitHub）

创建公开仓库需要有效的 API 认证，这是 GitHub 的强制要求。

## 三、最快完成方式：设备码登录（约 1 分钟）

```bash
cd H:/fapiao/invoice-organizer
env -u GITHUB_TOKEN gh auth login --web --git-protocol https
```

执行时会输出一次性验证码和网址（形如 `First copy your one-time code: XXXX-XXXX` 与 `https://github.com/login/device`）。在浏览器打开该网址、输入验证码并授权即可。授权完成后终端会提示 `Authentication complete`。

> 若你有可用的 Personal Access Token（需 `repo` 权限），也可改用：
> `env -u GITHUB_TOKEN gh auth login --with-token < token.txt`

## 四、认证完成后：一键创建并推送

```bash
cd H:/fapiao/invoice-organizer
env -u GITHUB_TOKEN gh repo create invoice-organizer --public --source=. --remote=origin --push --description "科研人员发票整理 Windows 桌面工具：目录监控、识别解析、规则分类、预览确认、归档与台账导出（本地离线）"
```

该命令会创建公开仓库、设置 `origin` 并推送全部 5 个提交。

## 五、推送后建议设为默认 upstream 并核验

```bash
cd H:/fapiao/invoice-organizer
git branch --set-upstream-to=origin/main main
git ls-remote origin main        # 应返回与本地 HEAD 一致的 commit hash
git status -sb                   # 应显示 ## main...origin/main
```

## 六、仓库信息（推送完成后回填）

| 项 | 值 |
|---|---|
| 仓库地址 | `https://github.com/<你的账号>/invoice-organizer` |
| 默认分支 | `main` |
| 提交数 | 5 |
| 描述 | 科研人员发票整理 Windows 桌面工具 |
| 许可证 | 待定（如需开源，建议在仓库设置中添加 MIT / Apache-2.0） |
