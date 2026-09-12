# HIDE 路线图

目标：S08 专用、三平台可用的 VSCode IDE。架构上把编译器后端、目标控制、调试信息解析做成独立模块，
但当前只实现 SDCC + USBDM + `.cdb` 一种组合。

## 阶段 0：调试适配器（已完成基础）

- [x] DAP 服务器：断点、单步、调用栈、变量、求值、内存视图（`hcs08-dap/`）
- [x] SDCC `.cdb` 解析：函数、行号、符号、结构体、位域
- [x] USBDM ctypes 绑定；FakeTarget 协议测试
- [ ] 在真实 MC9S08AW60 上验证烧录与调试全流程
- [ ] Windows / Linux 上验证库加载（`usbdm.dll` / `libusbdm.so.4`）

## 阶段 1：扩展主体

- [ ] 工程文件 `hide.json`：源文件、包含路径、宏、器件、链接参数
- [ ] 编译：调用 SDCC，生成 `.s19` + `.cdb` + `.map`，SDCC 诊断的 problem matcher
- [ ] 烧录：调用 `UsbdmFlashProgrammer`（擦除、编程、校验），状态栏按钮
- [ ] 器件选择器：读取 usbdm `DeviceData/hcs08_devices.xml`，自动填 `--code-loc` / `--data-loc` / `--xram-loc` / `--stack-loc`
- [ ] 调试配置生成：按工程输出路径生成 `hcs08-usbdm` 类型的 launch 配置
- [ ] 工程模板：启动文件、中断向量、寄存器头文件

## 阶段 2：器件支持与体验

- [ ] 寄存器头文件生成器（按数据手册，不分发 NXP 专有头文件），先覆盖常用型号
- [ ] 外设寄存器视图（基于头文件结构体）
- [ ] 反汇编视图
- [ ] 调试适配器改为 TypeScript + koffi 直接调库，去掉 Python 依赖（可选）

## 阶段 3：分发

- [ ] 三平台打包：随扩展附带 libusbdm 与 libusb，或检测已安装的 USBDM
- [ ] 发布到 VSCode Marketplace / Open VSX
