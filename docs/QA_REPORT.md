# 独立 QA 验证报告 — invoice_organizer v1.0.0

| 项目 | 内容 |
|---|---|
| 执行人 | 严过关（Yan）· QA 工程师（独立验证，不复述工程师自审结论） |
| 项目路径 | `H:/fapiao/invoice-organizer`（main 分支） |
| 报告阶段 | 第 1 轮已完成；第 2 轮回归待工程师修复后执行 |
| 判定依据 | `docs/PRD.md` v1.0、`docs/ARCHITECTURE.md` v1.0、`README.md` |

---

## 1. 运行环境

| 项 | 值 |
|---|---|
| 操作系统 | Windows（Git Bash 执行 shell 命令） |
| Python | 3.13.14（由 `--diag` 输出确认） |
| pytest | 已安装；`pytest.ini` 含 `-p no:langsmith_plugin` |
| 外部依赖 | OCR / pywebview / pdfplumber 等均非必需，测试使用桩与降级路径 |
| 命令执行方式 | `cd H:/fapiao/invoice-organizer && <command>` |

---

## 2. 三项验证原始输出（第 1 轮，修改前基线）

### 2.1 `python -m compileall -q invoice_organizer`

```text
Stdout: EXIT_CODE=0

Stderr: (empty)
Exit Code: 0
Signal: (none)
```

结论：编译通过，无语法错误。

### 2.2 `python -m pytest -q`（工程师交付的原始 30 条用例）

```text
..............................                                           [100%]
30 passed in 0.72s
EXIT_CODE=0

Stderr: (empty)
Exit Code: 0
```

结论：原始用例全绿，但**通过率不等于覆盖有效**（见第 3 节）。

### 2.3 `python -m invoice_organizer --diag`

```text
invoice_organizer v1.0.0 自检（schemaVersion=1）
Python: 3.13.14
- 共享常量 -
  [OK] 八字段键顺序
  [OK] 错误码数量
- 配置 -
  [OK] 默认配置校验
  [OK] 默认配置序列化往返
  [OK] 配置加载器可用
- 工具 -
  [OK] SHA-256 字节
  [OK] 日期归一
  [OK] 元转分
  [OK] 路径守卫
- 存储 -
  [OK] SQLite 建表

自检全部通过。
EXIT_CODE=0
```

结论：自检为静态/浅层检查，未覆盖数据库**读回**路径、错误码语义与权限闸门。

---

## 3. 原有测试用例有效性审阅（关键发现）

| 原用例 | 有效性问题 |
|---|---|
| `test_archiver::test_path_violation_rejected` | **假通过**：构造了 `newPath="../../etc/evil.pdf"` 的越界项，但执行时只传入安全项 `item2`，越界项从未被 `execute()` 处理，未断言 E501，也未断言越界文件未落盘。 |
| `test_archiver::test_rollback_removes_copies_only` | 只手工调用 `Archiver.rollback()`，未验证批次失败时引擎会自动回滚，也未验证回滚范围只含本批副本。 |
| `test_archiver` 全组 | 无任何哈希复核失败（E603）、原子写临时文件位置、原件字节/mtime 未变的断言。 |
| `test_engine_smoke::test_full_flow_with_mocked_parser` | 只断言导出文件存在，**未断言导出内容**；因此「台账空行」缺陷被漏过。 |
| `test_rules_template::test_rule_priority_first_hit` | 两条规则按 priority 升序给出，未验证乱序配置也会排序。 |
| `test_rules_template::test_config_validation_directory_overlap` | 只覆盖「归档在输入目录内」一个方向，未覆盖反向、相等、隔离区互相包含。 |
| `test_rules_template::test_template_path_guard_reject` | 直接测 `PathGuard`，未验证模板渲染结果经清洗后仍在归档根内，也未测脚本/表达式注入。 |
| `test_fields::test_tax_id_validation` | 只验证校验原语返回值，未验证 OCR 来源税号的最终置信状态。 |
| `test_exporter::test_xlsx_columns` | openpyxl 缺失时直接 `return`，属于条件跳过，不构成稳定断言。 |
| 全组 | 无 JsBridge 权限闸门测试、无数据库往返测试、无符号链接测试、无 OCR 超时/有限重试测试、无配置版本绑定测试。架构文档列出的 `test_paths_hash.py` 文件实际不存在。 |

---

## 4. 覆盖矩阵（风险点 → 用例 → 是否真实断言）

