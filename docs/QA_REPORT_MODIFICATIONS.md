# QA 验证报告 — 三项修改（PyInstaller 打包 / DeepSeek-OCR-2 / 大模型结构化）

| 项目 | 内容 |
|---|---|
| 文档语言 | 简体中文 |
| 验证人 | 严过关（QA 工程师 · software-qa-engineer-3） |
| 项目根目录 | `H:/fapiao/invoice-organizer` |
| 上游变更 | T10（三项修改）、T11（测试真实联网修复）、T12（真实链路暴露的 OCR 空输出/HTML 表格修复） |
| 验证方式 | 原样命令复跑 + 源码逐行审查 + 独立网络拦截实验 + 密钥全仓/历史扫描 + **真实 OCR 服务链路验证** |
| 测试轮次 | Round 1（82 passed）→ 工程师修复 OCR 真实链路后 Round 2（83 passed） |
| **最终路由判定** | **NoOne（通过）**；附带 3 项非阻塞遗留问题 |

> 声明：本报告不复用工程师结论。三项验证命令均在本机独立复跑两轮；适配器、密钥、打包配置均按源码逐项核对，并补充了工程师测试中未覆盖的独立实验（socket 级外网拦截、系统代理读取探针、git 全历史密钥扫描、真实 OCR 服务调用）。
>
> 边界遵守：真实调用**仅针对 OCR 服务 `172.19.1.2`**；计费服务 `172.19.1.10`（LLM）全程只用 mock，未发起任何真实请求。
>
> 扫描说明：为免本报告自身被后续密钥扫描命中，下文将真实 Key 前缀以 `oiC***` 掩码表示；实际扫描命令使用的是团队给定的完整前缀字面量。

---

## 一、三项验证原始输出（原样记录）

### 1.1 `python -m compileall -q invoice_organizer`

```
===== [1] compileall =====
compileall exit=0
```

退出码 0，无语法错误输出。

### 1.2 `python -m pytest -q`（连续两次）

```
===== [2] pytest run #1 =====
........................................................................ [ 87%]
..........                                                               [100%]
82 passed in 1.38s
===== [2] pytest run #2 =====
........................................................................ [ 87%]
..........                                                               [100%]
82 passed in 1.29s
```

两次均为 `82 passed`，耗时 1.38s / 1.29s，结果与稳定性符合预期。**这是 Round 1（OCR 真实链路修复前）的原始输出，保留备查。**

### 1.3 `python -m invoice_organizer --diag`

```
===== [3] diag =====
invoice_organizer v1.0.0 自检（schemaVersion=1）
Python: 3.13.14
- 共享常量 -
  [OK] 八字段键顺序
  [OK] 错误码数量
- 配置 -
  [OK] 默认配置校验
  [OK] 默认配置序列化往返
  [OK] 配置加载器可用
  [OK] OCR/LLM 配置校验
  [OK] OCR/LLM 密钥默认留空
  [OK] 冻结资源：index.html
  [OK] 冻结资源：default.json
- 工具 -
  [OK] SHA-256 字节
  [OK] 日期归一
  [OK] 元转分
  [OK] 路径守卫
- 存储 -
  [OK] SQLite 建表

自检全部通过。
```

退出码 0，14 项自检全绿。

### 1.4 Round 2 回归原始输出（工程师修复 OCR 真实链路后复跑）

```
=== Round2 compileall ===
compileall exit=0
=== Round2 pytest #1 ===
........................................................................ [ 86%]
...........                                                              [100%]
83 passed in 1.37s
=== Round2 pytest #2 ===
........................................................................ [ 86%]
...........                                                              [100%]
83 passed in 1.45s
=== Round2 diag ===
invoice_organizer v1.0.0 自检（schemaVersion=1）
Python: 3.13.14
- 共享常量 -
  [OK] 八字段键顺序
  [OK] 错误码数量
- 配置 -
  [OK] 默认配置校验
  [OK] 默认配置序列化往返
  [OK] 配置加载器可用
  [OK] OCR/LLM 配置校验
  [OK] OCR/LLM 密钥默认留空
  [OK] 冻结资源：index.html
  [OK] 冻结资源：default.json
- 工具 -
  [OK] SHA-256 字节
  [OK] 日期归一
  [OK] 元转分
  [OK] 路径守卫
- 存储 -
  [OK] SQLite 建表

自检全部通过。
```

