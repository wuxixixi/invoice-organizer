# 科研人员发票整理桌面软件（invoice_organizer）

报销前**本地**整理工具：把发票集中放入文件夹，自动识别、分类、重命名、归档并生成整理结果。
所有整理结果均为「整理辅助建议」，最终判断权在人工。

---

## 1. 功能概览

| 能力 | 说明 |
|---|---|
| 输入监控与写稳检测 | 监控投放目录，文件大小 + mtime 两轮一致且无共享写句柄后才入队 |
| 解析适配器 | PDF 文本层 / OFD-XML / 图片 OCR（可替换后端、受限子进程、超时终止）；无引擎时降级为「待人工确认」 |
| 八字段提取 | 票号 / 开票日期 / 购买方名称及税号 / 销售方名称及税号 / 项目名称 / 金额；每字段带证据与置信 |
| 金额与日期规范 | 金额整数分存储，默认取合计不含税；日期统一 `YYYY-MM-DD` |
| 去重与冲突 | SHA-256 去重，五类情形（同号同哈希 / 同号异哈希 / 异号同哈希 / 一 PDF 多票 / 新文件）分支处理 |
| 规则分类 | 按 `priority` 升序匹配，首条命中即停；未命中归入「未分类」 |
| 计划预览与确认 | 展示旧→新路径、命中规则、冲突与缺字段；`previewRequired=true` 时未确认不得写盘 |
| 事务化归档 | 原件只读永不删除；副本原子写、不覆盖重名、写后 SHA-256 复核、失败回滚 |
| 本地元数据库 | SQLite 记录文件哈希、字段证据、批次、计划、归档、冲突、复核与审计；本地离线 |
| 导出 | Excel / CSV（默认 UTF-8-BOM）台账、批次报告、待复核清单 |
| 配置版本化 | `profileId` 多档案 + 版本递增；批次绑定配置快照，历史可回溯 |
| 权限与审计 | 本地角色（管理员 / 操作员 / 只读）+ append-only 审计日志 |

### 产品边界（强制约束）

本产品**只做信息提取与整理**，明确不做以下事项：

- 不判断发票真伪；
- 不下审计 / 税务合规结论；
- 不认定重复报销（仅做文件级去重与同号异件冲突提示，是否重复报销由人判断）；
- 不生成记账凭证；分类标签 ≠ 会计科目；
- 不宣称适配财政部电子凭证会计数据标准。

**数据可信原则**：看不清 / 无法提取的字段一律标注「待人工确认」，不臆造；所有字段保留证据与置信状态。

---

## 2. 安装步骤

环境要求：Python 3.13.x（当前已在 3.13.14 验证）。

```bash
# 1) 进入项目根目录
cd H:/fapiao/invoice-organizer

# 2) 创建并激活虚拟环境（可选但推荐）
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 3) 安装依赖
python -m pip install -r requirements.txt

# 4) 自检（不需要 GUI / OCR 引擎）
python -m invoice_organizer --diag
```

> 说明：所有第三方库（pdfplumber / Pillow / openpyxl / pywebview / watchdog / pytesseract）均为**可选增强**。
> 缺少任一库时核心逻辑与 `--diag`、`pytest` 仍可正常运行，相关能力优雅降级（如无 OCR 引擎 → 字段标「待人工确认」，无 pywebview → 无法启动桌面窗口但可正常自检）。

---

## 3. 配置说明

配置以 JSON 持久化到 `data/profiles/<profileId>/`，每次保存 `version + 1`，历史版本保留。
默认配置见 `invoice_organizer/config/default.json`。完整示例：

