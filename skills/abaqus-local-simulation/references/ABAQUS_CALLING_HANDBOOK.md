# Abaqus调用全手册：从任务合同到可接受的ODB证据

本手册依据本机真实runner、Fortran环境、Stage6/7日志及此前的输入/后处理错误编写。它总结“如何调用”，不把历史失败模型当成可随便复用的标定模板。公开官方文档链接采用目前可访问的2025版本；本机实际版本2023的支持情况仍须以该版本文档、datacheck和运行日志核验。

## 1. 调用架构与两套Python

实际链条：

```text
用户任务/真实材料/几何/BC/验收标准
          ↓ 外部Python3，或CAE noGUI构建
INP + 递归include + user source + job-local abaqus_v6.env
          ↓ datacheck / 输入、单位和拓扑审计
外部Python3 queue → abaqus.bat → input processor → Standard/Explicit
          ↓ solver_stdout + .log/.msg/.sta/.dat + .odb
abaqus.bat python odb_probe.py → odbAccess(readOnly=True)
          ↓ paired history、field、energy、peak qualification
逐job证据 → 可重建汇总 → 物理/数值/机制验收 → 报告
```

外部Python3负责文件准备、哈希、并发、时间预算、状态和报告；不能直接用普通anaconda Python导入Abaqus2023的`odbAccess`。Abaqus2023自带Python2.7读取ODB；脚本不要使用f-string/pathlib/type annotations。新版本可能使用Python3，先查版本，不机械套用旧语法。

`abaqus python`通常适合odbAccess、文本/数学和后处理。使用`mdb`建模或CAE/Viewer绘图时，需要相应`cae noGUI`/`viewer noGUI`入口及可用许可证；不能因ODB读取成功就推断CAE或analysis许可证可用。

## 2. 调用前先定合同

至少明确：

1. 项目位置、源输入、geometry/material/interface/BC/loading/单位来源。
2. 本次是只读诊断、建模、datacheck、提交、restart还是停止；禁止扩张的行为。
3. 允许新增job的名称/数量/方向/参数点，复用条件，最大恢复次数。
4. 哪些物理量锁定，哪些纯数值控制可以变化；不能以“数值控制”名义改变加载路径或损伤耗散。
5. CPU/job、最大并发、总内存、scratch、磁盘保护、预计墙时；超时是否有停止权限。
6. 什么是有效结果：正式峰、筛查峰、provisional、无峰、异常；需要哪些field/energy/空间机制。
7. 每个stage是否自然运行、允许峰后停止、何时必须回用户决策。

没有真实材料/界面参数时可以做明确标记的mechanism screen或synthetic plumbing test，但不能声称是当前试件的实验复现。目标峰值不能代替完整CDP曲线或几何真值。

## 3. 环境、版本、许可证和资源

本机已存在：

```text
D:\SIMULIA\Commands\abaqus.bat
D:\SIMULIA\Commands\abq2023.bat
D:\SIMULIA\EstProducts\2023\win_b64
D:\anaconda3\python.exe
```

`abaqus.bat`转发`abq2023.bat`。调用时使用绝对路径；不要把PATH里另一个版本当成本项目运行版本。

读取版本示例（在新的审计目录执行）：

```powershell
& 'D:\SIMULIA\Commands\abaqus.bat' information=release
```

这可能生成信息/日志文件，所以不要把工作目录设为冻结的历史job。它不会运行一个FE分析，也不证明当前有可签出的solver license。

许可证：先确定站点使用FLEXnet还是DSLS，依其管理员/安装手册的合法状态查询命令检查。FLEXnet常见入口是`abaqus licensing lmstat -a`；并不适用于所有DSLS站点。不绕过许可、不自动修改许可证服务器地址、不把license等待当成材料收敛失败。输出中的服务器/用户信息不应随对外包泄露。本包doctor明确保留`license_availability=UNVERIFIED`。

