#!/usr/bin/env python
"""检查 PS_MIO51 的 BSDL 配置"""
import sys
sys.path.insert(0, 'pyjtagbs')

from jtagbs.pylinkbs import PyLinkRawBS as JTAGChain

# 初始化 JTAG 链
jc = JTAGChain(dll_path="tools/JLink_x64.dll")
jc.connect()

# 查找匹配的 BSDL 文件
num_devs = jc.get_number_devices()
print(f"检测到 {num_devs} 个设备")

for i in range(num_devs):
    devid = jc.get_devid(i)
    print(f"\n设备 {i}: IDCODE = 0x{devid:08X}")

# 假设使用第一个设备
dev_num = 0
bsdl_path = "E:/15_main_board_test_optimize/bscanner/pyjtagbs/bsdl_files/xilinx/zynq/xc7z020clg400.bsd"

try:
    jc.bsdl_attach(bsdl_path, dev_num, force=True)
    
    # 获取 PS_MIO51 的寄存器信息
    io_regs = jc.bsdl[dev_num].io_regs
    
    if 'PS_MIO51' in io_regs:
        print(f"\nPS_MIO51 寄存器信息:")
        for key, value in io_regs['PS_MIO51'].items():
            print(f"  {key}: {value}")
    else:
        print("\nPS_MIO51 不在 BSDL 文件中")
        
        # 列出所有包含 MIO51 的引脚
        print("\n包含 MIO51 的引脚:")
        for pin_name in sorted(io_regs.keys()):
            if 'MIO51' in pin_name:
                print(f"  {pin_name}: {io_regs[pin_name]}")
                
except Exception as e:
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()

jc.disconnect()