| # | 高风险点 | 原有用例 | QA 补充用例 | 真实断言 | 结论 |
|---|---|---|---|---|---|
| 1 | PathGuard `../` 注入 | 无（假通过） | `test_safe_join_rejects_traversal_and_continues`（参数化 5 类） | 是：断言抛 `PathViolation` 且 `error_code=E501` | 通过 |
| 2 | 绝对路径 / 盘符 / UNC 注入 | 1 条（直接测 PathGuard） | 同上参数化 `/etc/passwd`、`C:/`、`\\server\share` | 是 | 通过 |
| 3 | 软链接越界 | 无 | `test_resolve_inside_rejects_symlink_escape`、`test_archiver_rejects_symlinked_parent` | 是：断言拒绝且外部目录无文件落盘（可建链接环境） | 行为通过；错误码断言失败（缺陷 B） |
| 4 | 落盘前父目录复查 | 无 | 通过 `resolve_inside()` 在 `mkdir` 前复核；补充父目录软链接用例 | 部分：覆盖「写前已存在链接」，未覆盖「校验后到写入前的 TOCTOU 竞态」 | 遗留观察，见第 8 节 |
| 5 | 归档不覆盖重名 | `test_no_overwrite_duplicate_name` | `test_next_available_never_overwrites` | 是：断言 `_2`/`_3` 且原字节不变 | 通过 |
| 6 | 原子写（临时文件同目录 + os.replace） | 仅间接 | `test_write_atomic_uses_same_dir_temp_then_replace` | 是：spy `os.replace`，断言 replace 前临时文件存在且同目录、无残留 | 通过（修正测试自身调用方式后） |
| 7 | 写后 SHA-256 复核 | 仅成功路径 | `test_verify_detects_tampered_copy`、`test_hash_mismatch_sets_e603` | 是：篡改检测 + E603 + `verified=False` | 行为通过；错误码断言失败（缺陷 B） |
| 8 | 失败仅回滚本批副本 | 手工调用 rollback | `test_engine_failure_rolls_back_copies_only` | 是：批次失败后副本清空、原件字节保留 | 行为通过；anomaly 缺 E603（缺陷 B） |
| 9 | 原件只读永不删除 | 仅断言存在 | `test_archive_keeps_source_bytes_and_mtime`、`test_engine_failure_rolls_back_copies_only` | 是：字节 + `mtime_ns` 均未变 | 通过 |
| 10 | 同号同哈希 / 同号异哈希冲突元数据 | 断言 kind/accept | `test_dedupe_conflict_metadata_is_actionable` | 是：断言 conflict.type 与新哈希写入 detail | 通过 |
| 11 | 异号同哈希 | 有 | `test_one_pdf_two_invoices_produces_two_plan_items` 间接覆盖 | 是：两条记录、同源哈希 | 失败（缺陷 A，DB 读回丢失票号） |
| 12 | 一 PDF 多票拆分 | 仅测 `split_multi_invoice()` 单元 | 同上，端到端断言 | 是：2 条 plan item + 2 个票号 + 同源 SourceRef | 失败（缺陷 A） |
| 13 | 八字段键顺序 | 有 | `test_multiple_item_lines_merged_into_one_record` | 是：顺序 + `int` 类型 + 金额值 | 通过 |
| 14 | 置信 high/low/need_human | 部分 | `test_ocr_valid_tax_id_is_low_confidence`、`test_invalid_date_and_amount_are_need_human` | 是 | OCR 税号失败（缺陷 C）；非法日期/金额通过 |
| 15 | 金额整数分 / 默认不含税 | 有 | `test_multiple_item_lines_merged_into_one_record` | 是：`isinstance(int)` + 123456 分 | 通过 |
| 16 | 多行项目合并 | 无 | `test_multiple_item_lines_merged_into_one_record` | 是：2 条 ItemLine 归入 1 张票 | 通过 |
| 17 | 规则 priority 首条命中 | 有（规则已有序） | `test_rules_sorted_by_priority_even_if_config_unsorted` | 是：乱序输入仍命中高优先级 | 通过 |
| 18 | 未命中归未分类 | 有 | — | 是 | 通过 |
| 19 | 模板白名单纯替换 / 禁脚本 | 部分 | `test_template_rendering_is_pure_text_and_sanitized` | 是：无 `<script>`、`__import__`、`evil`，有告警 | 通过 |
| 20 | 模板非法字符清洗 / `..` 注入 | 部分 | `test_template_output_stays_inside_archive_root` | 是：`PathGuard.target_for()` 仍在根内 | 通过 |
| 21 | 配置目录互相包含 | 1 个方向 | `test_config_rejects_all_directory_overlaps`（4 种组合） | 是：`E502` | 通过 |
| 22 | 配置版本绑定 / 快照 | 无 | `test_loader_keeps_history_and_snapshot`、`test_plan_version_matches_batch_snapshot` | 是：v1/v2/v3 留存、快照含版本、计划版本不漂移 | 计划版本失败（缺陷 D）；快照读回失败（缺陷 A） |
| 23 | OPERATOR 拒绝配置变更 / ADMIN 通过 | 无 | `test_bridge_config_change_requires_admin` | 是：701/0 + 审计事件 | 通过 |
| 24 | READER 只读闸门 | 无 | `test_bridge_reader_denied_all_mutating_operations` | 是：4 个变更接口均 701，读配置 0 | 通过 |
| 25 | OCR 超时终止 + 有限重试 + 降级 | 无 | `test_ocr_timeout_retries_finite_then_degrades` | 是：2 次 E401 + E402 且无异常抛出 | warning 缺码（缺陷 B） |
| 26 | 前端无外链 | 声称有 | `test_frontend_has_no_external_resources` | 是：扫描 html/js/css 的 http(s) 与协议相对 URL | 通过 |
| 27 | 数据库落库→读回一致性 | 无 | `test_database_roundtrip_preserves_invoice_values`、`test_database_roundtrip_preserves_batch_snapshot` | 是 | **失败（缺陷 A，P0）** |
| 28 | 台账内容含票号/金额（端到端） | 仅断言文件存在 | `test_exported_ledger_contains_extracted_values` | 是：读 CSV 校验行值 | **失败（缺陷 A，P0）** |

