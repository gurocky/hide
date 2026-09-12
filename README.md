# HIDE — HCS08 Integrated Development Environment for VSCode

把 VSCode 变成 Freescale/NXP **S08（HCS08 / HC08）** 单片机的集成开发环境：
用 **SDCC** 编译，用 **USBDM** 探头烧录和源码级调试，支持 **Windows、Linux、macOS**。

HIDE 是 [USBDM](https://github.com/podonoghue/usbdm-eclipse-makefiles-build) 的兄弟项目：
本人的 USBDM fork（分支 `macos`）提供三平台可用的宿主库和命令行工具，HIDE 在其上提供 IDE 层。
两个仓库并排放置即可工作：

```
<工作目录>/
├── HIDE/     本仓库
└── usbdm/    USBDM fork（macOS 用 macos 分支编译；Windows/Linux 可用官方安装包）
```

## 范围

- 只做 S08 系列。HCS12、RS08 没有开源 C 编译器，不在范围内。
- 编译器只支持 SDCC（`-ms08` / `-mhc08`），不做 CodeWarrior 工程兼容。
- 调试探头只支持 USBDM（JMxx / JS16 系列）。

## 组成

| 目录 | 内容 | 状态 |
|---|---|---|
| `hcs08-dap/` | VSCode 调试适配器（Python，DAP 协议）：通过 libusbdm 控制 BDM，读 SDCC 的 `.cdb` 做源码级调试 | 可用，已在 MC9S08AW60 上验证协议层；硬件调试待接板验证 |
| `extension/` | VSCode 扩展主体（TypeScript）：工程模型、编译、烧录、器件选择、外设寄存器视图 | 未开始 |
| `devices/` | 器件支持：从 USBDM 器件库生成的存储器映射和链接参数，以及寄存器头文件 | 未开始 |
| `docs/` | 设计说明和路线图 | [docs/ROADMAP.md](docs/ROADMAP.md) |

## 现在能做什么

用 `hcs08-dap/` 即可在 VSCode 里调试 SDCC 固件，步骤见 [hcs08-dap/README.md](hcs08-dap/README.md)。
要求：SDCC 4.x（`--debug` 构建），Python 3.10+，旁边有编译好的 usbdm 目录或设置 `USBDM_HOME`。

## 依赖与许可

- SDCC：GPL
- USBDM：GPL-2.0（本项目通过 ctypes 加载其动态库）
- HIDE：GPL-2.0，见 [LICENSE](LICENSE)
