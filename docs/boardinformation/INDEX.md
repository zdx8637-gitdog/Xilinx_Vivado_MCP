# AX7020 Board Information — Index

> 来源: ALINX 官方教程 PDF, 随开发板交付
> 适用板卡: ALINX AX7020 (XC7Z020-2CLG400I)
> 授权: 厂商随板提供, 仅限项目内部离线参考, 不对外分发

## 文件清单

| 文件 | 大小 | SHA256 | 内容 | Brick 关联 |
|------|------|--------|------|-----------|
| `cource_s1_ALINX_ZYNQ(AX7020)2023开发平台FPGA教程V1.01.pdf` | 23.3 MB | `561d1b36ba7d83147868093c4ab21c1d1d52b41a7747b3a777de6fac584162f4` | Zynq 基础、硬件架构、PS/PL 互联、BD 创建、AXI GPIO | B01, B03 |
| `course_s2_ALINX_ZYNQ(AX7010_AX7020)2023开发平台Vitis应用教程V1.01.pdf` | 49.0 MB | `47d221e66649e03d30e441cd30f12e2b1e21d0274e46fc85e61d618d797d0b13` | Vivado BD→XSA→Vitis BSP→UART→中断全流程 | B01, B06 |
| `course_s3_ALINX_ZYNQ开发平台HLS教程V1.03.pdf` | 4.4 MB | `a08b5b4f214b860822ae6b9a0a4cf6d6bbe7e355e040cf84a31f34cfbc0c8245` | HLS 高级综合教程 | 未来 |
| `course_s4_ALINX_ZYNQ开发平台Linux应用教程V1.01.pdf` | 13.1 MB | `b2011436601fe443688432534f61d47ebb03d11c0c7572ee5e2153d151b146ae` | Linux 应用开发 | 未来 |
| `course_s5_PYNQ开发教程.pdf` | 2.2 MB | `f3b1ffe23ef7bd419456b64a1a276d8d38951a5ae09c74c504dd5c24cc77c0df` | PYNQ Python+FPGA | 未来 |
| `course_s6_ZYNQ那些事儿-Linux驱动篇V1.01.pdf` | 6.6 MB | `7a9ab51d9afb6404491975e36ac0768092fd49dfad469deefbf7a9d4e4bcd132` | Linux 驱动开发 | 未来 |

## 角色说明

这些 PDF 是**厂商流程与参数说明参考**。S1 和 S2 描述了 PS7 537 参数的作用和 BD/Vitis 流程步骤，但**不包含可执行的 Tcl/XDC 源码**。
B03 固化 Board Profile 时，实际 preset Tcl 和 XDC 来源仍是厂商分发介质中的原始文件。

## 已知异常

- `cource_s1` 文件名中 "cource" 为厂商原文拼写 (应为 "course")。保留原始文件名不修改。
