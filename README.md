# Data Asset Trust and Value

数据资产可信度与任务条件价值评估工程。

项目分别回答两个问题：数据是否具有可靠、完整、可追溯的证据，以及它在指定任务、模型和预算下能够带来什么收益。可信度、任务适配和实际效用分别输出，不合成一个无法解释的总分。

## 工程结构

| 目录 | 功能 |
|---|---|
| `src/data_asset_poc/assessment/` | 资产与成员绑定、七维数据集审计、证据准入、数据筛选与价值计算 |
| `src/data_asset_poc/task_verification/` | 独立任务解释、合同一致性、回答核验、任务授权与共享证据策略 |
| `src/data_asset_poc/` 下的服务模块 | 数据交付、可信审计、任务测量、视觉证据与同版本联合评估 API |
| `frontend/` | Vue / TypeScript 工作台、资产目录、可信与价值评估界面 |
| `scripts/` | 统一服务入口、工程检查及受资源监督的训练入口 |
| `tests/` | 算法、API、边界条件和无训练虚拟进程测试 |

## 安装与检查

使用 Python 3.10（本次验证版本）。建议在独立虚拟环境中安装所需依赖。

```sh
python -m venv .venv
# 激活虚拟环境后运行：
python -m pip install -r requirements.txt
python scripts/check_engineering.py --suite core
```

Web 服务使用独立依赖清单；其中 Pydantic 1 是当前 API 模型的运行要求。

```sh
python -m pip install -r requirements-web.txt
python scripts/check_engineering.py --suite web
python scripts/serve.py --help
```

创建位于源码目录之外的新空工作区，再启动本机服务：

```sh
python scripts/serve.py init --workspace ../data-asset-runtime --port 8040
python scripts/serve.py run --config ../data-asset-runtime/consumer.json --port 8040
```

初始化不会导入研究数据或连接外部服务。工作区保存本机运行身份和配置，不应提交到版本库；注册、交付节点及任务输入由使用者另行配置。先构建下述前端后，服务才能提供页面资源。

资源监督器当前支持 Windows Job Object。下述检查使用不会训练模型的虚拟工作进程，验证握手、超时、进程清理及预算限制；其他操作系统尚未完成该监督器的运行验证。

```sh
python -m pip install -r requirements-training.txt
python scripts/check_engineering.py --suite resources
```

## 前端

使用 Node.js 18 或更高版本及支持锁文件格式的 pnpm。

```sh
cd frontend
pnpm install --frozen-lockfile
pnpm test
pnpm typecheck
pnpm build
```

开发服务器将 `/api` 请求转发到 `127.0.0.1:8040`。服务和前端仅绑定本机；具体配置方式见 `scripts/serve.py --help`。

## 数据与运行条件

仓库包含工程代码和软件测试夹具。运行数据、模型、私钥、个人资料及研究结果均不随源码发布。测试夹具用于验证软件行为，不能作为真实训练或现实认证结果。

未配置的服务、证据或任务测量保持不可用或未评估状态。用户应提供自己有权使用的数据和明确的任务配置；不能把缺少证据视为通过。训练入口要求输入清单、代码冻结、成员隔离及资源预检，默认不会启动训练，也不会下载模型或数据。

逐记录参考覆盖接口当前未启用，其兼容响应不代表完成内容审计。新证据登记、通用可信审计和同版本联合评估可分别使用。

哈希绑定证明字节和版本一致性，不证明内容真实；参考符合性与独立事实核验分开表达。数据价值取决于任务、模型和预算，测试通过不证明真实训练收益或预测目标已经达标。

协议标识、字段名、签名与摘要绑定精确版本。外部已有收据需要按当前接口重新建立，不能仅替换文本后当作已验证材料。

第三方依赖由包管理器获取，并遵守各自许可；不随仓库复制依赖目录。
