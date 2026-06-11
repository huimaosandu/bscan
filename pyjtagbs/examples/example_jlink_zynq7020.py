'''
description :  基于 J-Link ARM 仿真器的 JTAG 边界扫描示例
              使用 PyLink 接口替代 Viveris JTAGCore DLL
author: fanc21
create date: 2025-0506
'''

import os
import sys
import json
import time
from grako.util import asjson

from jtagbs.pylinkbs.pylinkbs import PyLinkRawBS
from jtagbs.bsdlparser.bsdl import main
from jtagbs.bsdl import BSDLFile


class jtag_worker:  # 解析bsdl文件， 基于bsdl从jtag获取数据
    def __init__(self, path, dll_path=None):
        self.bsdl_path = path
        self.pin_map = {}
        self.pin_states = {}
        self.dev_num = ''

        # 使用 PyLink 接口（J-Link ARM 仿真器）
        self.jc = PyLinkRawBS(dll_path=dll_path)
        self.mode = 'sample'
        self.isPrintLog = True

    '''
    ============================bsdl文件处理部分=========================================
    '''

    def transfer_bsdl_to_json(self, isWrite2Local=True):  # 把芯片的bsdl描述文件中的pin信息提取出来，转为json格式
        ast = main(self.bsdl_path, '_bsdl_description_')

        ast_json = (json.dumps(asjson(ast), indent=4))
        bsdl_json_path = self.bsdl_path[:-3] + 'json'

        with open(bsdl_json_path, 'w', encoding='utf-8') as f:
            f.write(ast_json)

        ast = main(self.bsdl_path, '_bsdl_description_')
        ast_json = (json.dumps(asjson(ast), indent=4))

        if isWrite2Local:
            with open(bsdl_json_path, 'w', encoding='utf-8') as f:
                f.write(ast_json)

        return ast

    def list_all_pins(self):  # 列出所有的管脚名称和映射
        ast = self.transfer_bsdl_to_json(isWrite2Local=False)
        _pins_list = ast["device_package_pin_mappings"][0]["pin_map"]
        pins_list = []
        for pins in _pins_list:
            if ":" in pins:
                pins_list.append(pins)
            else:
                pins_list[-1] += pins

        for pins in pins_list:
            _p = pins.split(':')
            self.pin_map[_p[0]] = _p[1]

    '''
    ============================ JTAG (J-Link) =========================================
    '''

    def init_jc(self):  # 初始化jtag设备，获取设备号，给设备添加bsdl文件
        # 打开 J-Link 探头
        probes = self.jc.get_probe_names()
        if not probes:
            raise RuntimeError('未检测到J-Link探头，请检查USB连接和J-Link驱动')
        if self.isPrintLog:
            print(f'检测到J-Link探头: {probes}')

        self.jc.open_probe()
        if self.isPrintLog:
            print('打开J-Link探头成功')

        # 初始化扫描链
        self.jc.scan_init_chain(verbose=self.isPrintLog)

        numDevs = self.jc.get_number_devices()
        if self.isPrintLog:
            print(f'检测到{numDevs}个设备')

        for i in range(numDevs):
            # 从 BSDL 文件获取设备 ID
            bsdl_file = BSDLFile(self.bsdl_path)
            idmask, fileidcode = bsdl_file.get_idcode()
            bsdl_dev_id = str(hex(fileidcode))[2:]
            if self.isPrintLog:
                print(f'从bsdl文件中获取到的设备ID: {hex(fileidcode)}')

            dev_id = str(hex(self.jc.get_devid(i)))[2:]  # 获取扫描链设备ID
            if self.isPrintLog:
                print(f'扫描链设备{i} ID: {hex(self.jc.get_devid(i))}')

            if (fileidcode & idmask) == (self.jc.get_devid(i) & idmask):
                self.dev_num = i
                self.jc.bsdl_attach(self.bsdl_path, i)
                if self.isPrintLog:
                    print(f'给设备{i} (ID={hex(self.jc.get_devid(i))}) 加载bsdl文件')

    def get_properties_for_all_pins(self):  # 根据bsdl文件获取芯片所有的状态
        self.jc.scan()
        for k in self.pin_map.keys():
            try:
                pin_id = self.jc.get_pin_id(self.dev_num, k)
                self.pin_states[k] = [pin_id]
                pins_property = self.jc.get_pin_properties(self.dev_num, pin_id)
                self.pin_states[k].append(pins_property)
            except Exception as e:
                if self.isPrintLog:
                    print(k, e)

    def read_single_pin(self, pin, pin_type='input', isPinLocation=False):  # 读取单个管脚
        state = -1
        self.jc.scan()
        if not isPinLocation:
            state = self.jc.get_pin_state(self.dev_num, pin, pin_type)
        else:
            for k in self.pin_map.keys():  # 根据pin的位置来获取状态
                if self.pin_map[k].strip(',') == pin:
                    state = self.jc.get_pin_state(self.dev_num, k, pin_type)
        return state

    def set_single_pin(self, pin, state, isPinLocation=False):  # 设置单个管脚
        if self.mode == 'sample':
            self.jc.set_scan_mode(self.dev_num, "extest")
            self.mode = 'extest'

        if isPinLocation:
            for k in self.pin_map.keys():
                if self.pin_map[k].strip(',') == pin:
                    pin = k
        if isinstance(pin, str):
            pin = self.jc.get_pin_id(self.dev_num, pin)

        self.jc.set_pin_state(self.dev_num, pin, not state, 'oe')
        self.jc.set_pin_state(self.dev_num, pin, state, 'output')
        self.jc.scan()

    def extest_blink(self, pin_name, freq_hz=2, duration_s=3):
        """函数3: EXTEST模式驱动引脚闪烁

        Args:
            pin_name: 引脚名称，如 'PS_MIO51'
            freq_hz: 闪烁频率(Hz)
            duration_s: 持续时间(秒)
        """
        half_period_s = 1.0 / (2 * freq_hz)  # 半周期
        total_toggles = int(freq_hz * 2 * duration_s)  # 总翻转次数

        print(f'\n--- EXTEST闪烁: {pin_name} @ {freq_hz}Hz, 持续 {duration_s}s ---')

        # 切换到 EXTEST 模式
        self.jc.set_scan_mode(self.dev_num, 'extest')
        self.mode = 'extest'

        # 使能输出 (oe_disable=1, 所以设oe=True表示使能)
        self.jc.set_pin_state(self.dev_num, pin_name, True, 'oe')

        start_time = time.perf_counter()
        state = False  # 初始低电平

        for i in range(total_toggles):
            state = not state
            t0 = time.perf_counter()

            self.jc.set_pin_state(self.dev_num, pin_name, state, 'output')
            self.jc.scan()

            scan_ms = (time.perf_counter() - t0) * 1000
            elapsed_s = time.perf_counter() - start_time
            print(f'[{i+1:2d}] {pin_name} = {int(state)}  '
                  f'(扫描 {scan_ms:.1f}ms, 总耗时 {elapsed_s:.2f}s)')

            # 等待剩余时间
            sleep_time = half_period_s - (time.perf_counter() - t0)
            if sleep_time > 0:
                time.sleep(sleep_time)

        # 结束后恢复低电平并禁用输出
        self.jc.set_pin_state(self.dev_num, pin_name, False, 'output')
        self.jc.set_pin_state(self.dev_num, pin_name, False, 'oe')
        self.jc.scan()

        # 切回 SAMPLE 模式
        self.jc.set_scan_mode(self.dev_num, 'sample')
        self.mode = 'sample'

        total_ms = (time.perf_counter() - start_time) * 1000
        print(f'--- EXTEST闪烁结束: {total_toggles} 次翻转, 总耗时 {total_ms:.0f}ms ---')

    def set_i2c_pins(self, scl_pin, sda_pin, isPinLocation=False):
        raise NotImplementedError("I2C操作需要Viveris JTAGCore DLL支持，PyLink接口暂未实现")

    def i2c_write_read(self, address, address10bits, write_data, read_size):
        raise NotImplementedError("I2C操作需要Viveris JTAGCore DLL支持，PyLink接口暂未实现")

    def sample_read_all(self):
        """函数1: SAMPLE扫描 - 一次性读取所有引脚状态"""
        self.jc.set_scan_mode(self.dev_num, 'sample')
        self.jc.scan()

        results = {}
        # 直接使用 BSDL io_regs 的键（端口名）遍历
        for port_name in self.jc.bsdl[self.dev_num].io_regs.keys():
            try:
                state = self.jc.get_pin_state(self.dev_num, port_name, 'input')
                results[port_name] = state
            except Exception:
                pass  # 部分引脚可能没有 input cell

        return results

    def monitor_pin(self, pin_name, interval_ms=20, count=50):
        """函数2: 定时读取指定引脚状态并打印

        Args:
            pin_name: 引脚名称，如 'PS_MIO51'
            interval_ms: 采样间隔(毫秒)
            count: 采样次数
        """
        self.jc.set_scan_mode(self.dev_num, 'sample')
        interval_s = interval_ms / 1000.0

        print(f'\n--- 监控引脚: {pin_name} (间隔 {interval_ms}ms, 共 {count} 次) ---')
        prev_state = None
        changes = 0

        for i in range(count):
            t0 = time.perf_counter()
            self.jc.scan()
            state = self.jc.get_pin_state(self.dev_num, pin_name, 'input')

            changed = ''
            if prev_state is not None and state != prev_state:
                changed = ' <<< 变化!'
                changes += 1
            prev_state = state

            elapsed_ms = (time.perf_counter() - t0) * 1000
            print(f'[{i+1:3d}] {pin_name} = {state}  (扫描耗时 {elapsed_ms:.1f}ms){changed}')

            # 等待剩余时间
            sleep_time = interval_s - (time.perf_counter() - t0)
            if sleep_time > 0:
                time.sleep(sleep_time)

        print(f'--- 监控结束: 共 {count} 次采样, {changes} 次状态变化 ---')
        return changes


