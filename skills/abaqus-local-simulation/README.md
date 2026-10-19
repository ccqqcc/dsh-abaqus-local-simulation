# Abaqus跨Agent调用包：Skill + CLI + MCP

本包将本项目实际使用的Abaqus调用方式、踩坑经验和证据规则封装为可移植入口。三个入口使用同一套后端，避免一个agent报RUNNING、另一个agent按旧缓存计算结果。无需第三方Python包；编写/调度使用Python≥3.9，ODB读取通过本机Abaqus自带Python。

完整调用手册：[ABAQUS_CALLING_HANDBOOK.md](references/ABAQUS_CALLING_HANDBOOK.md)。子程序和故障专题见`references/`。本项目历史全面复盘不随包复制整个研究工作区，可在本机参考：

`D:/ai_abaqus/3DPC_IBM_C50_RESTART_20260921/08_PROJECT_RETROSPECTIVE_20261004/PROJECT_ROUTES_PROBLEMS_LESSONS_AND_ABAQUS_CALLS.md`

## 1. 包含哪些实际能力

| 能力 | 入口 | 行为/边界 |
|---|---|---|
| 环境检查 | `doctor` / `abaqus_doctor` | 本机版本、硬件、盘、工具链路径、现有solver；许可证签出不冒充已验证 |
| 输入/依赖审计 | `plan` / `abaqus_plan_job` | 递归INCLUDE、额外文件、代码/环境哈希、CPU/RAM预算、命令、计划token；不求解 |
| 输入预检查 | request `operation=datacheck` | 独立、明确授权的job，不自动覆盖原job |
| Standard/Explicit提交 | `submit` / `abaqus_submit_job` | 一个job一个持久记录/后台worker；Explicit双精度domain；需授权和token |
| 数值控制 | INP/请求文件 | 参数由用户任务确定；后端不自行改刚度/材料/BC/增量 |
| 用户子程序 | request `user_subroutine` | 添加`user=`；编译环境需经独立验证；不宣称所有Fortran代码均可运行 |
| 状态 | `status` / `abaqus_job_status` | PID创建时刻+LOG/MSG/STA/DAT，标明失联/陈旧，不凭缓存等待 |
| 有限自动停止 | request两个stop布尔开关 | 默认均false；用户批准后可墙时止损/严格峰后停止；不做taskkill |
| 精确停止 | `terminate` / `abaqus_terminate_job` | 仅自身登记且身份匹配的一个job；另需停止授权 |
| Restart证据 | `restart-audit` / `abaqus_restart_audit` | 文件清单，不伪造增量/状态；oldjob续算需额外状态验证 |
| ODB目录清单 | `probe mode=inventory` | steps、regions、RF/U字段、field keys，只读 |
| ODB峰值 | `probe mode=peak` | 显式region、配对/有限性、5%/2%/稳定临时规则、候选与有效峰分开 |
| 能量/近峰field | 同上 | KE/IE等、峰时/全程、field时间偏差、真实字段名、缺项报告 |

没有封装“自动创造材料本构”“自动校准目标”“自动恢复未知job”“自动重启Windows”“远程Abaqus集群”。几何建模、复杂空间机制、单位与实验真实性仍需agent依据真实任务处理。

## 2. 当前机器与迁移

已核验本机：Windows，Abaqus2023，`D:/SIMULIA/Commands/abaqus.bat`；Python3为`D:/anaconda3/python.exe`；32物理核/64逻辑线程，约63.89GiB内存；H盘NVMe。ifx/MSVC候选路径存在，不等于本轮重新编译验证了所有UMAT/UINTER。

本机Skill安装位置：`C:/Users/21050/.codex/skills/abaqus-local-simulation`。

迁移：解压整个skill目录到目标agent可访问位置；复制`assets/local_config.json`为该机器的配置文件，修改launcher、允许读写的项目根、registry、scratch及容量。配置中的工作区范围必须显式授权。Abaqus和合法许可证须在目标机器独立安装；包不附带求解器、许可证、专有模型/ODB或私有编译器。

配置要点：

- `registry_dir`：本包自己的任务和审计记录，不指向历史queue文件。
- `allowed_roots`：所有INP/include/user/ODB/work/scratch需要处于其内部。
- `scratch_roots`：每job只能使用其下独立、为空的子目录。
- `max_total_cpus`：物理核预算；`max_concurrent`和总内存预算同时限制。
- 有未登记/无法识别的现有solver会阻止新提交，不偷偷占用旧队列资源。
- 路径含cmd元字符会被拒绝；改用安全工作路径，不关闭校验。