Round 2：83 passed（新增 1 条 HTML 清洗用例），两次均通过；compileall / diag 仍全绿。

### 1.5 「无真实内网请求」专项核查（重要澄清）

启用 pytest 实时日志后，全量 82 用例的日志中**确实出现 1 条** `大模型结构化失败，保持原提取结果：OSError: offline`（`llm_client.py:148`）和 1 条 `DeepSeek OCR 识别失败，降级为空结果：OSError: network down`（`deepseek_ocr.py` 异常降级分支）。经溯源，**这两条均来自刻意构造的 mock 失败用例**，不是真实内网请求：

- `tests/test_llm_client.py:99-102`：`failing.open = lambda *_a, **_k: raise OSError("offline")`，假 opener，域名 `http://llm.invalid`；
- `tests/test_deepseek_ocr.py:124-127`：`_FakeOpener(error=OSError("network down"))`，完全无 socket 调用。

独立复验（排除这两个用例后）：

```
=== pytest excluding the deliberate mocked-failure test, live warnings ===
exit=0
(no OCR/LLM failure warnings)
80 passed, 2 deselected in 1.49s
```

**socket 级外网拦截实验**（QA 独立编写，patch `socket.socket.connect` / `connect_ex`，禁止一切非环外连接后跑全量测试）：

```
=== pytest under socket-level outbound network guard (non-loopback blocked) ===
........................................................................ [ 87%]
..........                                                               [100%]
82 passed in 1.42s
```

结论：**测试期间无任何真实内网/外网请求**；出现的告警是异常降级路径被刻意触发的预期日志，不构成缺陷。测试隔离由 `OrganizeEngine(allow_network=False)`、`conftest.py` 的 autouse `no_real_llm_client` fixture、`config.llm.enabled=False` 三重保障，审查确认有效。

---

## 二、适配器代码审查结论（读源码，不依赖测试结果）

### 2.1 `invoice_organizer/parsers/deepseek_ocr.py` — 通过（含修复后复核）

> 行号按 Round 2 修复后版本（193 行）标注；Round 1 版本的审查结论一致，修复只改提示词/清洗/token，未破坏原有安全与直连约束。

| 审查项 | 证据（行号） | 结论 |
|---|---|---|
| 无 Authorization | `L70-89` 接收 `apiKey` 后立即 `self.apiKey = ""` + `del apiKey`；`L176-178` 调用 `post_json()` **不传 headers** | 通过。任何情况下都不会发送 Key |
| 端点/模型 | `L21-22` `http://172.19.1.2:30080/v1/chat/completions`、`model="default"` | 与要求一致 |
| 多模态 content | `L164-174`：`content` 为 list，含 `{"type":"text"}` + `{"type":"image_url"}` 两个 part | 通过 |
| 图片 data URI base64 | `L91-110` 路径/bytes/PIL 三入口；`L59-62` 扩展名映射 `jpg→jpeg`、`tif→tiff`；`L110` `base64.b64encode`；`L171` `data:image/{ext};base64,{encoded}` | 通过，MIME 与扩展名匹配 |
| 响应解析 | `L112-131` 取 `choices[0].message.content`，兼容 str 与 list[text] | 通过 |
| **真实链路修复** | `L24-27` 极简提示 `OCR_PROMPT="输出图片中的所有文字。"` + `OCR_MAX_TOKENS=2048`；`L174` 传 `max_tokens`；`L177` 不再传 `temperature` | 通过；真实调用 `finish_reason=stop`、content 非空 |
| **HTML 清洗** | `L30-57` `_BLOCK_TAGS` + `_HtmlTextExtractor(HTMLParser)` 保留标签内文本、布局标签转换行；`L133-157` `_clean_content()` 先 HTMLParser、异常时正则兜底，再 `html.unescape` 与空行压缩 | 通过；容忍 `<th>` 无闭合，实测输出无 `<table>` |
| 异常降级 `[]` | `L184-187` 捕获所有异常，warning 后 `return []`；`L180-181` 空文本同样 `[]` | 通过，绝不阻塞主流程 |
| 直连 | `L19` 从 `utils.net` 导入 `post_json`，未注入 opener → 默认走 `direct_opener()` | 通过 |