本机2990WX为32物理核/64逻辑线程。4×8和16×2均按32物理核预算，不按64逻辑线程翻倍。总内存约63.89GiB；包配置预留49152MiB给已登记job，仍不代表操作系统实际可用内存恒定。并发还受license、NVMe空间、NUMA和日志/ODB I/O制约。现场先查看已有solver，不能把旧队列当不存在。

## 4. 模型构建与依赖审计

### 4.1 两种实际方式

1. 从已审计的INP/真实facet构建新的输入文本。Stage6/7主要如此，便于保留材料include和精确节点/facet。
2. CAE Python脚本创建mdb/model/job并writeInput，必要时run；历史V11使用：

```text
abaqus cae noGUI=main.py -- --run --analyze --calibrate
```

这只是历史脚本自己的参数，不能对其他main.py照搬`--calibrate`。新任务建模脚本应在授权的新目录运行，明确其写入和提交行为。使用CAE脚本不等于必须开启可见窗口。

### 4.2 构建后必须检查

- 坐标/加载轴、单位、试件尺寸、受力面积/测试公式；N-mm-MPa-tonne中的密度不能用kg/m³原数值。
- 节点/元素标签唯一、集合存在、每行关键字的数据列数量、有效材料定义顺序。
- 界面双节点是否独立；共节点会绕过预期cohesive跳跃。
- 只有用户批准的界面数和方向，没有遗留IFJ/竖直接口。
- 分面mesh细化时保持源facet、平台、拓扑及法向，不重新生成analytic sine再称pure h-refinement。
- 压板接触和层间接触分别核对；μ_platen与μ_interlayer不混用。
- 定位约束不致刚体运动，也不致过度锁死；MONO/TIE/elastic基准用于识别端面约束污染。
- CDP塑性应变转换、材料源哈希/逐行表；名称C50不等于输入表峰值50MPa。
- step/procedure/加载ramp/输出/数值控制；Explicit也不能悄悄引入质量缩放。

包的plan递归审计`*INCLUDE, INPUT=`，哈希INP/include/user/job-local env和`additional_files`。`FILE=`依赖、Fortran INCLUDE文件、分布表、共享动态库和安装层全局env不保证自动发现，agent必须显式列出或另建可信审计。循环include、缺文件、路径出界、已有job输出会阻止计划。

## 5. 输入检查与正式提交

实际datacheck范式：

```text
abaqus.bat job=CHECK_MODEL_X input=MODEL_X.inp cpus=2 memory=2048mb scratch=<独立检查scratch> datacheck interactive ask_delete=OFF
```

保留独立CHECK名称/目录，不先对正式job生成ODB后再用同名覆盖。检查DAT的ERROR、keyword支持、材料/接触/元素统计；返回码0只是启动/程序层信息。语法通过不等于物理模型正确。

普通Standard：

```powershell
& 'D:\SIMULIA\Commands\abaqus.bat' job=MY_X input=MY_X.inp cpus=2 memory=2048mb scratch=H:\ABAQUS_AGENT_SCRATCH\MY_X interactive ask_delete=OFF
```

Standard/Explicit由INP的procedure确定，不是job名字决定。`input=`可不写时一般默认为同名INP，本包仍显式传入已审计绝对路径。

Explicit：

```text
abaqus.bat job=MY_EXPLICIT_X input=MY_EXPLICIT_X.inp cpus=8 memory=2048mb scratch=<独立scratch> interactive ask_delete=OFF double=both parallel=domain domains=8
```

`double=both`和domain设置是本项目竞速使用方式，不是所有任务都必须8CPU。2CPU筛查时domains=2，不能把domains留成8而不核验支持。Explicit输入必须包含真实`*DYNAMIC, EXPLICIT`和相应density/时间/输出，不能仅改launcher参数就把Standard deck变Explicit。