## 3. CLI使用

下面路径是本机实例；在其他机器替换为解压位置。示例INP/region名称只是合同模板，不代表存在可立即提交的模型。

```powershell
$abaqusPython = 'D:\anaconda3\python.exe'
$abaqusBridge = 'C:\Users\21050\.codex\skills\abaqus-local-simulation\scripts\abaqus_agent.py'
& $abaqusPython $abaqusBridge doctor --query-release
& $abaqusPython $abaqusBridge plan --request 'D:\my_authorized_project\request.json'
```

审查返回的`sealed`、`command_preview`、`plan_token`；用户批准该job及数值/资源/停止策略后：

```powershell
& $abaqusPython $abaqusBridge submit --request 'D:\my_authorized_project\request.json' --plan-token '审查后返回的token' --authorize --authorization-note '用户已批准准确job、资源与请求中的停止策略'
& $abaqusPython $abaqusBridge status --job-id '提交后返回的32位id'
```

token不是许可证/密码；它绑定计划内容。输入、include、用户代码、局部env、后端代码或配置改变后必须重做plan。`authorize=true`是调用者记录的人工授权，不是防恶意客户端的身份认证。MCP客户端仍应启用工具审批。

读取ODB先做inventory，使用其真实step/region，不选“最大位移看起来像RP”：

```json
{"mode":"inventory","odb":"H:/STAGE7_20261003/S7_E1_X/S7_E1_X.odb"}
```

保存为新请求文件，再运行`probe --request 文件路径`。根据inventory填`assets/odb_peak_request.example.json`。新证据写入registry；原ODB只读。

## 4. WorkBuddy及其他MCP Agent

选择支持**本地stdio MCP**的客户端，添加一个服务器。不同产品的导入UI和配置文件位置不同；本包不擅自修改WorkBuddy设置，也不假定它支持某个专有skill格式。

可导入的本机JSON：`assets/mcp_client.local.example.json`。其常见`mcpServers`形式为：

```json
{
  "mcpServers": {
    "abaqus-local-simulation": {
      "command": "D:/anaconda3/python.exe",
      "args": [
        "C:/Users/21050/.codex/skills/abaqus-local-simulation/scripts/mcp_server.py",
        "--config",
        "C:/Users/21050/.codex/skills/abaqus-local-simulation/assets/local_config.json"
      ]
    }
  }
}
```

连接顺序：initialize → notifications/initialized → tools/list → abaqus_doctor。随后plan/request/token/人工批准/submit/status/probe。另提供四个只读MCP文档资源：`abaqus://handbook`、`abaqus://user-subroutines`、`abaqus://troubleshooting`、`abaqus://project-contract`，没有本机文件读取工具的agent也可通过resources/read获取指导。服务无HTTP端口，不会上传模型；stdout只输出UTF-8逐行JSON-RPC，solver输出写文件，不污染协议。客户端退出不自动终止独立job worker。

若目标agent没有MCP，但能调用命令行：直接使用CLI JSON接口并读取SKILL/手册。若可导入Skills：导入完整目录，指示其使用`$abaqus-local-simulation`或读取SKILL.md。三种入口不依赖彼此。

目标agent必须有本机命令执行权限，或能连接本机stdio MCP。仅把Skill文本发送给没有本机访问能力的云端agent，不能令其调用这台机器的Abaqus。

建议给其他agent的首次任务：

> 读取本包SKILL.md和README。先运行abaqus_doctor，核对路径、物理核、盘和已有作业。只审计我提供的模型并生成计划，暂不提交；不得更改物理参数、接管旧队列或把候选RF当峰值。

## 5. 验收范围

见`VALIDATION_REPORT.md`及`validation/`实际记录。测试包括命令规划/保护、峰值算法、MCP stdio握手与错误路径，以及本机Abaqus版本查询和既有ODB只读实测。打包本身不会提交新的有限元分析。新提交/编译/restart路径若仅被模拟测试覆盖，报告会明确写出，不能冒充新job的端到端验证。

许可证、真正求解、用户子程序版本兼容、WorkBuddy产品UI导入仍需在使用时验证。异常脚本非零返回码与JSON error不能写成物理模型失败。

## 6. 授权与来源

作者新编的桥接代码按根目录LICENSE使用；Abaqus/SIMULIA、官方文档及原研究项目不受该许可替代。包不包含第三方软件或文档全文。MCP实现参照[官方stdio规范](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)、[工具规范](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)和[生命周期](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle)。
