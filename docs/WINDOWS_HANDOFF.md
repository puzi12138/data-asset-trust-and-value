# 在另一台 Windows 电脑接续工程

用 Windows 10/11 x64、Git、PowerShell 和 64 位 Python 3.10。在仓库首页选 **Code → HTTPS** 复制克隆地址。从准备放置工程的目录打开 PowerShell，执行下列命令。安装依赖需要联网。核心检查在本地运行，无需训练数据或模型。

```powershell
$RepoUrl = Read-Host '粘贴仓库的 HTTPS 克隆地址'
git clone $RepoUrl data-asset-engineering
if ($LASTEXITCODE -ne 0) { throw '克隆失败' }
Set-Location -LiteralPath data-asset-engineering
git rev-parse HEAD
py -3.10 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Python 3.10 环境创建失败' }
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }
& .\.venv\Scripts\python.exe -B scripts/check_engineering.py --suite core
if ($LASTEXITCODE -ne 0) { throw '核心工程检查失败' }
```

最后一行 JSON 中应有 `"status": "PASS"` 和 `"training": "NOT_RUN"`。保留刚才打印的提交号，后续报告即可对应到具体代码版本。主分支的社区实验另有[逐步运行手册](EXPERIMENT_RUNBOOK.md)和[结果提交说明](RESULT_SUBMISSION.md)。

## 接续外部实验

在仓库的 **Pull requests** 搜索 #18 可看到外部实验运行工具。当前交接快照的代码提交是 `77ca07ebd140d44ec802f737304e113e152ef0c8`。要在保留主分支的同时阅读或测试这版，从刚才的仓库根目录运行以下命令。工作树建在同级新目录。

```powershell
git fetch origin pull/18/head
if ($LASTEXITCODE -ne 0) { throw '未取到 PR #18' }
git worktree add --detach ../data-asset-runner 77ca07ebd140d44ec802f737304e113e152ef0c8
if ($LASTEXITCODE -ne 0) { throw '工具版本未就位，检查提交号和目录' }
Set-Location -LiteralPath ../data-asset-runner
git rev-parse HEAD
Get-Content -Encoding utf8 docs/EXTERNAL_EXPERIMENT_RUNBOOK.md
```

下表按现有 Issue 分工。正式运行使用该 Issue 正文指定的提交、种子和输入。建议先看任务末条维护说明，避免重复认领。

| Issue | 可从哪一步接手 | 提交什么 |
|---|---|---|
| #14 Covertype | 对照原始字段编写54个特征、7个类别的数据读取；先测同组隔离和输入维度 | 适配代码、测试、成员与输入清单，通过任务预检后再提交训练结果 |
| #15 Letter Recognition | 种子1103已完成。接续原结果父目录与累计预算账本，依次做2207和3301 | 每个种子各自的冻结输入、监督运行记录、全部预测、独立核算结果和累计账本 |
| #16 Superconductivity | 先编写真实材料数据适配，按材料组划分；检查同一材料组跨集合的条数为0 | 适配器、测试和分组清单，再按回归手册执行 |
| #17 Bike Sharing | 先把同一天的记录放入同一集合，按 Issue 的时序与缓冲规定划分，剔除由目标派生的列 | 按天成员清单、列排除与缓冲测试，通过后再运行小时需求回归 |

如果接手的是 #15 而新机只有 Git 克隆，先按 Issue 联系原运行者取得已用预算和运行收据。实际训练前核对提交、输入及累计账本，随后运行预检。各任务的输出放在仓库之外，提交前按[贡献说明](../CONTRIBUTING.md)整理。当前维护的代码许可证安排见[公开材料范围](PUBLICATION_BOUNDARY.md)。
