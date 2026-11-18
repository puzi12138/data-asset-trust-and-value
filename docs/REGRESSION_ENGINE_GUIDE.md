# 共享回归执行器使用说明

本说明对应候选提交 `f55bd59c8dc840df0cfc669f1e9a3505a2dbb1b7`，代码地址 `https://github.com/puzi12138/data-asset-trust-and-value/tree/f55bd59c8dc840df0cfc669f1e9a3505a2dbb1b7`，当前工程验证状态 `合成数据的受监督工程验收通过；真实数据适配与训练尚未完成`。此候选为 Windows 10/11 x64、Python 3.10 x64、CPU 单 worker 环境设计。运行资源检查通过前不会开始拟合。

共享执行器提供表格回归模型和受监督运行：Ridge(alpha=1)与100树、最大深度12的随机森林回归；在两种新增样本预算128/512下，每个模型比较4种选择方法和只用D0的基线，合计18个目标模型臂，另拟合一个来源版本符合性检测器。分类 Letter 的现有入口继续保留。结果包括成员清单、逐条预测、模型JSON、MAE/RMSE/R²、时间/资源、选择冻结、监督与数值核对记录。

当前**没有现成的真实超导或 Bike 数据适配器**。请分别完成 [#16](https://github.com/puzi12138/data-asset-trust-and-value/issues/16) 或 [#17](https://github.com/puzi12138/data-asset-trust-and-value/issues/17) 中明确列出的适配文件、字段测试及来源记录，才可准备真实输入。不要把本说明里的合成输入当作真实数据。

## 输入接口

| 配置项 | 超导材料 EXT-03 | Bike EXT-04 |
|---|---|---|
| `dataset` | `Superconductivity` | `Bike Sharing` |
| `task_type` / `feature_count` | `regression` / `81` | `regression` / `12` |
| `mutation_feature_index` | `0` | `8`，即 `temp` |
| 目标模型 | `['ridge','random_forest']` | 同左 |
| `budgets` | `[128,512]` | 同左 |

配置`schema`为`external-experiment-v1`，`seed`从1103、2207、3301中选，`inputs`逐项记录相对文件名与64位SHA-256。必须绑定`selection_input.json`、`audit_reference.json`、`development_input.json`、`test_input.json`、`injection_truth.json`、`MEMBERS.json`、`source_record.json`。前5个文件名还要分别写入config相应键。输入的角色、参考、成员和拆分规范见两个Issue；选择输入只含D0/pool，dev/test/受控修改真值分别保存，选择冻结后才读取后者。

`selection_input.json`包含`rows`（每行 `id,source_row,group,x,y`）、`roles`（D0/pool为rows中的索引）、`task`（类型、维数、按输入顺序的`feature_names`）和`asset_metadata.source_sha256`。`MEMBERS.json`顶层记录`source_sha256`和`seed`；`roles`中各角色均为逐行列表，每条记录**恰有**`source_row,id,group`三个键，不加其他键，并与实际输入逐项匹配。Bike的`split_group`、`record_identity`放在选择/开发/测试输入行里，不放入`roles`成员记录；其他元信息可放在成员表顶层。

Bike使用 `group=split_group=YYYY-MM-DD`，`record_identity=YYYY-MM-DD:HH`；训练、候选、开发、测试及缓冲日不交叉，同一日不同小时可按组轮次被选择。`task.feature_names`仅包含`season,yr,mnth,hr,holiday,weekday,workingday,weathersit,temp,atemp,hum,windspeed`，严格按此顺序；`casual`与`registered`不得作为输入。成员表必须包含6个缓冲日，具体日期划分见#17。

超导来源清单同时保存`train.csv`的`raw_sha256`和`unique_m.csv`的`materials_sha256`。`alignment_status='SOURCE_SUPPORTED'`仅表示适配器声明已有配对依据，**字段本身不是证据**：提交者还需提供官方资料中的对应说明或可核查的逐行身份关联。只有目标值相等不足以证明每行对应；证据不足时停止，不生成支持状态。真实数据的来源许可和标签是否正确均需独立判断，哈希不会自动证明。

## Windows 环境与无训练检查

在用户目录的新路径克隆公开候选分支、固定提交并创建独立环境；若目录已经存在就保留，避免覆盖。

```powershell
$ErrorActionPreference = 'Stop'
$DATV_Repo = Join-Path $env:USERPROFILE 'datv-regression-candidate'
if (Test-Path -LiteralPath $DATV_Repo) { throw '目录已存在，保留并检查。' }
git clone --branch feature/external-experiment-runner --single-branch https://github.com/puzi12138/data-asset-trust-and-value.git $DATV_Repo
if ($LASTEXITCODE -ne 0) { throw '克隆失败。' }
Set-Location -LiteralPath $DATV_Repo
git switch --detach f55bd59c8dc840df0cfc669f1e9a3505a2dbb1b7
if ($LASTEXITCODE -ne 0) { throw '固定提交切换失败。' }
py -3.10 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw '环境创建失败。' }
$DATV_Python = Join-Path $DATV_Repo '.venv\Scripts\python.exe'
& $DATV_Python -m pip install --disable-pip-version-check -r requirements-external.txt
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败。' }
& $DATV_Python -B -m unittest -v tests.test_external_data tests.test_external_runner tests.test_external_worker tests.test_external_verify tests.test_external_regression
if ($LASTEXITCODE -ne 0) { throw '软件测试失败；保留输出。' }
```

上述命令仅准备环境并运行不拟合模型的软件测试，不下载实际数据。不要用现有Letter数据准备脚本冒充其他数据源的适配器。

## 合成端到端验证

以下输入由程序自动生成，只有在需要检验自己的运行环境时使用；它不作为真实实验结果。先生成全新目录和冻结文件，预检通过后通过原监督器执行：

```powershell
$DATV_Data = Join-Path $env:USERPROFILE 'datv-regression-synthetic-input'
$DATV_Freeze = Join-Path $env:USERPROFILE 'datv-regression-synthetic-freeze.json'
$DATV_Out = Join-Path $env:USERPROFILE 'datv-regression-synthetic-runs'
if ((Test-Path -LiteralPath $DATV_Data) -or (Test-Path -LiteralPath $DATV_Freeze) -or (Test-Path -LiteralPath $DATV_Out)) { throw '目标已存在；请保留并检查。' }
& $DATV_Python -B scripts/external_experiments/prepare_regression_smoke.py --out $DATV_Data --seed 1103
if ($LASTEXITCODE -ne 0) { throw '合成输入准备失败。' }
$DATV_Config = Join-Path $DATV_Data 'config.json'
& $DATV_Python -B scripts/external_experiments/run.py --config $DATV_Config --data-dir $DATV_Data --freeze $DATV_Freeze --freeze-only
if ($LASTEXITCODE -ne 0) { throw '冻结失败。' }
& $DATV_Python -B scripts/external_experiments/run.py --config $DATV_Config --data-dir $DATV_Data --freeze $DATV_Freeze --dry-run
if ($LASTEXITCODE -ne 0) { throw '预检未通过，不启动训练。' }
& $DATV_Python -B scripts/external_experiments/run.py --config $DATV_Config --data-dir $DATV_Data --freeze $DATV_Freeze --output $DATV_Out
if ($LASTEXITCODE -ne 0) { throw '运行中止或数值检查未通过；保留结果。' }
```

资源门槛：启动可用内存≥1.5GiB、运行中≥0.75GiB、进程树RSS≤1.5GiB、单worker≤300秒、任务累计≤1800秒；禁止通过更换输出目录绕开累计账本。测试或训练失败时保存日志及未完成条件，不因获得好结果而改参数或种子。

## 正式数据结果

待合作者实现并测试适配器后，使用各自Issue给出的正式运行命令。只在成员、参数、代码和输入哈希冻结后开展训练；保存所有实验臂及失败，不只上报表现最好的一项。所有seed属于同一来源的重复，不是新的独立数据集。模型输出数值复核不等于其训练过程经过身份认证，也不证明数据天然真实或训练价值预测已完成。
