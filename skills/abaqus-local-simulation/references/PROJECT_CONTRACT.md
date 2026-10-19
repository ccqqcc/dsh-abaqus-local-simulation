# 仅适用于当前3DPC研究主线的合同

根目录：`D:/ai_abaqus/3DPC_IBM_C50_RESTART_20260921`。这不是通用Abaqus设置；其他试件/材料不得复制以下参数作为默认真值。

## 固定物理和来源

- 40×40×40mm；一个层间界面；不添加条间/竖界面。
- supplied concrete.lib C50CDP：E34500MPa，ν0.2，密度2.42e-9tonne/mm³；压缩硬化表最大32.4MPa，不因C50名字改成50。
- 源条目SHA256：`bdf879212cdc4959d0bdc125a63f105cb0f5b27253f5f40a1b774f9e52fbae7e`；Stage6/7完整common_material include为另一对象哈希`f817fc24e1bb9cd02f80bd8c29b57e59eca7e20d6d7592661597af18385bd8d7`。
- A8_L20约2mm fixed-facet必须继承实际facet/platform/topology，约20%投影水平平台不能在细化时消除。
- 压板μ0.20与层间μ分别设置；Stage6D的层间0.60不是当前试件实测参数。
- target40.5/47.1/41.2MPa；DXYtarget.14013，RZtarget1.01728；不能改target救模型。

## 指标

```text
f_MPa = abs(total axial RF_N)/1600
Hhigh=max(fX,fY); Hlow=min(fX,fY)
DXY=(Hhigh-Hlow)/Hhigh
RH=Hlow/Hhigh
RZ=fZ/Hlow
RZX=fZ/fX                 只有X弱时等于RZ
XY_RETENTION=DXY/.13567
Z_RECOVERY=(RZ-.64309)/(1.01728-.64309)
```

逐阶段peak gate、筛选阈值和自动衔接权限必须读用户最近合同，不能从本文件推断仍可自动提交18XZ/补Y/参数拟合。Skill/MCP并不授权恢复Stage7。

## 历史证据边界

- Stage6B BASE DXY.13567、RZ.64309：XY_MATCH_Z_FAIL。
- NoDamage RZ约.9964，XY保留约22.54%：机制对照，不是最终材料无损伤结论。
- 原M2/M1 ΔRH.05143fail；几何重构与mesh density混杂，不能解释为pure mesh sensitivity。
- Stage6B3固定facet X/Z变化约3.97%/3.65%，regional consistent PASS；没有Y，不能量化拆分旧ΔRH；不是正式mesh independence。
- Stage6C0 X及两次恢复未确认峰：NUMERICALLY_INCONCLUSIVE_X，禁止第三次自动恢复。
- Stage6C1有效Z/Y约.585232，目标包络必要条件不通过；不因X无峰继续无边界恢复。
- Stage6D层间μ.6的Z提高约34.8%，Standard X无峰；不把candidate最大值用于DXY/RZ。
- Stage7 Explicit E1/E2 X有效筛查峰和低KE证据；完整A–μ/XYZ仍未闭合，不能混用旧StandardY/Z。

上述是2026-10-04整理的历史事实，未来状态必须查实时证据。旧cache显示RUNNING不证明仍在运行。本包此次安装只新增工具和只读验证，不修改旧材料、INP、ODB、stage queue或结果。