`interactive`使launcher随分析保持同步生命周期，便于worker捕获返回码，并非要求GUI交互。`ask_delete=OFF`会关闭删除询问；它不提供“禁止覆盖”的保护。包额外拒绝已有`.odb/.lck/.res/...`，不要关闭此检查。

Windows .bat会通过cmd.exe；空格、引号和元字符非常容易污染命令。本包参数不接收任意额外command字符串，逐参数验证并使用显式BAT wrapper，隐藏窗口。不要拼接不可信shell文本或用通配符删除结果。

## 6. 单job目录、后台worker和状态

建议布局：

```text
AUTHORIZED_PROJECT/jobs/MY_X/
  MY_X.inp
  common_material.inp
  common_interface.inp
  user.f90                 如需
  abaqus_v6.env            经审计的局部配置，如需
  MY_X.log/.dat/.msg/.sta/.odb/...
H:/ABAQUS_AGENT_SCRATCH/MY_X/
registry/job-<managed-id>.json
registry/worker-<managed-id>.log
registry/probe-<unique-id>-result.json
```

submit只在批准的job目录生成solver文件，registry不指向历史queue。独立worker监控自己的launcher；MCP客户端关闭不会自动停止它。worker记录PID及Windows创建时间，PID重用后不会把别的进程当自己。

状态含义：

- `RESERVED/STARTING/RUNNING`：启动和活进程层状态，不是有有效峰。
- `STOP_REQUESTED`：已请求终止，不能说已停止。
- `COMPLETED`：日志有完成标记；仍需ODB验收。
- `ABORTED`：分析终止；此前有可靠峰后下降的ODB仍可能具备峰值证据。
- `ENDED_UNCONFIRMED`：launcher退出但未发现明确完成/终止事实，需日志收口。
- `STALE_RECORD_NEEDS_RECONCILIATION`：记录未收口且无匹配活进程，不能继续叫RUNNING。
- `ORPHANED_LAUNCHER/SUPERVISOR_FAILED_LAUNCHER_MAY_BE_RUNNING`：监控失联，但solver可能存在；不自动重提。

worker超时只有`terminate_at_walltime=true`时才请求Abaqus终止；否则标attention并继续只读观察。`stop_after_confirmed_peak=true`须有明确region的probe，严格确认且质量合格才stop。默认两个开关均false，原先“自然运行不得打断”的任务不会被这一包改写。

本包无Windows自启动服务。主机重启后应先查进程、STA/ODB时间和任务文件，辨别自然结束/意外中断，再决定恢复授权。不要把“MCP能重新启动”当作“solver能自动restart”。

## 7. 吞吐、queue和输出

先做与实际规模接近的短benchmark，比较jobs/hour而非只比较单job时间；一个8核job更快，不代表8×4更高吞吐。本机曾短测2×16优于4×8/8×4，宏观损伤模型仍需重测。

队列只允许预先授权的清单，不由结果自动扩展新参数。可通过MCP逐job submit，或外部queue按status/capacity补槽。所有存活race/job均计入总CPU和内存；不要race4×8未结束又立刻开16×2。每job完成立即probe/update，不等全批。

此通用包负责容量保护和每jobworker，不内置本项目A–μ排名/自动补Y；此类物理决策应另写stage-specific控制器。不要复制旧controller并沿用它的alias、schema、stage权限或缓存。

scratch用真实最快盘、每job独立、预留保护空间。H盘是此机器970PRO NVMe，不代表其他机器H盘一样快。磁盘保护下限8GiB是本地筛查选择，迁移需调整；ODB和用户trace可能是主要瓶颈。

history高频RF/U，Explicit至少ALLKE/ALLIE，必要时ALLAE/ALLVD/ALLSD/ALLCD/ALLDMD/ETOTAL。field低频选损伤、应力、塑性及界面接触；不要输出全部field每increment。低频field与history峰不重合时报告时间偏差，必要的精确峰帧应在运行前规划，而不是后处理造出。