### 2.2 `invoice_organizer/core/llm_client.py` — 通过

| 审查项 | 证据（行号） | 结论 |
|---|---|---|
| Bearer 注入 key | `L130-132` 仅当 `self.apiKey` 非空才加 `Authorization: Bearer ...`；`L53-57` key 由构造参数传入（来自运行时注入） | 通过 |
| 端点/模型 | `L17-18` `http://172.19.1.10:8080/api/v1/chat/completions`、`DeepSeek-V4-Flash-0731` | 与要求一致 |
| 直连 | `L15` 统一走 `utils.net.post_json`，无自定义 opener | 通过 |
| 八字段解析 | `L99-107` 按 `FIELD_KEYS` 映射八字段（`amountCent` 兼容 `amount` 别名），过滤未知键；`L81-96` 容忍 Markdown 代码块与 `{"fields":{...}}` 包装 | 通过 |
| 响应解析 | `L60-79` `choices[0].message.content` | 通过 |
| 失败返回 `{}` | `L147-150` 捕获所有异常，warning 后 `return {}`；`L118-119` 空文本直接 `{}`；`L82-96` 非法 JSON `{}` | 通过，不崩溃 |
| 兼容性重试 | `L138-145` 400/404/422 时去掉 `response_format` 重试一次 | 合理 |

### 2.3 `invoice_organizer/utils/net.py` — 通过（关键直连点）

`L20`：`return urllib.request.build_opener(urllib.request.ProxyHandler({}))`。

`post_json()`（`L23-55`）在 `opener is None` 时使用该 opener（`L49`），因此 OCR 与 LLM 两个调用方默认均直连。

> QA 独立探针（修正了初版探针的错误断言）：在 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY` 均设为 `http://10.0.0.1:8888` 的环境下，`direct_opener()` 对 `urllib.request.getproxies()` 的调用次数为 **0**；默认 `build_opener()` 为 **1**。证明环境代理/安全网关配置不会被读取，`ProxyHandler({})` 直连策略真实生效。
>
> 说明：空 `ProxyHandler` 因没有代理 scheme 而不出现在 `opener.handlers` 列表中，这是 CPython 的正常行为，不代表直连失效；工程师测试 `test_deepseek_ocr.py:110-117` 对 `ProxyHandler({})` 的断言方式正确。

### 2.4 `invoice_organizer/core/fields.py` — 通过

| 审查项 | 证据（行号） | 结论 |
|---|---|---|
| 只补全缺失/低置信 | `L189-191`：`if existing.verified or existing.confidence == Confidence.HIGH: continue` | 通过，**high 与人工已核验字段永不覆盖**；只作用于 `low` / `need_human` |
| 证据标注 | `L195-198`：`FieldSlot(confidence=LOW, evidence=Evidence(sourceFile="llm", rawText=str(raw)))` | 通过，置信 `low`、证据 `llm` |
| 不臆造 | `L147-164` 逐类型归一化校验：日期归一、金额转分再转元、税号 18 位字符集、票号 8-20 位数字；非法值返回 `""` 跳过 | 通过 |
| 开关与容错 | `L173-182`：`llm.enabled=False` 直接返回；客户端异常捕获后保留原结果 | 通过 |
| 冗余字段同步 | `L199-203` 同步 `invoiceNo` / `issueDate` / `amountCent`，避免槽位与冗余字段漂移 | 通过 |
| 调用位置 | `L138-139` 在八字段提取、行项目合并之后，排序之前 | 合理 |

### 2.5 编排层接线（`core/engine.py` / `parsers/image_ocr.py` / `parsers/registry.py`）

