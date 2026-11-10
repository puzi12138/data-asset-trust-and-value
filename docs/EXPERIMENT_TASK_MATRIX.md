# 第一批可认领的实验任务

状态：目前只开放Windows 10/11 x64。请先在对应Issue按认领模板留言并等待维护者协调。每个任务使用三个种子1103、2207、3301；各自独立记录运行，串行执行。以下是固定候选数量，不是观察到的收益。

| ID | 数据集 | 任务/模型 | 每次新增样本k | 主要指标 | 每种子目标训练臂 |
| --- | --- | --- | ---: | --- | ---: |
| [EXP-01](../experiments/community/tasks/EXP-01.md) | Spambase | 分类 / Logistic Regression | 64 | LogLoss | 11 |
| [EXP-02](../experiments/community/tasks/EXP-02.md) | Concrete Compressive Strength | 回归 / Ridge | 64 | MAE | 12 |
| [EXP-03](../experiments/community/tasks/EXP-03.md) | Wine Classification | 分类 / Logistic Regression | 31 | LogLoss | 11 |
| [EXP-04](../experiments/community/tasks/EXP-04.md) | Auto MPG | 回归 / Ridge | 64 | MAE | 12 |
| [EXP-05](../experiments/community/tasks/EXP-05.md) | Ionosphere | 分类 / Logistic Regression | 61 | LogLoss | 11 |
| [EXP-06](../experiments/community/tasks/EXP-06.md) | Airfoil Self-Noise | 回归 / Ridge | 64 | MAE | 12 |

除表中目标模型臂外，每种子另拟合一个D0扰动监督的来源符合性RF。共同选择方法为随机、随机+组覆盖、证据、证据+组覆盖、RF、RF+组覆盖、证据+RF+组覆盖、完整率、距离、固定质量分；另有D0基准。回归另包括使用独立开发集标签的条件MC Data Shapley，固定8个排列。所有方法的新增样本预算相同；方法信息及计算成本并不完全相同。

每任务默认CPU、最多2个计算线程，启动可用内存至少1.5 GiB、进程树RSS上限1.5 GiB、单次最多300秒，GPU不需要。准确运行成本尚须实测，超时和失败应保留。每个任务的三个seed共用一个结果父目录与本机1800秒账本；不通过更换目录规避预算。结果ZIP优先提交Issue，公开UCI评价集是可访问的复现基准，不是隐藏最终测试。

可由维护者按仓库需要建立`experiment`、`help-wanted`、`cpu`、`gpu`、`high-memory`、`reproduction`、`claimed`、`completed`和`needs-review`标签。贡献者先在对应Issue说明任务、种子和设备，维护者再协调指派；这份文档不会自动在GitHub创建标签、Issue或指派。重复运行可按`reproduction`区分。

后续可以研究更多独立来源、更高排列数Shapley、多模型、公开合成指令和更大模型；这些只列为待研究项目，不表示当前已实现或被授权执行。

Windows环境准备、可复制PowerShell步骤、失败停止、run_id记录、完整ZIP与SHA-256见每项任务正文及 docs/EXPERIMENT_RUNBOOK.md。主分支尚未包含可信校验器时先走Issue附件；结果PR须由维护者确认后，以当前协作分支为基线提交。公开评价标签不是隐藏最终测试。
