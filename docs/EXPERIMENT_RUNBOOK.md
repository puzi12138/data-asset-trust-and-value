# 贡献者实验运行手册

## 范围

本批任务考察固定来源数据在指定模型、任务和新增数据预算下的选择与训练效用，保留全部规定基线和不利结果。数据集自身可信度、可观测质量、数据选择、实际训练效用是不同输出。分类和回归指标不能不加说明地合并。

任务使用公开的UCI数据和公开评价成员，其目的是可重复运行，不能作为未接触的隐藏最终测试。后续需要真正盲测时，应由维护者建立独立版本和不向选择过程公开的最终评价方式。

## 环境与资源

- Python 3.10及本仓库的`requirements-experiments.txt`；依赖版本按清单安装。下载的数据和运行输出放在仓库外。数据来源、大小上限及许可见`experiments/community/data/README.md`。
- CPU运行，GPU不需要，不下载大型模型。一次一个工作进程，最多2个数值计算线程；`--max-workers 1`，单次上限300秒。三个种子逐次运行。
- 固定任务启动时系统可用内存至少1.5 GiB，运行时系统可用内存下限0.75 GiB、工作进程树RSS上限1.5 GiB；超限、超时或输入不一致应停止并保留结果状态。建议开始前预留至少4 GiB可用内存，但这只是保守建议，不是实测资源消耗。运行时间取决于设备和数据；启动门槛不等于预期峰值。
- `--max-rss-gib`和`--min-free-gib`可将内存保护调得更保守，不能放宽冻结门槛。单个输出父目录维护累计1800秒的本机预算账本；它不跨不同目录或贡献者机器共享，也不构成全局自动预算。维护者需要汇总跨机器成本，勿通过换目录规避预算。
- 系统兼容性以实现和实际测试收据为准。Windows的Job Object不等同Linux的进程组或资源限制。未实测的平台不能声称通过同等资源监督；如果平台不能提供规定保护，不要绕过安全检查启动正式任务。
- 输出中的CPU、内存峰值和耗时按各机器独立记录；未知GPU能耗、价格或不可观测的峰值保持未知。不同硬件时间不能直接当成同条件成本比较。

可用`--max-rss-gib`调低进程内存上限，用`--min-free-gib`调高必须保留的系统可用内存；这些参数只能收紧默认保护，不能提高冻结任务资源上限。Linux正式执行需要显式`--allow-unverified-platform`，其监督器仍是未实测的实验性实现；macOS当前拒绝正式训练。`.gitattributes`固定Python源码换行，并保留配置、软件夹具和提交结果的精确字节，避免平台自动转换破坏哈希。

创建隔离环境并安装依赖后，检查环境：

```sh
python -m venv .venv
# 激活 .venv；安装会从包源获取所列依赖，请先检查本机网络及许可。
python -m pip install -r requirements-experiments.txt
python scripts/experiments/check_env.py
```

## 运行一个任务、一个种子

下面以EXP-01、种子1103为例。只使用维护者确认可发布、与结果绑定的配置版本。其他任务和种子仅替换ID与种子；建议把数据和结果目录放在仓库外。

```sh
python scripts/experiments/prepare_data.py --task-id EXP-01 --seed 1103 --output ../community-data
python scripts/experiments/run_task.py --task-id EXP-01 --seed 1103 --data-dir ../community-data --output ../community-results --max-workers 1 --timeout 300 --device cpu --dry-run
python scripts/experiments/run_task.py --task-id EXP-01 --seed 1103 --data-dir ../community-data --output ../community-results --max-workers 1 --timeout 300 --device cpu
```

数据准备默认只取得指定任务所需的小型官方数据；缓存参数及下载大小限制见数据说明。`--dry-run`检查配置、输入与资源条件，不训练、不生成真实训练效用标签。正式运行前，检查任务ID、种子、配置哈希、数据哈希和Git提交均与准备执行的协议一致。不要擅自改动冻结配置或调整评价集。

运行结束后，用程序输出的唯一`run_id`定位运行目录：它位于输出父目录下，即本例的`../community-results/run_id`。使用该目录执行：

```sh
python scripts/experiments/verify_result.py --result path/to/run-directory --data-root ../community-data --output ../verification-report.json
```

`path/to/run-directory`需要替换成实际输出目录；`--output`指定此前不存在的报告路径。缺少可信输入时，检查器返回NEEDS_REVIEW，不能将格式通过当作对独立数据和模型计算的核实。结果文件和提交过程见[结果提交说明](RESULT_SUBMISSION.md)。

## 故障恢复

无足够资源、缺依赖、下载失败、哈希不一致、超时、任一臂异常或产出不全时，保留日志与状态，说明已执行和未执行的步骤，不补造结果，也不通过删除失败臂让结果变绿。查明原因并与维护者协调后，新建运行目录重试；不要覆盖原记录，不改变主指标或挑选有利种子。无法验证资源保护的平台暂停正式运行。
