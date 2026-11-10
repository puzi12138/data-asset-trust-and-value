# EXP-02：Concrete Compressive Strength任务条件价值复现

状态：Windows 10/11 x64 任务。请在对应Issue留言计划运行的种子和环境；维护者协调后再运行。重复执行同一数据来源与配置是复现，不是新增独立数据集。

## 研究问题与数据

在固定D0、候选池、目标模型和新增64条数据预算下，不同数据选择方法对Concrete Compressive Strength任务的损失及实际训练成本有什么影响？数据来源：[UCI Concrete Compressive Strength](https://archive.ics.uci.edu/dataset/165/concrete+compressive+strength)；数据许可记录为[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)，下载来源与逐文件哈希以`experiments/community/data/README.md`及当前配置为准。访问原始数据前应检查许可和归属要求。

- 官方下载：[UCI原始压缩包](https://archive.ics.uci.edu/static/public/165/concrete+compressive+strength.zip)。下载脚本检查配置中的固定哈希。
- 任务类型：回归；模型：`Ridge(alpha=1)`。
- 主要评价：MAE；同时报告RMSE、R²。收益定义为同一评价集上的`D0损失 - 选择臂损失`。
- 协议配置：`experiments/community/configs/EXP-02/1103.json`、`2207.json`、`3301.json`；确认配置和输入摘要后运行。
- 随机种子：1103、2207、3301；每次只运行一个种子，不删去失败或不利结果。
- 目标训练臂：每个种子12个，包含D0；另拟合1个来源符合性RF。

分组隔离下的冻结成员数量如下（评价成员来自公开数据）：

| 种子 | D0 | 候选池 | 开发集 | 评价集 |
| ---: | ---: | ---: | ---: | ---: |
| 1103 | 304 | 361 | 184 | 181 |
| 2207 | 310 | 363 | 174 | 183 |
| 3301 | 301 | 366 | 183 | 180 |

## 需要完整执行的对照

D0、随机、随机+组覆盖、证据、证据+组覆盖、RF来源符合性、RF+组覆盖、证据+RF+组覆盖、完整率、距离、固定质量分；回归另含条件MC Data Shapley，8个完整排列并使用开发集标签。每个选择臂都使用同一新增预算；来源比对证据不等于自然标签正确性。保存完整选中成员、各臂指标、逐条评价和失败，不能只上传最好结果。

## 资源与依赖

Python 3.10和`requirements-experiments.txt`中的固定版本；CPU执行，最多2个数值计算线程，`--max-workers 1`，不需要GPU，也不会下载大型模型。正式运行启动前系统可用内存至少1.5 GiB、运行中至少0.75 GiB、进程树RSS上限1.5 GiB，单个种子最多300秒。建议预留至少4 GiB可用内存；这些是门槛和保守建议，不是本任务实测的耗时或峰值。请根据手册检查当前平台资源监督支持状态，遇到超时或资源不足即停止并保留状态。

## 一、准备 Windows 环境（首次执行）

在任务 Issue 下先留言认领模板，等待维护者协调：

~~~text
我要认领：EXP-02
运行顺序：1103、2207、3301
系统：Windows 10/11 x64
预计CPU线程及空闲内存：
预计开始和完成时间：
是否为已有任务的重复复算：
~~~

准备 Windows 10/11 x64、Git for Windows 和带 Python Launcher 的 64 位 Python 3.10；安装来源分别为 https://git-scm.com/download/win 和 https://www.python.org/downloads/windows/ 。安装时保留 Python Launcher；不要使用管理员权限或修改 PowerShell 执行策略。请预留至少 4 GiB 系统可用内存作为保守建议；正式门槛是启动可用内存至少1.5 GiB、运行时至少0.75 GiB、工作进程树RSS不超过1.5 GiB、每次不超过300秒。单次只运行一个worker，最多2个数值计算线程，CPU即可，不需要GPU。

打开普通 PowerShell，将下面整段一次粘贴。它只在用户目录创建新的副本，若同名目录存在会停止，不覆盖已有文件；出现错误请停止并回报，不要继续下一段。

~~~powershell
& {
    $ErrorActionPreference = 'Stop'
    if (-not [Environment]::Is64BitOperatingSystem) { throw '需要64位Windows。' }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw '请先安装Git for Windows。' }
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw '请先安装64位Python 3.10及Python Launcher。' }
    $global:DATV_Repo = Join-Path $env:USERPROFILE 'data-asset-contrib'
    $global:DATV_Blocked = $false
    if (Test-Path -LiteralPath $global:DATV_Repo) { throw 'data-asset-contrib 已存在，请勿覆盖；请联系维护者确定已有副本的使用方式。' }
    git clone --branch collaboration/community-experiments-v1 --single-branch https://github.com/puzi12138/data-asset-trust-and-value.git $global:DATV_Repo
    if ($LASTEXITCODE -ne 0) { throw '下载仓库失败，停止。' }
    git -C $global:DATV_Repo switch --detach 82489db75b55622f871d6585a4292d9ac23595d9
    if ($LASTEXITCODE -ne 0) { throw '无法切换到固定执行提交，停止。' }
    $Head = (git -C $global:DATV_Repo rev-parse HEAD | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $Head -ne '82489db75b55622f871d6585a4292d9ac23595d9') { throw '提交不匹配，停止。' }
    Set-Location -LiteralPath $global:DATV_Repo
    py -3.10 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '创建Python环境失败，停止。' }
    $global:DATV_Python = Join-Path $global:DATV_Repo '.venv\Scripts\python.exe'
    & $global:DATV_Python -c "import sys,struct; assert sys.version_info[:2] == (3,10) and struct.calcsize('P')*8 == 64; print('Python 3.10 x64 OK')"
    if ($LASTEXITCODE -ne 0) { throw 'Python版本或位数不匹配，停止。' }
    & $global:DATV_Python -m pip install --disable-pip-version-check -r requirements-experiments.txt
    if ($LASTEXITCODE -ne 0) { throw '依赖安装失败，停止。' }
    Write-Host '环境安装完成；请继续执行下一段。'
}
~~~