if __name__ == '__main__':
    # 文件路径（基于脚本位置计算，不依赖 cwd）
    script_dir = os.path.dirname(os.path.abspath(__file__))
    bsdl_path = os.path.join(script_dir, '..', 'bsdl_files', 'xilinx', 'zynq', 'xc7z020_clg400.bsd')
    bsdl_path = os.path.normpath(bsdl_path)

    # J-Link DLL 路径（优先使用项目 tools 目录中的 DLL）
    project_root = os.path.normpath(os.path.join(script_dir, '..', '..'))
    dll_candidates = [
        os.path.join(project_root, 'tools', 'JLink_x64.dll'),
        os.path.join(project_root, 'tools', 'JLinkARM.dll'),
    ]
    dll_path = None
    for p in dll_candidates:
        if os.path.exists(p):
            dll_path = p
            break

    if dll_path:
        print(f'J-Link DLL: {dll_path}')
    else:
        print('警告: 未在 tools 目录中找到 J-Link DLL，将尝试系统默认路径')

    if not os.path.exists(bsdl_path):
        print(f'BSDL文件不存在: {bsdl_path}')
        sys.exit(1)

    print(f'BSDL文件: {bsdl_path}')

    jw = jtag_worker(bsdl_path, dll_path=dll_path)
    # jw.transfer_bsdl_to_json()
    jw.init_jc()

    if jw.dev_num == '':
        print('未找到匹配BSDL文件的设备！')
        sys.exit(1)

    # jw.list_all_pins()  # 如需通过引脚位置号控制，先调用此方法
    # jw.get_properties_for_all_pins()

    print('-----------------')
    print(f'设备编号: {jw.dev_num}')

    # 打印 IR 配置信息
    print(f'\n=== IR 配置信息 ===')
    print(f'总 IR 长度: {jw.jc.total_IR_length} bits')
    print(f'每设备 IR 长度: {jw.jc._ir_lengths}')
    for dev_num, ops in jw.jc._ir_opcodes.items():
        print(f'设备{dev_num} BSDL IR: length={ops["ir_length"]}, '
              f'SAMPLE=0b{ops["sample"]:0{ops["ir_length"]}b}, '
              f'EXTEST=0b{ops["extest"]:0{ops["ir_length"]}b}, '
              f'BYPASS=0b{ops["bypass"]:0{ops["ir_length"]}b}')

    # 必须先构建引脚映射
    # jw.list_all_pins()  # 如需通过引脚位置号控制，先调用此方法

    '''
    函数1: SAMPLE扫描 - 一次性读取所有引脚状态
    '''
    print('\n===== 函数1: SAMPLE扫描 - 读取所有引脚状态 =====')
    all_states = jw.sample_read_all()
    print(f'共读取 {len(all_states)} 个引脚')
    # 打印部分结果（前20个）
    for i, (pin, state) in enumerate(all_states.items()):
        if i < 20:
            print(f'  {pin:20s} = {state}')
    if len(all_states) > 20:
        print(f'  ... (省略 {len(all_states) - 20} 个引脚)')

    # 单独查看 PS_MIO51 的 input 状态
    if 'PS_MIO51' in all_states:
        print(f'\n  PS_MIO51 input状态 = {all_states["PS_MIO51"]}')

    '''
    函数2: 定时20ms监控 PS_MIO51(B9) 的状态
    '''
    print('\n===== 函数2: 定时监控 PS_MIO51 (20ms间隔) =====')
    jw.monitor_pin('PS_MIO51', interval_ms=20, count=100)  # 20ms × 100次 = 2秒

    '''
    函数3: EXTEST模式 - PS_MIO51 以 2Hz 闪烁 3秒
    '''
    print('\n===== 函数3: EXTEST闪烁 PS_MIO51 (2Hz, 3秒) =====')
    jw.extest_blink('PS_MIO51', freq_hz=2, duration_s=3)
