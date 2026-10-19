# 用户子程序：调用、编译、接口与验收

## 1. 先区分procedure和入口

| 求解/用途 | 常见入口 | 关键区别 |
|---|---|---|
| Standard实体/材料 | UMAT | 返回stress/state/consistent tangent，调用含迭代试探与rollback |
| Explicit材料 | VUMAT | block接口，precision/array/layout必须匹配；不是把UMAT改名字 |
| Standard用户interface | UINTER | 局部法向/切向符号、接触配对、properties、Jacobian/state重要 |
| Explicit用户interface | VUINTER及版本对应接口 | block/precision/argument ABI与Standard不同，先核对版本手册 |
| 内置CDP/cohesive contact | 无user参数 | 不需要Fortran用户代码；不要为了调用而插入空user |

历史V11使用COH3D8 UMAT；Stage4与6C0使用UINTER；早期Explicit探索有VUINTER。本包不附送这些研究定律/源代码，不暗示其已获当前材料验证。

## 2. 实际提交方式

```text
abaqus.bat job=UINTER_X input=UINTER_X.inp user=uinter.f90 cpus=2 memory=3000mb scratch=<独立scratch> interactive ask_delete=OFF
```

Abaqus启动时调用编译器/链接器生成user库，再在solver中调用接口。Python调度不代替Fortran材料积分。一次链接成功不证明每种加载/STATEV正确。

包request加`user_subroutine`绝对路径，并把其Fortran INCLUDE文件/头文件/依赖库列入`additional_files`。相关文件和job-local env列入hash。安装级/site级环境还有继承影响，需要现场核验并记录，不是只审计一个env就闭合。

## 3. 本机验证过的环境事实

- Abaqus2023安装根：`D:/SIMULIA/EstProducts/2023`。
- Intel ifx：`D:/anaconda3/pkgs/ifx_impl_win-64-2025.3.1-intel_761/Library/bin/ifx.exe`。
- MSVC x64 link：`C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/VC/Tools/MSVC/14.44.35207/bin/Hostx64/x64/link.exe`。
- job-local env先放正确Intel Fortran/compiler runtime，再放旧PATH，避免旧Abaqus runtime抢先。
- 旧`ifort.bat`曾是转发ifx的适配器，不能据文件名宣称classic ifort已安装。

典型job-local配置做了什么：PATH前置VS x64/ifx/Intel runtime；LIB加入ifx、runtime、compiler shared、VS x64、Windows SDK um/ucrt；`compile_fortran`采用`ifx /c /fpp /extend-source /DABQ_WIN86_64 /DABQ_FORTRAN /iface:cref /recursive /Qauto ... /include:%I ... %P`。这些参数是此host的历史适配，不作为跨机器通用模板，更不保证Intel2025属于Abaqus2023官方支持矩阵。

迁移先核对该Abaqus版本的支持编译器、Windows SDK、MSVC、Intel精度和运行库。不要修改系统全局PATH/ABAQUS环境以修一个case；优先新增、审计job-local设置。job-local env本身是可执行Python，必须视为可信代码，不能运行来源不明的env。

## 4. 验证层次和历史故障

1. 版本/编译器可找到：路径存在还不是能运行。
2. 官方verify/smoke：编译、链接、加载。历史classic检测SKIPPED但实际官方uinternx通过；两个事实不矛盾。
3. 单材料点/patch：法向拉伸、剪切、压剪、开闭重新接触、旋转、不同方向；范围依据当前接口，不做无边界大套件。
4. properties回显：数量/排列/真实数值。Abaqus property数据行通常有列数限制；本项目12项曾错误放一行，改8+4后才正确。
5. 局部有限性/边界保护：NaN可能先于unpaired，ierr=0不能覆盖非有限state。
6. 数值Jacobian/切线：elastic tangent正确不代表非线性tangent正确；finite difference也须看step size/branch，不能只贴一个很小误差。
7. 试探与rollback：全局未accepted的试探调用不应永久累计damage/history或写成accepted历史。
8. ABI/precision：Explicit single/double mismatch曾破坏Kn等properties。wrapper与core、编译类型和运行`double=`必须一致。
9. 实际结构基准：总反力、局部符号/法向、BC/加载路径、energy、bulk/interface起裂。patch门通过不等于论文benchmark或C50实体通过。

本项目纠正过局部法向位移/应力符号：`g1=-RDISP1`和返回normal stress符号应按所采用论文及Abaqus约定统一转换；不能将此具体公式作为所有UINTER的通用规则。局部D/T方向与globalX/Y/Z必须实画/实核，不能按job名推定。

## 5. 如何诊断编译或运行失败

先分层：

- launcher找不到：不是Fortran定律问题。
- ifort/ifx找不到、链接器/SDK缺失：环境。
- unresolved externals/ABI：接口/编译精度/命名。
- runtime DLL加载失败：PATH与运行库。
- 最初返回properties已错误：接口/数据行/精度，不扫物理参数。
- initial tangent正常但localreturn非有限：定律算法/保护。
- patch干净但宏观缩步：边界/接触追踪/局部化/失稳/材料耦合需要隔离。

不要凭通用verify一个状态断言当前环境全部坏掉，也不要因一次正式作业失败而马上重装系统或改材料强度。优先保存实际编译stdout、env、source hash与最小复现，不在user文件里填未知constitutive值。