成功判据：屏幕出现 Python 3.10 x64 OK 和“环境安装完成”。执行提交固定为上面完整哈希；当前协作分支可能只含之后的文档或CI变动。无需激活虚拟环境；后续均调用其 python.exe。请保持同一PowerShell窗口，下一段使用本段变量。

## 二、执行一个种子，按顺序重复

首次使用下面的1103。仅当它完整成功且独立复算为REPRODUCED时，才将下面唯一的 $Seed = 1103 改为2207，再完整粘贴本段；2207成功后同样改为3301。三个种子必须依次运行，不能并行或跳过失败。每个任务的三个种子共用一个结果父目录和1800秒本机账本；账本不跨任务或机器，不能通过更换目录规避限制。

每次运行均由程序产生唯一run_id，追加到 seed_run_index.csv；不要按文件时间盲选最新目录。先前同一任务/种子若已有记录，本段会停止，须由维护者确认是否重跑。数据目录已经存在时不会再次准备、修改或覆盖；接下来的dry-run仍会重新校验所有哈希。把整段一次粘贴：任何数据准备或预检失败会终止本段，不会继续训练。

~~~powershell
& {
    $ErrorActionPreference = 'Stop'
    try {
        if ($global:DATV_Blocked) { throw '上一步已失败或待复核，请先联系维护者。' }
        if (-not $global:DATV_Repo -or -not (Test-Path -LiteralPath $global:DATV_Python)) { throw '请先完成第一段并保持同一窗口。' }
        $TaskId = 'EXP-02'
        $Seed = 1103
        if ($Seed -notin @(1103,2207,3301)) { throw '种子仅允许1103、2207、3301。' }
        Set-Location -LiteralPath $global:DATV_Repo
        $Head = (git -C $global:DATV_Repo rev-parse HEAD | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $Head -ne '82489db75b55622f871d6585a4292d9ac23595d9') { throw '执行提交已改变，停止。' }
        $Workspace = Join-Path (Split-Path -Parent $global:DATV_Repo) 'data-asset-contrib-work'
        $DataRoot = Join-Path $Workspace 'data'
        $TaskRoot = Join-Path (Join-Path $Workspace 'tasks') $TaskId
        $ResultRoot = Join-Path $TaskRoot 'results'
        $CheckRoot = Join-Path $TaskRoot 'checks'
        $IndexPath = Join-Path $TaskRoot 'seed_run_index.csv'
        New-Item -ItemType Directory -Path $CheckRoot -Force | Out-Null
        $Previous = @()
        if (Test-Path -LiteralPath $IndexPath) { $Previous = @(Import-Csv -LiteralPath $IndexPath) }
        if (@($Previous | Where-Object { $_.seed -eq "$Seed" }).Count -gt 0) { throw '此种子已有运行记录；请勿覆盖或自动重试。' }
        if ($Seed -ne 1103) {
            $PriorSeed = if ($Seed -eq 2207) { '1103' } else { '2207' }
            $Prior = @($Previous | Where-Object { $_.seed -eq $PriorSeed } | Select-Object -Last 1)
            if ($Prior.Count -ne 1 -or $Prior[0].run_status -ne 'COMPLETE' -or $Prior[0].independent_verify -ne 'REPRODUCED') { throw '前一个种子未完成并通过复算，停止。' }
        }
        $EnvText = & $global:DATV_Python 'scripts/experiments/check_env.py' | Out-String
        if ($LASTEXITCODE -ne 0) { throw '环境检查失败，停止。' }
        $EnvState = $EnvText | ConvertFrom-Json
        Write-Host $EnvText
        if ($EnvState.os -ne 'Windows' -or $EnvState.resource_platform -ne 'WINDOWS_JOB' -or -not $EnvState.scientific_preflight_memory_ok) { throw '系统或启动可用内存不符合要求，停止。' }
        foreach ($Package in @('numpy','scipy','scikit-learn','psutil','threadpoolctl','xlrd')) {
            if (-not $EnvState.packages.$Package) { throw ('缺少依赖：' + $Package) }
        }
        $Prepared = Join-Path (Join-Path $DataRoot $TaskId) "$Seed"
        if (Test-Path -LiteralPath $Prepared) {
            Write-Host '数据目录已存在，不重建；下一步dry-run会核对哈希。'
        } else {
            & $global:DATV_Python 'scripts/experiments/prepare_data.py' --task-id $TaskId --seed $Seed --output $DataRoot
            if ($LASTEXITCODE -ne 0) { throw '数据准备失败，停止，禁止继续训练。' }
        }
        $DryText = & $global:DATV_Python 'scripts/experiments/run_task.py' --task-id $TaskId --seed $Seed --data-dir $DataRoot --output $ResultRoot --max-workers 1 --timeout 300 --device cpu --dry-run | Out-String
        $DryExit = $LASTEXITCODE
        Write-Host $DryText
        if ($DryExit -ne 0) { throw '预检失败，停止，禁止继续训练。' }
        $Dry = $DryText | ConvertFrom-Json
        if ($Dry.status -ne 'DRY_RUN_READY' -or -not $Dry.input_hashes_verified) { throw '预检未就绪或输入哈希不匹配，停止。' }
        $RunText = & $global:DATV_Python 'scripts/experiments/run_task.py' --task-id $TaskId --seed $Seed --data-dir $DataRoot --output $ResultRoot --max-workers 1 --timeout 300 --device cpu | Out-String
        $RunExit = $LASTEXITCODE
        Write-Host $RunText
        try { $Run = $RunText | ConvertFrom-Json } catch { throw '没有可解析的run_id；请保留屏幕错误并联系维护者，不要猜测最新目录。' }
        $RunId = [string]$Run.run_id
        if ($RunId -notmatch '^\d{8}T\d{6}Z-[0-9a-f]{32}$') { throw 'run_id缺失或格式不符；不要猜测最新目录。' }
        $RunDir = Join-Path $ResultRoot $RunId
        if (-not (Test-Path -LiteralPath $RunDir -PathType Container)) { throw '结果目录不存在；保留屏幕错误并联系维护者。' }
        $global:DATV_LastRunId = $RunId
        $global:DATV_LastRunDir = $RunDir
        $VerifyStatus = 'NOT_RUN'
        $VerifyExit = -1
        if ($RunExit -eq 0 -and $Run.status -eq 'COMPLETE' -and $Run.verify_status -eq 'REPRODUCED') {
            $VerifyPath = Join-Path $CheckRoot ("{0}-{1}-{2}.json" -f $TaskId,$Seed,$RunId)
            if (Test-Path -LiteralPath $VerifyPath) { throw '独立复算报告已存在，不覆盖。' }
            & $global:DATV_Python 'scripts/experiments/verify_result.py' --result $RunDir --data-root $DataRoot --output $VerifyPath
            $VerifyExit = $LASTEXITCODE
            if (Test-Path -LiteralPath $VerifyPath -PathType Leaf) {
                $VerifyStatus = (Get-Content -LiteralPath $VerifyPath -Raw | ConvertFrom-Json).status
            }
        }
        $Record = [PSCustomObject]@{task_id=$TaskId; seed="$Seed"; run_id=$RunId; run_status=$Run.status; run_exit="$RunExit"; automatic_verify=$Run.verify_status; independent_verify=$VerifyStatus; independent_exit="$VerifyExit"}
        if (Test-Path -LiteralPath $IndexPath) {
            $Record | Export-Csv -LiteralPath $IndexPath -NoTypeInformation -Encoding UTF8 -Append
        } else {
            $Record | Export-Csv -LiteralPath $IndexPath -NoTypeInformation -Encoding UTF8
        }
        Write-Host ('本次结果目录：' + $RunDir)
        Write-Host ('种子与run_id清单：' + $IndexPath)
        if ($RunExit -ne 0 -or $Run.status -ne 'COMPLETE' -or $VerifyExit -ne 0 -or $VerifyStatus -ne 'REPRODUCED') {
            $global:DATV_Blocked = $true
            Write-Warning '未完整通过。保留此run_id、已生成结果、账本和检查报告；请执行下面打包段并在Issue反馈，不要继续其他种子或重复尝试凑成功。'
        } else {
            Write-Host '该种子完整运行且独立数值复算为REPRODUCED。'
        }
    } catch {
        $global:DATV_Blocked = $true
        throw
    }
}
~~~

