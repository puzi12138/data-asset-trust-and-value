# EXP-05：Ionosphere任务条件价值复现

状态：待维护者完成公开许可、实际执行与认领流程检查。请在对应Issue留言计划运行的种子和环境；维护者协调后再运行。重复执行同一数据来源与配置是复现，不是新增独立数据集。

## 研究问题与数据

在固定D0、候选池、目标模型和新增61条数据预算下，不同数据选择方法对Ionosphere任务的损失及实际训练成本有什么影响？数据来源：[UCI Ionosphere](https://archive.ics.uci.edu/dataset/52/ionosphere)；数据许可记录为[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)，下载来源与逐文件哈希以`experiments/community/data/README.md`及当前配置为准。访问原始数据前应检查许可和归属要求。

- 官方下载：[UCI原始压缩包](https://archive.ics.uci.edu/static/public/52/ionosphere.zip)。下载脚本检查配置中的固定哈希。
- 任务类型：分类；模型：`LogisticRegression(C=1, solver=lbfgs, max_iter=500, tol=1e-5)`。
- 主要评价：LogLoss；同时报告Accuracy、Macro-F1。标准执行器未计算AUROC，记为NOT_EVALUATED。收益定义为同一评价集上的`D0损失 - 选择臂损失`。
- 协议配置：`experiments/community/configs/EXP-05/1103.json`、`2207.json`、`3301.json`；确认配置和输入摘要后运行。
- 随机种子：1103、2207、3301；每次只运行一个种子，不删去失败或不利结果。
- 目标训练臂：每个种子11个，包含D0；另拟合1个来源符合性RF。

分组隔离下的冻结成员数量如下（评价成员来自公开数据）：

| 种子 | D0 | 候选池 | 开发集 | 评价集 |
| ---: | ---: | ---: | ---: | ---: |
| 1103 | 105 | 123 | 61 | 62 |
| 2207 | 106 | 122 | 61 | 62 |
| 3301 | 106 | 122 | 61 | 62 |

## 需要完整执行的对照

D0、随机、随机+组覆盖、证据、证据+组覆盖、RF来源符合性、RF+组覆盖、证据+RF+组覆盖、完整率、距离、固定质量分；分类不运行回归专用的Data Shapley。每个选择臂都使用同一新增预算；来源比对证据不等于自然标签正确性。保存完整选中成员、各臂指标、逐条评价和失败，不能只上传最好结果。

## 资源与依赖

Python 3.10和`requirements-experiments.txt`中的固定版本；CPU执行，最多2个数值计算线程，`--max-workers 1`，不需要GPU，也不会下载大型模型。正式运行启动前系统可用内存至少1.5 GiB、运行中至少0.75 GiB、进程树RSS上限1.5 GiB，单个种子最多300秒。建议预留至少4 GiB可用内存；这些是门槛和保守建议，不是本任务实测的耗时或峰值。请根据手册检查当前平台资源监督支持状态，遇到超时或资源不足即停止并保留状态。

## 准备、检查并运行

以下以种子1103为例。每次先准备并核验数据，再使用预检；将其他种子按原配置分别运行。数据和结果目录应位于仓库外。

```sh
python -m pip install -r requirements-experiments.txt
python scripts/experiments/check_env.py
python scripts/experiments/prepare_data.py --task-id EXP-05 --seed 1103 --output ../community-data
python scripts/experiments/run_task.py --task-id EXP-05 --seed 1103 --data-dir ../community-data --output ../community-results --max-workers 1 --timeout 300 --device cpu --dry-run
python scripts/experiments/run_task.py --task-id EXP-05 --seed 1103 --data-dir ../community-data --output ../community-results --max-workers 1 --timeout 300 --device cpu
python scripts/experiments/verify_result.py --result path/to/run-directory --data-root ../community-data --output ../verification-report.json
```

将`path/to/run-directory`替换为`../community-results/运行时输出的run_id`；验证输出使用新路径。`--dry-run`不训练。不同平台支持和安装详情见`docs/EXPERIMENT_RUNBOOK.md`。

## 输出与验收

应保留`RUN_MANIFEST.json`、`METRICS.json`、`PREDICTIONS.csv`、`COST.json`、`ENVIRONMENT.json`、`VERIFY_REPORT.json`、`README.md`及逐臂模型、成员、状态和错误日志。自动检查任务、种子、配置和输入摘要、训练臂、分组隔离、有限数值以及可从逐条预测重算的指标。缺可信输入时返回NEEDS_REVIEW；数值复算通过是REPRODUCED，不等于科研验收，维护者需独立复核。请按`docs/RESULT_SUBMISSION.md`报告环境、是否更改协议、结果链接、SHA-256、保存期限和失败信息；不要公开凭据、私有路径、未经授权的数据或个人信息。

公开来源和评价标签可以被重复查看，本任务是公开复现，不是隐藏最终测试。后续研究若改变模型、划分或指标，应使用新协议，不覆盖此配置。