---

## 5. 补充用例说明

新增文件：`invoice_organizer/tests/test_qa_hardening.py`（**36 条**，含参数化展开），按主题分组：

| 分组 | 条数 | 目的 |
|---|---|---|
| 路径安全 | 9 | `../`、绝对路径、盘符、UNC、软链接、重名序号、越界项失败后继续处理 |
| 归档事务 | 7 | 哈希篡改检测、E603、原子写 spy、原件字节/mtime、失败自动回滚 |
| 去重与拆分 | 2 | 冲突元数据可追溯、一 PDF 多票端到端 |
| 存储往返与导出 | 3 | 发票/批次读回一致性、台账实际内容 |
| 字段提取 | 3 | OCR 税号置信、非法日期金额留空、多行项目合并 |
| 规则与模板 | 3 | 乱序规则排序、模板注入纯替换、渲染结果路径复核 |
| 配置与版本 | 5 | 四类目录重叠、E701 校验、版本历史、计划版本绑定 |
| 权限闸门 | 2 | OPERATOR/ADMIN 配置变更、READER 只读 |
| OCR 与离线 | 2 | 超时有限重试降级、前端无外链 |

编写原则：Arrange-Act-Assert、中文 docstring、参数化覆盖多输入、无无意义凑数用例；不依赖 GUI/OCR 真实引擎。

---

## 6. 第 1 轮测试结果与路由判定

```text
30（原有） + 36（新增） = 66 条收集
55 passed, 11 failed in 1.16s
```

测试代码自身 2 处问题已由 QA 自行修复（路由 QA）：
1. `_write_atomic` 误按静态方法调用 → 改为实例调用；
2. 权限用例未切回 ADMIN 角色 → 增加 `bridge.set_role("admin")`。

其余 11 条失败断言均符合 PRD / ARCHITECTURE，判定为**源码缺陷 → 路由 Engineer**，已附文件行号与失败用例名发送给 software-engineer，并同步 team-lead。

### 缺陷清单

| ID | 严重度 | 描述 | 关键位置 | 失败用例数 |
|---|---|---|---|---|
| A | **P0** | SQLite 行为 snake_case，模型 `from_dict()` 只读 camelCase；票号/日期/金额/字段槽位/批次快照/计划关联读回后丢失，导出台账为空行 | `storage/db.py:153,178,186,219,232,292,305`；`models/invoice.py:224-246`；`models/batch.py:112-121`；`models/plan.py:141-151` | 4 |
| B | 中 | `Archiver.errorCode` 与 OCR warning 写入中文描述而非 `E501/E603/E401`，BatchReport.anomalies 丢失错误码 | `core/archiver.py:98,101,111,124,131,138,142`；`parsers/image_ocr.py:188,195,205` | 5 |
| C | 中 | OCR 来源的合法税号被标 `high`，违反「OCR/模糊 = low」置信约定 | `core/fields.py:169-189` | 1 |
| D | 中 | `build_plan()` 使用运行中配置版本而非批次启动时快照版本，历史批次可能漂移 | `core/engine.py:229` | 1 |

---

## 7. 第 2 轮回归

> 待 software-engineer 修复上述缺陷后填写。按 2 轮上限执行：若第 2 轮仍有失败，立即停止并转入第 8 节遗留问题，不进入第 3 轮。

---

## 8. 遗留问题 / 观察项

| 项 | 说明 | 状态 |
|---|---|---|
| 落盘前父目录 TOCTOU | `Archiver._execute_one()` 在 `mkdir()` 之前完成 `resolve_inside()`，但未在 `os.replace()` 前再次复核真实路径；理论上校验后至写入前若目录被替换为软链接可越界。当前平台无法稳定构造该竞态，未写不稳定用例。建议写入前对 `dest.parent.resolve()` 做二次 `resolve_inside()` 复核。 | 观察，非失败用例 |
| `Archiver.execute()` 自身不回滚 | 回滚由 `OrganizeEngine.execute_batch()` 负责；直接调用 `Archiver.execute()` 的调用方需自行 `rollback()`。系统级行为正确，接口文档 `Archiver.execute` 的「已回滚」描述略有误导。 | 观察 |
| `test_xlsx_columns` 条件跳过 | openpyxl 缺失时静默 return，建议改为 `pytest.importorskip("openpyxl")`。 | 低优先级 |

---

## 9. 最终路由判定

- 第 1 轮：**Engineer**（4 类源码缺陷，11 条失败；测试代码问题已自行修复）
- 第 2 轮：待回归后更新

> 说明：`compileall`、`--diag` 与原始 30 条 pytest 均通过，不能据此判定 NoOne；独立补充断言后暴露出 P0 级数据丢失缺陷。