- `engine.py L53-97`：密钥解析优先 `INVOICE_LLM_KEY` 环境变量，其次 `data/secrets.local.json`；`allow_network=False` 时不读取密钥、不构造客户端。
- `engine.py L124-129, L151-160`：初始化与 `start()` 重载配置两条路径均注入 key、传 `ocr_config` 给注册表、按开关构造 LLM 客户端，路径一致无遗漏。
- `image_ocr.py L198-218`：默认后端按 `ocr.provider=deepseek_ocr` 优先构造 DeepSeek，未配置端点/其它 provider 才回退 tesseract/text-layer；`L244-273` DeepSeek 无结果且有 pytesseract 时再降级一次，最终 E402 待人工确认。
- `image_ocr.py L275-311`：子进程只接收可 pickle 的后端名 + 配置字典，`join(timeout)` 超时 `terminate()`/`kill()`，异常隔离。
- `registry.py L16-27`：`ocr_config` 透传给 `ImageOcrAdapter`。

---

## 二A、真实链路验证（OCR 修复后，真实服务）

### 背景与范围

团队负责人用真实 DeepSeek-OCR-2 服务发现单测 mock 掩盖的问题：原长中文 `OCR_PROMPT` + `temperature=0` 导致 content 为空；改短提示后返回 `<table>` 布局且 `finish_reason=length` 被截断。工程师随后修复（极简提示 + HTML 清洗 + `max_tokens`）。本节为**修复后独立复验**。

- 调用范围：**仅** `http://172.19.1.2:30080/v1/chat/completions`（OCR，无 Key、不计费）。
- **未**对 `http://172.19.1.10:8080`（LLM，Bearer Key 计费）发起任何真实请求；LLM 仍仅 mock 验证。
- 图片：PIL 生成 1000×360 白色合成票面，ASCII 文本（避免中文默认字体变方框）：
  `INVOICE NO 24312000000012345678` / `DATE 2024-11-24` / `TOTAL 1308.63` / `BUYER ABC RESEARCH INSTITUTE`。
- 方法：临时包装 `deepseek_ocr.post_json`（断言端点与无 headers，然后委托真实 `post_json` 发请求），调用 `DeepSeekOcrAdapter(timeoutSec=60).recognize(path)`，同时抓取原始响应元数据。临时图片在验证后已删除，未留在仓库。

### 原始输出

```
=== request ===
endpoint: http://172.19.1.2:30080/v1/chat/completions
prompt: '输出图片中的所有文字。'
payload keys: ['max_tokens', 'messages', 'model']
max_tokens: 2048
temperature present: False
=== raw response ===
finish_reason: stop
usage: {'prompt_tokens': 696, 'total_tokens': 758, 'completion_tokens': 62, 'prompt_tokens_details': None, 'completion_tokens_details': None}
raw content length: 98
raw content head: '<table>INVOICE NO2431200000012345678DATE2024-11-24TOTAL1308.63BUYER ABC RESEARCH INSTITUTE</table>'
=== cleaned recognize() result ===
fields count: 1
text length: 83
text: 'INVOICE NO2431200000012345678DATE2024-11-24TOTAL1308.63BUYER ABC RESEARCH INSTITUTE'
contains <table>: False
contains <th>: False
RESULT: PASS - real OCR chain returns >=1 field and no HTML table markup
```

### 判定

- `recognize()` 返回 **1 个字段**（≥1，通过）；
- 清洗后 text **不含 `<table>` / `<th>`**（通过）；
- 原始响应 `finish_reason=stop`（不再是 `length`），content 长度 98（不再为空），`max_tokens=2048` 生效（通过）。

**真实链路验证结论：PASS。** 该问题在 Round 1 的单测中不可见，本轮补齐后确认修复有效。

### 观察项（非缺陷，记录供下游留意）

真实模型把 20 位票号 `24312000000012345678` 识别为 19 位 `2431200000012345678`（少一位 0），且标签与数值之间无分隔符（`INVOICE NO2431...`、`DATE2024-11-24`）。这属于 OCR 模型本身的识别精度/排版行为，不是适配器实现缺陷；下游 `FieldExtractor` 的票号校验（8–20 位数字）与日期/金额正则仍可命中，且低置信字段会进入待复核队列，符合"整理辅助建议、最终人工判断"的设计原则。

---

## 三、密钥安全扫描（最高优先级）

