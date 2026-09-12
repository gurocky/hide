# hcs08-dap — 在 VSCode 里源码级调试 MC9S08（SDCC + USBDM）

GNU 工具链没有 HCS08 目标，USBDM 的 GDB 服务器也不支持 HCS08，所以这里直接实现 VSCode 的
调试适配器协议（DAP）：适配器用 Python 写，通过 ctypes 调用本仓库编译出的
`usbdm/PackageFiles/lib/x86_64-apple-darwin/libusbdm.4.dylib` 控制 BDM，源码、行号、变量、类型
来自 SDCC `--debug` 生成的 `.cdb` 文件。不需要 GDB，也不需要再装任何包（Python 3 标准库）。

```
hcs08-dap/
├── hcs08-dap.py           VSCode 启动的适配器入口（stdio）
├── package.json           VSCode 扩展清单（扩展 ID readlbyte.hide-debug）：调试类型 hide-debug
├── hcs08dap/
│   ├── cdb.py             SDCC .cdb 解析：函数地址范围、行号↔地址、符号、结构体
│   ├── usbdm.py           libusbdm ctypes 绑定
│   ├── target.py          目标控制：UsbdmTarget（真硬件）、FakeTarget（协议测试）
│   └── adapter.py         DAP 服务器：断点、运行控制、栈、变量、求值、内存
└── tests/                 unittest：解析真实 cal32.cdb，用 FakeTarget 跑完整调试会话
```

## 安装

1. 固件用 `--debug` 构建（cal32-fw 的 Makefile 已加），得到 `build/cal32.s19` 和 `build/cal32.cdb`。
2. 在 hide 仓库根目录执行下面的命令，把本目录以符号链接方式装成 VSCode 扩展，然后重新加载窗口：

```
ln -s "$PWD/hcs08-dap" ~/.vscode/extensions/readlbyte.hide-debug-0.1.0
```

3. 在 cal32-fw 里按 F5，选 "CAL32 固件：烧录并调试 (USBDM)"（配置在 `cal32-fw/.vscode/launch.json`）。

## 能做什么

- 启动：可选先用 UsbdmFlashProgrammer 烧录 S19，然后复位并运行到 `main()`（`stopAtEntry` 则停在复位向量）；
  `attach` 不烧录，直接暂停正在运行的目标。
- 断点：C 源码行断点，最多 3 个硬件断点（BDC BKPT 寄存器 + DBG 模块比较器 A/B，tag 模式），
  超出的断点标为未验证；没有代码的行自动下移到最近的有代码行。
- 运行控制：继续、暂停、逐语句（遇 JSR/BSR 在返回地址放临时断点跨过）、逐过程进入、跳出
  （在栈里找返回地址）。单步用 BDC TRACE1，步进期间屏蔽中断（否则 TPM1 中断每 62.5 µs 就会进 ISR）。
- 调用栈：当前帧加上从栈里扫到的、前面紧跟 JSR/BSR 的返回地址（HCS08 没有帧指针，深层帧是启发式）。
- 变量：局部变量（SDCC s08 非重入模式下局部变量是静态地址）、寄存器 PC/SP/HX/A/CCR（带标志位）、
  全局变量、外设寄存器（`_PTAD` 等结构体，位域逐位展开）；结构体、数组、指针可展开；标量可修改；
  悬停 / 监视支持 `name`、`name.member`、`&name`、`*0x1234`。内存视图（readMemory）可用。
- 16 位值按 SDCC s08 的大端存储解释。

## 无硬件演示与测试

```
cd hcs08-dap
python3 -m unittest discover -s tests -v
```

launch.json 里的 "假目标演示 (无硬件)" 用 FakeTarget：它只在 `.cdb` 的行起始地址之间移动，可以
体验断点、单步、变量面板；值全是 0，除非在测试里预置。

## 硬件注意

- 接上 USBDM 后先用 `usbdm/PackageFiles/bin/x86_64-apple-darwin/UsbdmScript` 确认 `settarget HCS08`、
  `openbdm`、`connect`、`rb 0x1800 8` 正常。
- `vdd` 为 `off` 时目标板自供电（CAL32 板由 24 V 供电，探头不要供电）。
- 烧录会先关闭适配器对探头的占用，调用 UsbdmFlashProgrammer 完成后再重新连接。
- 处于 BDM 停机状态时 COP 看门狗停止计数，单步不会被看门狗复位；但停在断点时 RS485 通讯会超时。

## 已知限制

- 断点只有 3 个硬件槽；flash 里的代码不能插软件断点。
- 逐语句在循环行上可能要执行很多次 TRACE1（上限 5000 步），每步约 1–2 ms。
- 调用栈深层帧是启发式扫描，可能多报或少报。
- 没有反汇编视图。