## 8. Restart和恢复

Standard通常需`.res/.mdl/.stt/.prt/.odb`等；Explicit还可能有`.abq/.pac/.sel`，具体analysis/version有差异。先查写出restart请求、实际非空文件、版本/精度/模型一致性，以及哪一个increment真的保存了restart状态。ODB最后frame不代表那个increment可续算。[官方restart说明](https://docs.software.vt.edu/abaqusv2025/English/SIMACAEANLRefMap/simaanl-c-restart.htm)。

包`restart-audit`只做清单，`restart_state_verified=false`；不把存在文件自动写成TRUE。`oldjob`是原job的绝对路径stem，不加.res；新job必须另名、有`*RESTART, READ`，并由独立证据确认state/increment。原状态是读取对象，不改ODB/INP。

```text
abaqus.bat job=MY_X_RESTART input=MY_X_RESTART.inp oldjob=<原job绝对路径stem> ...
```

没有state时，从头运行只能作为明确批准的recovery copy；几何/材料/界面/BC/加载仍锁定，只调许可的数值控制。较小初始/最小增量不保证跨峰；Riks、隐式dynamic、stabilization、Explicit各有不同假设，需要用户合同。恢复次数有上限，不能无限重试或把provisional升级为final。

本包不自动把*.res文件搬到新目录，也不复制未知restart家族；原job仍运行时更不能创建不一致state快照。

## 9. ODB选择、反力和峰值

实际入口：

```text
abaqus.bat python scripts/odb_probe.py request.json result.json
```

在Abaqus Python内部`openOdb(path=...,readOnly=True)`，finally关闭。2023本机Python2对unicode path曾抛TypeError，适配filesystem编码；中文路径仍需实际确认，推荐模型/脚本技术路径用ASCII、中文留在报告。

先inventory所有steps、historyRegions和field keys；显式指定加载RP。如果用top节点集合：各节点RF需同一时间、相同位移定义，先求有符号总和再取幅值；位移不一致时不能盲目平均。底部定位RP不是加载RP。Material3旧例单节点5727N与top total215726N差巨大，说明自动选最大U或一个节点均不能替代loadpath审计。

单位和强度公式由试验决定：40³轴压N/1600=MPa；100³为N/10000；劈裂和抗折有另外公式。通用probe只输出力/位移，刻意不自动按1600除。

默认单调压缩峰规则，可配置：

| 状态 | 曲线证据 | 使用边界 |
|---|---|---|
| PEAK_VALID | 全history最大不是endpoint；≥3后点；持续增位移；下降≥5%；无非有限/重复/失配 | 仍需质量/能量/机制gate，不等于job整步完成 |
| SCREEN_PEAK_VALID | 同样clean，下降≥2% | 仅快速筛查，不能冒充严格5% |
| PEAK_STABLE_PROVISIONAL | 峰后≥30点、无更高点、位移增加、后段负斜率、下降≥0.5% | 返回形态证据，但`usable_peak=false`直到有明确provisional使用策略 |
| NO_CONFIRMED_PEAK | 终点最大或缺下降支持 | candidate_RF保留为诊断，RF_peak为空 |
| INVALID_HISTORY / INVALID_QUALITY | 非有限、重复、失配、能量/符号/field异常 | 不进入比值表，不叫物理无效 |

history点必须为accepted结果，不能来自Newton尝试/迭代trace。包检查时间重复/失配，但不能单靠history推断所有localstate正确。循环、卸载—再加载或非单调测试需要专用算法；当前通用probe不用于这类峰值定义。

postpeak minimum是下降响应，不是peak lower bound。不会再使用[min postpeak,candidate peak]造不确定区间。变更peak门槛须记版本，不能追溯改历史结果。

## 10. 能量、准静态与跨solver可比性

Explicit准静态常用ALLKE/ALLIE<5%，尽量<2%，但这是筛查规则，不是所有模型的数学充分条件。至少检查加载承载段的峰时和全程最大值；startup零能量需要合理处理，不能把0/0填0。probe对RF≥20%candidate的点计算比值，包含postpeak，energy只能时间插值，不插值力来制造峰，也不越界外推。

ALLAE检查hourglass/artificial strain；ALLVD检查耗散但不替代ALLCD；stabilized Standard需ALLSD/ALLIE小且不污染loadpath。需要某energy却没有输出，保持missing/invalid，不能由另一个量替代。两个可用加载速度的峰差通常是重要校核，本项目E1/E2约0.93%，仍不自动验证尾段损伤耗散。

同一个C50输入卡在Standard包含CDPviscosity=1e-5；[官方关键字](https://docs.software.vt.edu/abaqusv2025/English/SIMACAEKEYRefMap/simakey-r-concretedamagedplasticity.htm)说明该参数在Explicit忽略。Standard small-sliding contact pair和Explicit general contact也不等同。因此不能拿Explicit X与旧Standard Y/Z直接拼XYZ通过；完整比较需同一求解合同。

## 11. Field、损伤位置和机制归因

真实字段名可能带contact pair后缀，CPRESS裸名不存在不代表ODB无压力。SDEG(元素cohesive)、CSDMG(surface cohesive)、UINTERκ/历史变量、bulkDAMAGEC/DAMAGET/PEEQ互不等价。STATEV不能输出时报告缺项/trace限制，不制造“界面SDEG”。

通用probe给实际近峰frame、offset、字段min/max/mean/P95及非有限数。其统计按output point计数，不是面积/体积；不同积分点/重复位置不能混成几何比例。SDEG>0.1/0.5、QUADSCRT≥1的面积份额须用源facet面积/积分权重及去重mapping；首次损伤U/Upeak须有时间分辨率和损伤定义，必要时报bracket。

损伤位置比较应在相同parent facet/几何部位/区域层次，不要求细网格逐元素相同；新failure band、异常localization、bulk/interface主导转换要单独识别。最大DAMAGEC/PEEQ数值改变不等于损伤区域换了，也不能忽略。

层间压力p、total shear、cohesive shear、friction stress/slip需分清。只有total CSHEAR不能硬算frictional contribution；`total-(1-D)Ks*CSLIP`未验证弹性/塑性slip分解和Coulomb容量时可能失真。本项目甚至Y D=0仍产生残差，正式限制为MECHANISM_ATTRIBUTION_LIMITED。[官方cohesive contact](https://docs.software.vt.edu/abaqusv2025/English/SIMACAEITNRefMap/simaitn-c-cohesivebehavior.htm)区分压缩normal contact、损伤cohesive和摩擦贡献。

## 12. 安全、验收和交接

- submit/terminate需要直接人工授权；客户端传true不构成安全认证，不能把本地MCP开放到网络或给任意用户。
- 无自动retry/恢复/标定，不改旧queue，不移动旧state，不删未知lck/scratch。
- 准确job终止：`abaqus terminate job=MY_X`。若无效先查该job而不是杀所有standard.exe/explicit.exe；本包不提供taskkill兜底。
- LOG/DAT/MSG/STA/ODB分别承担启动/输入/收敛/accepted进度/科学证据；launcher0、DatacheckPASS、ODB存在、final文件名都不是模型通过。
- 最终交付列清源hash、命令、控制/资源、job终态、峰资格、能量、field缺项、原始路径、禁止声称的结论。
- implementation gate、numerical gate、method screen、experimental validation分开；缺真实材料/拓扑时保持数据边界。

本机来源入口：Stage6C0 `run_stage6c0.py`，Stage7 `build_stage7.py/control_stage7.py/probe_stage7.py/watchdog_stage7.py`及各job的`abaqus_v6.env`。本包是提炼/加保护的新实现，不接管这些历史脚本。