| 检查项 | 命令 / 证据 | 结果 |
|---|---|---|
| 跟踪文件中真实 Key | `git grep -n "oiC***" -- .` | **无输出**（exit=1），通过 |
| 全工作区（排除 `.git`、`data/`、缓存） | `grep -rn -I --exclude-dir=.git --exclude-dir=data ... -e "oiC***" .` | **无输出**（exit=1），通过 |
| git 全历史增量搜索 | `git log --all --oneline -S"oiC***"` | **无输出**，历史中从未提交真实 Key |
| 所有 commit 对象搜索 | `git grep -n "oiC***" $(git rev-list --all)` | **无输出**，通过 |
| `ProfileConfig.to_dict()` | `config/schema.py L282-292`：`payload = asdict(self)` 后强制 `ocr.apiKey=""`、`llm.apiKey=""` | 通过 |
| 全部序列化出口 | `to_json()`（L294-296）、`loader._write_version()`（L129-139）、`loader.snapshot()`（L143-145）、`bridge.get_config()`（L164-170）、`bridge.db_save_profile()`（L185-192）**均经 to_dict/to_json** | 通过，配置快照与 JsBridge 不泄漏 |
| `default.json` | `L74:"apiKey": ""`、`L81:"apiKey": ""` | 两处均为空字符串，通过 |
| `data/secrets.local.json` 忽略状态 | `git check-ignore -v data/secrets.local.json` → `.gitignore:14:data/`；`git ls-files data/` → **空** | 文件本地存在但被忽略、未被 git add，通过 |
| 测试注入演练 | `tests/test_llm_client.py:123-153`：注入假 key 后 `to_dict/to_json/snapshot` 均不含该 key | 通过 |

**结论：真实 Key 未进入任何受 git 跟踪的文件、提交历史、配置快照或桥接响应。密钥安全项全部通过。**

---

## 四、打包配置审查（`build.spec` / `build.bat` / `utils/paths.py`）

### 4.1 `build.spec`

- 入口 `L24`：`Analysis([root/invoice_organizer/__main__.py], pathex=[root])`。`__main__.py` 内部使用绝对导入且 `run_app`/`run_diag` 延迟导入 GUI，配合 `pathex` 可作为脚本入口；与 `python -m invoice_organizer` 行为一致。
- 资源收集 `L13-21`：`ui/*.html|js|css` → `_MEIPASS/invoice_organizer/ui`；`config/*.json` → `_MEIPASS/invoice_organizer/config`。覆盖了 `AppWindow` 加载的 `index.html` 与 `ProfileLoader.default_config()` 读取的 `default.json`。
- `hiddenimports` `L28`：`webview / pdfplumber / openpyxl`，覆盖延迟导入的 GUI 与导出依赖；`watchdog` 在源码中无顶层静态导入需求（`core/watcher.py` 使用轮询兜底，无强制外部依赖）。
- 单文件：`EXE(pyz, a.scripts, a.binaries, a.datas, ...)`，无 `COLLECT` → onefile，符合 README 描述。
- `console=False`（`L49`）：windowed 无控制台，符合桌面应用定位；`upx=True` 仅压缩，不影响逻辑。

### 4.2 `invoice_organizer/utils/paths.py`

- `is_frozen()`（`L214-216`）检测 `sys.frozen`。
- `resource_root()`（`L218-222`）：冻结时 `sys._MEIPASS`，源码时 `Path(__file__).resolve().parents[2]`（仓库根）——路径层级正确。
- `runtime_root()`（`L228-234`）：冻结时 `sys.executable` 所在目录，源码时仓库根；可写数据与密钥**不写入 `_MEIPASS` 临时解压目录**，避免退出丢失。
- 消费方核查：`ui/app.py L55` 用 `resource_path("invoice_organizer/ui/index.html")`；`config/loader.py L17, L54` 用 `resource_path("invoice_organizer/config/default.json")`；`__main__.py L16-20` 用 `runtime_root()` 定位 data。**资源/数据分离逻辑闭合。**

### 4.3 `build.bat`

`chcp 65001` + `cd /d "%~dp0"` + `python -m PyInstaller build.spec --noconfirm --clean`，错误码检查与产物提示齐全，与 README 命令一致。