```json
{
  "profileId": "default",
  "version": 3,
  "schemaVersion": 1,
  "watch": {
    "path": "D:/inbox",
    "recursive": true,
    "settleSeconds": 3,
    "maxSettleWait": 60
  },
  "output": {
    "archiveRoot": "D:/archive",
    "quarantine": "D:/quarantine"
  },
  "organizeBy": "invoiceNo",
  "categories": [
    {
      "id": "travel",
      "label": "差旅",
      "priority": 10,
      "match": { "field": "itemName", "contains": "交通" }
    },
    {
      "id": "office",
      "label": "办公",
      "priority": 20,
      "match": { "field": "itemName", "regex": "办公|耗材" }
    }
  ],
  "fields": [
    "invoiceNo", "issueDate", "buyerName", "buyerTaxId",
    "sellerName", "sellerTaxId", "itemName", "amountCent"
  ],
  "amountSource": "totalExclTax",
  "directoryTemplate": "{issueDate}/{sellerName}",
  "filenameTemplate": "{issueDate}_{sellerName}_{amountCent}_{invoiceNo}",
  "export": {
    "template": "default",
    "columns": [
      "invoiceNo", "issueDate", "sellerName", "amountCent",
      "categoryLabel", "archivedPath", "batchId", "profileVersion", "fieldStatus"
    ],
    "encoding": "UTF-8-BOM",
    "format": "xlsx"
  },
  "execution": {
    "previewRequired": true,
    "preserveSource": true,
    "dedupe": true,
    "ocrRetry": 2,
    "ocrTimeoutSec": 20
  }
}
```

### 关键字段说明

| 配置 | 说明 |
|---|---|
| `watch.path` | 输入（投放）目录；不得与 `archiveRoot` / `quarantine` 互相包含（含子目录），否则保存报 `E502` |
| `settleSeconds` / `maxSettleWait` | 写稳检测采样间隔（秒）/ 最长等待（秒） |
| `amountSource` | `totalExclTax`（默认，合计不含税）或 `totalInclTax`（价税合计，需显式配置） |
| `categories[].priority` | 升序逐条匹配，**首条命中即停**，必须唯一 |
| `categories[].match` | 支持 `contains`（子串）、`regex`（正则）、`equals`（全等），作用于白名单字段 |
| `directoryTemplate` / `filenameTemplate` | 仅允许八字段 + `batchId` 占位符；纯字符串替换、禁脚本；非法字符替换为 `_`，空值回退「待人工确认」 |
| `export.encoding` | `UTF-8-BOM`（默认，兼容 Excel 中文）、`UTF-8`、`GBK` |
| `export.format` | `xlsx` 或 `csv` |
| `execution.previewRequired` | `true` 时计划未经确认禁止执行归档（`E702`） |
| `execution.ocrRetry` / `ocrTimeoutSec` | OCR 子进程超时（秒）与有限重试次数，超时转「待处理」 |

---

## 4. 运行方式

```bash
# 核心自检（验证版本、常量、配置、工具、建表；不需要 GUI）
python -m invoice_organizer --diag

# 启动桌面应用（需要 pywebview 与系统 WebView2）
python -m invoice_organizer
# 指定配置档案
python -m invoice_organizer --profile default
```

桌面应用启动后按五个视图操作：

1. **设置**：加载 / 保存配置（保存即版本 +1）；
2. **导入与监控**：启动监控，把发票文件投放到 `watch.path`；
3. **预览与确认**：加载计划，核对旧→新路径与命中规则，人工修正后确认；
4. **批次历史**：查看批次状态与统计；
5. **待复核与导出**：回填待确认字段、执行归档、导出台账。

前端为单页应用，**全部资源本地加载**（`ui/index.html` / `app.js` / `style.css`），无任何外网请求；
所有数据经 `JsBridge` 以统一响应 `{code, data, message}` 交互（code=0 成功）。

---

## 5. 测试方式

```bash
# 运行全部测试（30 项）
python -m pytest -q
# 或
pytest -q
```

测试覆盖：八字段提取与置信/证据、日期金额归一、五类去重、规则命中顺序、模板白名单与路径安全、
归档原子写/不覆盖/哈希复核/回滚、导出列序与编码、端到端冒烟（投放→计划→确认→归档→导出）。
OCR 相关降级路径通过 stub 覆盖，**不依赖本机 OCR 引擎**。

---

## 6. 目录结构