成功判据：dry-run显示DRY_RUN_READY且input_hashes_verified为true；正式运行退出码0、status为COMPLETE、自动及独立复算均为REPRODUCED。正式运行返回2表示失败或待复核：结果目录和账本应保留，不能改数据、改阈值、跳过失败或自行反复运行。若屏幕没有run_id，保留错误并联系维护者，不得猜测他人的“最新目录”。

复算报告位于仓库外的checks文件夹，不写入run目录；这能避免改变运行清单。REPRODUCED只表示给定输入及保存结果能在检查范围内复算，不等于科学验收或证明机器身份。

## 三、打包完整结果并计算 SHA-256

完成三个种子后打包；任何一步失败则停止，并打包已产生的全部运行和失败记录。下面创建唯一新ZIP，包含本任务results（包括账本和全部run）、checks和seed_run_index.csv，不包含数据、虚拟环境、源码或内部审阅包。没有产生结果时，请直接在Issue描述屏幕错误。

~~~powershell
& {
    $ErrorActionPreference = 'Stop'
    if (-not $global:DATV_Repo) { throw '请先完成环境准备。' }
    $TaskId = 'EXP-02'
    $Workspace = Join-Path (Split-Path -Parent $global:DATV_Repo) 'data-asset-contrib-work'
    $TaskRoot = Join-Path (Join-Path $Workspace 'tasks') $TaskId
    $ResultRoot = Join-Path $TaskRoot 'results'
    $CheckRoot = Join-Path $TaskRoot 'checks'
    $IndexPath = Join-Path $TaskRoot 'seed_run_index.csv'
    $PackageRoot = Join-Path $TaskRoot 'packages'
    if (-not (Test-Path -LiteralPath $ResultRoot -PathType Container)) { throw '没有可打包的结果目录，请反馈屏幕错误。' }
    $Nodes = @(Get-ChildItem -LiteralPath $ResultRoot -Force -Recurse)
    if (@($Nodes | Where-Object { ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 }).Count -gt 0) { throw '结果中有链接；停止并联系维护者。' }
    New-Item -ItemType Directory -Path $PackageRoot -Force | Out-Null
    $ZipName = $TaskId + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N') + '.zip'
    $ZipPath = Join-Path $PackageRoot $ZipName
    if (Test-Path -LiteralPath $ZipPath) { throw 'ZIP已存在，不覆盖。' }
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::CreateFromDirectory($ResultRoot,$ZipPath)
    $Archive = [IO.Compression.ZipFile]::Open($ZipPath,[IO.Compression.ZipArchiveMode]::Update)
    try {
        if (Test-Path -LiteralPath $CheckRoot -PathType Container) {
            foreach ($File in @(Get-ChildItem -LiteralPath $CheckRoot -File -Force)) {
                if (($File.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw '检查报告包含链接，停止。' }
                [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($Archive,$File.FullName,('checks/' + $File.Name)) | Out-Null
            }
        }
        if (Test-Path -LiteralPath $IndexPath -PathType Leaf) {
            [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($Archive,$IndexPath,'seed_run_index.csv') | Out-Null
        }
    } finally { $Archive.Dispose() }
    $global:DATV_Zip = $ZipPath
    $global:DATV_ZipSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $ZipPath).Hash
    Write-Host ('ZIP路径：' + $global:DATV_Zip)
    Write-Host ('ZIP SHA-256：' + $global:DATV_ZipSha)
    if (Test-Path -LiteralPath $IndexPath) { Import-Csv -LiteralPath $IndexPath | Format-Table -AutoSize }
}
~~~

打包出错时不要发送该ZIP。发送前请检查日志和报告是否含凭据、私人路径或其他敏感信息；不要编辑或删除run目录内的README、日志、JSON和其他文件，这会破坏运行清单绑定。发现敏感信息时先暂停公开，在Issue只报告概况并与维护者约定处理方式。若附件大小受限，先与维护者约定可访问存储及保留期限；不要公开带令牌的链接。Issue附件不会自动触发结果CI。

## 四、回帖与验收

回到本任务Issue，将上述ZIP拖入回帖并粘贴SHA-256，填写以下表格；不要把原始数据、虚拟环境或内部审阅包上传。运行中断也要如实填写，已有结果和日志一并打包。

~~~text
任务：EXP-02
种子与run_id（按seed_run_index.csv）：
每个种子的状态及退出码：
自动复算 / 独立复算状态：
系统和可用内存概要（不要写用户名、私人路径或私密联系方式）：
是否改变配置或协议：否 / 如有请说明
异常及已停止步骤：
结果ZIP附件或经维护者认可的下载方式：
ZIP SHA-256：
保存期限：
~~~

请维护者核对所有训练臂、成员、实际成本和失败记录。自动状态为REPRODUCED不等于SCIENTIFICALLY_ACCEPTED；后者需要维护者独立审核。公开评价数据不是隐藏盲测。后续只在维护者确认主分支已含可信校验器并同意后，才将选定完整run目录提交到以当前协作分支为基线的PR；本阶段普通贡献者先走Issue附件。具体PR步骤见仓库 docs/RESULT_SUBMISSION.md。