**限制说明**：本环境 `PyInstaller installed: False`，未实际产出 exe；打包部分为静态配置审查 + `--diag` 对冻结资源路径的模拟校验，**未做真实 Windows exe 构建/运行验证**。

---

## 五、遗留问题（非阻塞，不影响本次通过判定）

1. **P2 — OCR 超时配置不一致**
   - 证据：`config/default.json L68` `execution.ocrTimeoutSec=20`；`L75` `ocr.timeoutSec=60`。
   - `core/engine.py L186-188` 用 `execution.ocrTimeoutSec` 构造 `ParseContext`；`parsers/image_ocr.py L295` 以该值作为子进程 `join(timeout)` 硬上限并 `terminate()`；而 `parsers/deepseek_ocr.py L87/L174` / `image_ocr.py L152-156, L209-212` 用 `ocr.timeoutSec=60` 作为 HTTP 请求超时。
   - 影响：README 未说明两者关系，用户若把 `ocr.timeoutSec` 调到 60 期望更长识别时间，实际仍会在 20s 被父进程杀掉并触发 E401/重试。功能不崩溃，但配置项语义容易误导。
   - 建议：统一为单一超时源（如让 `ParseContext.timeoutSec = max(execution.ocrTimeoutSec, config.ocr.timeoutSec)`），或在 README 明确"子进程硬超时优先"的层级关系。

2. **P3 — 打包运行验证缺口**
   - `console=False` 时，打包版 `--diag` 的 `print()` 输出在 Windows 无控制台可见；且本环境未安装 PyInstaller，未执行真实构建。
   - 建议：如需要现场排障，可另出 `build_debug.spec`（`console=True`）或把 diag 结果写入 `data/diag.txt`。

3. **日志澄清（非缺陷）**：全量测试会出现 1 条 mock 触发的 `大模型结构化失败` warning，来源 `tests/test_llm_client.py:99-102`。若团队要求测试日志字面零告警，可在该用例内局部静音 logger；**不建议**以牺牲异常路径可观测性为代价。socket 拦截实验已证明无真实请求，故本次未修改测试。

---

## 六、测试报告摘要

```markdown
# Test Report

## Summary
- Round 1: 82 passed / 82（1.38s / 1.29s）｜Round 2（OCR 修复后）: 83 passed / 83（1.37s / 1.45s）｜Failed: 0
- 自检: python -m invoice_organizer --diag → 14/14 [OK]，退出码 0（两轮均通过）
- 编译: python -m compileall -q invoice_organizer → 退出码 0（两轮均通过）
- 网络隔离: 非环回 socket 连接全部拦截后 82 passed → 单测期间无真实内网请求
- 真实链路: 仅 OCR 服务真实调用 → 1 字段、无 `<table>`、`finish_reason=stop` → PASS；LLM 服务未真实调用
- 密钥扫描: tracked / worktree / 全历史 / 全 commit 均无真实 Key
- 路由决策: NoOne
- 轮次: Round 1 + Round 2（工程师修复真实 OCR 链路后的回归），已达 2 轮上限

## Failed Tests
- 无

## Known Issues
- P2 OCR 超时配置不一致（execution.ocrTimeoutSec=20 硬杀 vs ocr.timeoutSec=60）
- P3 未执行真实 PyInstaller 构建；windowed 模式下 --diag 输出不可见
- 观察：真实 OCR 将 20 位票号识别为 19 位、标签与数值无分隔（模型精度行为，非适配器缺陷）
```

## 七、最终路由判定

**Send To: NoOne** — 三项修改及后续 OCR 真实链路修复均通过独立验证，未发现需回退给工程师的源码缺陷；测试自身无需修复。两轮测试全部通过，已达 2 轮上限，不再进入第三轮。上述 3 项遗留问题属配置语义/验证覆盖/模型精度改进建议，不影响交付。

**未执行操作声明**：全程未执行 `git add` / `git commit`；未修改任何源码或测试用例；未打印或外传真实密钥内容；仅新增本报告文件 `docs/QA_REPORT_MODIFICATIONS.md`。