```text
invoice-organizer/
├── docs/
│   ├── ARCHITECTURE.md          # 系统设计（文件清单/类图/时序图/共享知识/任务分解）
│   └── PRD.md                   # 产品需求文档
├── invoice_organizer/
│   ├── __init__.py              # 包标识、版本、全局常量（八字段/错误码/置信/金额口径）
│   ├── __main__.py              # CLI 入口：--diag 自检 / 启动桌面窗口
│   ├── config/                  # 配置模型与校验、加载器、默认配置 JSON
│   ├── models/                  # 领域模型（发票/计划/批次/审计，全部 dataclass + Enum）
│   ├── parsers/                 # 解析适配器层（PDF/OFD/图片OCR/注册表，可插拔）
│   ├── core/                    # 核心领域逻辑（写稳/去重/字段/规则/模板/归档/导出/编排引擎）
│   ├── storage/                 # SQLite 存储层（11 张表 + 迁移框架）
│   ├── ui/                      # pywebview 桌面壳 + JsBridge + 本地前端（HTML/JS/CSS）
│   ├── utils/                   # 工具层（流式哈希/路径安全/日期/金额/日志）
│   └── tests/                   # pytest 用例与公共 fixture
├── requirements.txt             # 依赖声明
├── pytest.ini                   # pytest 配置
├── .gitignore                   # 忽略运行产物
└── README.md                    # 本文档
```

---

## 7. 依赖说明

| 依赖 | 用途 | 缺失时行为 |
|---|---|---|
| `pdfplumber` | PDF 文本层 + 字符坐标（证据定位） | PDF 解析返回空字段 + 告警，字段标「待人工确认」 |
| `Pillow` | 图片预处理（OCR 前置） | OCR 相关能力降级 |
| `openpyxl` | Excel 台账导出 | xlsx 自动降级为 CSV |
| `pywebview` | 桌面窗口 + JS↔Python bridge | 无法启动 GUI，`--diag` 与核心逻辑不受影响 |
| `watchdog` | 目录事件监控 | 自动切轮询兜底（PollingObserver 语义） |
| `pytesseract` | Tesseract OCR 适配器（需本机 tesseract 可执行文件） | 走 `TextLayerOcrAdapter` 降级，字段标「待人工确认」 |
| `pytest` | 测试 | 无法运行测试 |

标准库已覆盖 `sqlite3 / hashlib / multiprocessing / zipfile / xml.etree / pathlib / os.replace` 等核心能力。
Umi-OCR 适配器为占位接口，通过本机环回 HTTP 调用，无需额外 Python 依赖。

---

## 8. 常见问题

**Q1：`--diag` 报 pywebview / pdfplumber 未安装？**
A：`--diag` 不导入这些库。若你在启动桌面窗口时遇到缺失提示，请执行 `pip install -r requirements.txt`。

**Q2：启动桌面窗口失败？**
A：pywebview 依赖系统 WebView2（Windows 10/11 x64 一般已内置；若无，需安装 WebView2 Runtime）。
本软件为本地桌面应用，**不需要联网**，源码与前端均无外链资源。

**Q3：OCR 识别不出内容怎么办？**
A：未安装 OCR 引擎 / tesseract 语言包缺失 / 识别超时都会降级为「待人工确认」，绝不臆造字段。
可在「待复核与导出」视图人工回填。OCR 子进程超时会有限重试后转「待处理」，不会阻塞批次。

**Q4：保存配置报 `E502`（目录互相包含）？**
A：`watch.path` 不能是 `archiveRoot` / `quarantine` 的父目录或子目录，三者必须相互独立。

**Q5：归档会删除或修改原件吗？**
A：不会。原件恒定只读，软件只写归档副本；副本写前先写临时文件再原子重命名，不覆盖重名（追加 `_2` / `_3`），写后 SHA-256 复核；批次失败仅回滚本批新建副本，原件与日志保留。

**Q6：`E702` 计划未确认禁止执行？**
A：`execution.previewRequired=true` 时必须先在「预览与确认」视图点击确认，才能执行归档；这是防止误操作的执行闸门。

**Q7：为什么某些字段是「待人工确认」？**
A：看不清、缺失、校验失败（如税号字符集非法、日期无法归一）的字段一律标 `need_human` 并进入待复核清单；低置信（`low`，如 OCR 结果）同样进入待复核。这是「不臆造」原则的体现。

**Q8：pytest 报 `langsmith` / `pydantic` 插件错误？**
A：这是环境中其他包注册的第三方 pytest 插件。`pytest.ini` 已通过 `-p no:langsmith_plugin` 屏蔽，正常执行 `pytest -q` 不受影响。

---

> 边界声明：本软件默认离线运行，不上传任何数据；原件只读；关键操作有本地审计日志；
> 所有分类 / 去重 / 冲突结果均为整理辅助建议，最终判断权在人工。
