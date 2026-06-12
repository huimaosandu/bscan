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

    def extest_blink_with_monitor(self, blink_pin_name, monitor_pin_name, freq_hz=2, duration_s=3):
        """函数3改进版: EXTEST模式驱动引脚闪烁，并在每次设置后立即读取监控引脚状态

        Args:
            blink_pin_name: 要闪烁的引脚名称，如 'PS_MIO51'
            monitor_pin_name: 要监控的引脚名称，如 'PS_MIO0'
            freq_hz: 闪烁频率(Hz)
            duration_s: 持续时间(秒)
        """
        half_period_s = 1.0 / (2 * freq_hz)  # 半周期
        total_toggles = int(freq_hz * 2 * duration_s)  # 总翻转次数

        print(f'\n--- EXTEST闪烁 {blink_pin_name} 并监控 {monitor_pin_name}: @ {freq_hz}Hz, 持续 {duration_s}s ---')

        # 切换到 EXTEST 模式
        self.jc.set_scan_mode(self.dev_num, 'extest')
        self.mode = 'extest'

        # 使能闪烁引脚的输出
        self.jc.set_pin_state(self.dev_num, blink_pin_name, True, 'oe')
        
        # 获取监控引脚的详细信息
        pin_id = self.jc.get_pin_id(self.dev_num, monitor_pin_name)
        io_reg = self.jc.bsdl[self.dev_num].io_regs[pin_id]
        print(f'{monitor_pin_name} IO Register: {io_reg}')
        
        # 读取初始状态
        initial_output = self.jc.get_pin_state(self.dev_num, monitor_pin_name, 'output')
        initial_oe = self.jc.get_pin_state(self.dev_num, monitor_pin_name, 'oe')
        print(f'{monitor_pin_name} 初始状态 - output: {initial_output}, oe: {initial_oe}\n')

        start_time = time.perf_counter()
        state = False  # 初始低电平

        for i in range(total_toggles):
            state = not state
            t0 = time.perf_counter()

            # 只修改目标引脚的状态
            self.jc.set_pin_state(self.dev_num, blink_pin_name, state, 'output')
            
            # 执行一次完整的扫描
            self.jc.scan()
            
            # 立即读取监控引脚的状态
            monitor_output = self.jc.get_pin_state(self.dev_num, monitor_pin_name, 'output')
            monitor_oe = self.jc.get_pin_state(self.dev_num, monitor_pin_name, 'oe')

            scan_ms = (time.perf_counter() - t0) * 1000
            elapsed_s = time.perf_counter() - start_time
            print(f'[{i+1:2d}] {blink_pin_name} = {int(state)}  '
                  f'| {monitor_pin_name}_out={monitor_output}, {monitor_pin_name}_oe={monitor_oe}  '
                  f'(扫描 {scan_ms:.1f}ms, 总耗时 {elapsed_s:.2f}s)')

            # 等待剩余时间
            sleep_time = half_period_s - (time.perf_counter() - t0)
            if sleep_time > 0:
                time.sleep(sleep_time)

        # 结束后恢复初始状态并禁用输出
        self.jc.set_pin_state(self.dev_num, blink_pin_name, False, 'output')
        self.jc.set_pin_state(self.dev_num, blink_pin_name, False, 'oe')
        self.jc.scan()

        # 切回 SAMPLE 模式
        self.jc.set_scan_mode(self.dev_num, 'sample')
        self.mode = 'sample'

        total_ms = (time.perf_counter() - start_time) * 1000
        print(f'\n--- EXTEST闪烁结束: {total_toggles} 次翻转, 总耗时 {total_ms:.0f}ms ---')

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

    def monitor_pin_during_extest(self, monitor_pin_name, blink_pin_name, freq_hz=2, duration_s=3):
        """函数4: 在EXTEST闪烁期间监控另一个引脚的状态（验证其不受影响）

        Args:
            monitor_pin_name: 要监控的引脚名称，如 'PS_MIO0'
            blink_pin_name: 正在闪烁的引脚名称，如 'PS_MIO51'
            freq_hz: 闪烁频率(Hz)
            duration_s: 持续时间(秒)
        """
        half_period_s = 1.0 / (2 * freq_hz)
        total_toggles = int(freq_hz * 2 * duration_s)

        print(f'\n--- 监控 {monitor_pin_name} 当 {blink_pin_name} 闪烁时 ---')
        print(f'    闪烁参数: {freq_hz}Hz, 持续 {duration_s}s')
        print(f'    预期: {monitor_pin_name} 状态应保持不变\n')

        # 切换到 EXTEST 模式
        self.jc.set_scan_mode(self.dev_num, 'extest')
        self.mode = 'extest'

        # 使能闪烁引脚的输出
        self.jc.set_pin_state(self.dev_num, blink_pin_name, True, 'oe')
        
        # 读取监控引脚的初始状态（output 和 oe）
        initial_output_state = self.jc.get_pin_state(self.dev_num, monitor_pin_name, 'output')
        initial_oe_state = self.jc.get_pin_state(self.dev_num, monitor_pin_name, 'oe')
        print(f'{monitor_pin_name} 初始 output 状态: {initial_output_state}')
        print(f'{monitor_pin_name} 初始 oe 状态: {initial_oe_state}')
        
        # 获取监控引脚的详细信息
        pin_id = self.jc.get_pin_id(self.dev_num, monitor_pin_name)
        pin_props = self.jc.get_pin_properties(self.dev_num, pin_id)
        io_reg = self.jc.bsdl[self.dev_num].io_regs[pin_id]
        print(f'{monitor_pin_name} 详细信息:')
        print(f'  Pin ID: {pin_id}')
        print(f'  Properties: {pin_props}')
        print(f'  IO Register: {io_reg}')

        state_changes = []
        oe_changes = []
        start_time = time.perf_counter()
        blink_state = False

        for i in range(total_toggles):
            blink_state = not blink_state
            t0 = time.perf_counter()

            # 只设置闪烁引脚的状态
            self.jc.set_pin_state(self.dev_num, blink_pin_name, blink_state, 'output')
            
            # 执行扫描
            self.jc.scan()

            # 读取监控引脚的 output 和 oe 状态
            monitor_output = self.jc.get_pin_state(self.dev_num, monitor_pin_name, 'output')
            monitor_oe = self.jc.get_pin_state(self.dev_num, monitor_pin_name, 'oe')
            state_changes.append(monitor_output)
            oe_changes.append(monitor_oe)

            elapsed_s = time.perf_counter() - start_time
            
            # 只在状态变化时打印
            output_changed = len(state_changes) > 1 and state_changes[-1] != state_changes[-2]
            oe_changed = len(oe_changes) > 1 and oe_changes[-1] != oe_changes[-2]
            
            if i == 0 or output_changed or oe_changed:
                change_flags = []
                if output_changed:
                    change_flags.append("output变化!")
                if oe_changed:
                    change_flags.append("oe变化!")
                flag_str = f' <<< {", ".join(change_flags)}' if change_flags else ""
                
                print(f'[{i+1:3d}] {blink_pin_name}={int(blink_state)}, '
                      f'{monitor_pin_name}_out={monitor_output}, {monitor_pin_name}_oe={monitor_oe}'
                      f'{flag_str} (t={elapsed_s:.2f}s)')

            # 等待剩余时间
            sleep_time = half_period_s - (time.perf_counter() - t0)
            if sleep_time > 0:
                time.sleep(sleep_time)

        # 恢复状态
        self.jc.set_pin_state(self.dev_num, blink_pin_name, False, 'output')
        self.jc.set_pin_state(self.dev_num, blink_pin_name, False, 'oe')
        self.jc.scan()

        # 切回 SAMPLE 模式
        self.jc.set_scan_mode(self.dev_num, 'sample')
        self.mode = 'sample'

        # 统计结果
        unique_output_states = set(state_changes)
        unique_oe_states = set(oe_changes)
        output_changes_count = sum(1 for i in range(1, len(state_changes)) if state_changes[i] != state_changes[i-1])
        oe_changes_count = sum(1 for i in range(1, len(oe_changes)) if oe_changes[i] != oe_changes[i-1])
        
        print(f'\n--- 监控结束 ---')
        print(f'{monitor_pin_name} 状态统计:')
        print(f'  Output 状态:')
        print(f'    初始值: {initial_output_state}')
        print(f'    最终值: {state_changes[-1] if state_changes else "N/A"}')
        print(f'    唯一值: {unique_output_states}')
        print(f'    变化次数: {output_changes_count}/{len(state_changes)-1 if len(state_changes) > 1 else 0}')
        print(f'  OE 状态:')
        print(f'    初始值: {initial_oe_state}')
        print(f'    最终值: {oe_changes[-1] if oe_changes else "N/A"}')
        print(f'    唯一值: {unique_oe_states}')
        print(f'    变化次数: {oe_changes_count}/{len(oe_changes)-1 if len(oe_changes) > 1 else 0}')
        
        if len(unique_output_states) == 1 and len(unique_oe_states) == 1:
            print(f'  ✓ 正常: {monitor_pin_name} 的 output 和 oe 状态均未受影响')
        else:
            print(f'  ✗ 异常: {monitor_pin_name} 的状态发生变化！')
            if len(unique_output_states) > 1:
                print(f'    - output 状态发生了变化')
            if len(unique_oe_states) > 1:
                print(f'    - oe 状态发生了变化')
        
        return output_changes_count + oe_changes_count

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

    def test_extest_single_pin_set(self, target_pin='PS_MIO51', monitor_pins=None):
        """函数6: 测试 EXTEST 模式下设置单个引脚时是否影响其他引脚
        
        Args:
            target_pin: 要设置的引脚名称
            monitor_pins: 要监控的其他引脚列表，None则监控几个关键引脚
        """
        if monitor_pins is None:
            monitor_pins = ['PS_MIO0', 'PS_MIO1', 'PS_MIO12', 'PS_MIO13']
        
        print(f'\n===== 函数6: EXTEST 单引脚设置测试 =====')
        print(f'目标引脚: {target_pin}')
        print(f'监控引脚: {monitor_pins}')
        print(f'预期: 只有 {target_pin} 的状态改变，其他引脚保持不变\n')
        
        # 切换到 EXTEST 模式
        self.jc.set_scan_mode(self.dev_num, 'extest')
        self.mode = 'extest'
        
        # 先将所有引脚设为高阻态 (Z)
        print('--- 步骤1: 将所有引脚设为高阻态 (Z) ---')
        all_pins = [target_pin] + monitor_pins
        for pin_name in all_pins:
            try:
                reg = self.jc.bsdl[self.dev_num].io_regs.get(pin_name, {})
                if 'oe' in reg:
                    self.jc.set_pin_state(self.dev_num, pin_name, False, 'oe')
                    print(f'  {pin_name}: 设置为 Z')
            except Exception as e:
                print(f'  {pin_name}: 设置失败 - {e}')
        
        # 执行一次扫描
        self.jc.scan()
        
        # 读取初始状态
        print('\n--- 步骤2: 读取初始状态 ---')
        initial_states = {}
        for pin in all_pins:
            try:
                state = self.jc.get_pin_state(self.dev_num, pin, 'output')
                oe_state = self.jc.get_pin_state(self.dev_num, pin, 'oe')
                initial_states[pin] = {'output': state, 'oe': oe_state}
            except Exception as e:
                print(f'警告: 无法读取 {pin}: {e}')
                initial_states[pin] = {'output': -1, 'oe': -1}
        
        print('初始状态:')
        for pin, states in initial_states.items():
            print(f'  {pin:15s}: output={states["output"]}, oe={states["oe"]}')
        
        # 记录 _output_bits 的快照
        print('\n--- 步骤3: 记录 _output_bits 快照 ---')
        before_output_bits = None
        if self.dev_num in self.jc._output_bits:
            before_output_bits = self.jc._output_bits[self.dev_num][:]
            print(f'  _output_bits 长度: {len(before_output_bits)}')
            num_ones = sum(1 for b in before_output_bits if b == 1)
            print(f'  1 的数量: {num_ones}/{len(before_output_bits)}')
        
        # 设置目标引脚为 1
        print(f'\n--- 步骤4: 设置 {target_pin} -> 1 ---')
        try:
            reg = self.jc.bsdl[self.dev_num].io_regs.get(target_pin, {})
            if 'output' in reg and 'oe' in reg:
                self.jc.set_pin_state(self.dev_num, target_pin, True, 'oe')
                self.jc.set_pin_state(self.dev_num, target_pin, True, 'output')
                print(f'  ✓ 已设置 {target_pin} 的 output=1, oe=enable')
        except Exception as e:
            print(f'  ✗ 设置失败: {e}')
            return False
        
        # 检查 _output_bits 的变化
        print('\n--- 步骤5: 检查 _output_bits 变化 ---')
        if before_output_bits and self.dev_num in self.jc._output_bits:
            after_output_bits = self.jc._output_bits[self.dev_num]
            changed_indices = [i for i in range(min(len(before_output_bits), len(after_output_bits))) 
                              if before_output_bits[i] != after_output_bits[i]]
            
            print(f'  变化的位数量: {len(changed_indices)}')
            if changed_indices:
                print(f'  变化的位索引: {changed_indices[:10]}...' if len(changed_indices) > 10 else f'  变化的位索引: {changed_indices}')
                # 检查是否只有目标引脚的 output 和 oe 位变化
                target_output_idx = reg['output']
                target_oe_idx = reg['oe']
                expected_changes = {target_output_idx, target_oe_idx}
                actual_changes = set(changed_indices)
                
                if actual_changes == expected_changes:
                    print(f'  ✓ 正确: 只有 {target_pin} 的 output[{target_output_idx}] 和 oe[{target_oe_idx}] 位发生变化')
                elif actual_changes.issubset(expected_changes):
                    print(f'  ⚠️ 部分正确: 只有部分位发生变化 {actual_changes}')
                else:
                    unexpected = actual_changes - expected_changes
                    print(f'  ✗ 错误: 有意外的位发生变化: {unexpected}')
                    return False
        
        # 执行 scan()
        print('\n--- 步骤6: 执行 scan() ---')
        self.jc.scan()
        print(f'  ✓ scan() 完成')
        
        # 读取最终状态
        print('\n--- 步骤7: 读取最终状态 ---')
        final_states = {}
        for pin in all_pins:
            try:
                state = self.jc.get_pin_state(self.dev_num, pin, 'output')
                oe_state = self.jc.get_pin_state(self.dev_num, pin, 'oe')
                final_states[pin] = {'output': state, 'oe': oe_state}
            except Exception as e:
                print(f'警告: 无法读取 {pin}: {e}')
                final_states[pin] = {'output': -1, 'oe': -1}
        
        print('最终状态:')
        for pin, states in final_states.items():
            init = initial_states[pin]
            print(f'  {pin:15s}: output={init["output"]}->{states["output"]}, oe={init["oe"]}->{states["oe"]}')
        
        # 检查结果
        print('\n--- 测试结果 ---')
        success = True
        
        # 检查目标引脚是否正确设置
        if final_states[target_pin]['output'] != 1:
            print(f'  ✗ {target_pin} 的 output 未正确设置为 1')
            success = False
        else:
            print(f'  ✓ {target_pin} 的 output 正确设置为 1')
        
        # 检查其他引脚是否保持不变
        for pin in monitor_pins:
            if pin in final_states and pin in initial_states:
                if (final_states[pin]['output'] != initial_states[pin]['output'] or
                    final_states[pin]['oe'] != initial_states[pin]['oe']):
                    print(f'  ✗ {pin} 的状态发生了意外变化!')
                    print(f'      output: {initial_states[pin]["output"]} -> {final_states[pin]["output"]}')
                    print(f'      oe:     {initial_states[pin]["oe"]} -> {final_states[pin]["oe"]}')
                    success = False
                else:
                    print(f'  ✓ {pin} 的状态保持不变')
        
        if success:
            print(f'\n✓ 测试通过: 只有 {target_pin} 的状态改变，其他引脚不受影响')
        else:
            print(f'\n✗ 测试失败: 设置 {target_pin} 影响了其他引脚')
        
        # 切回 SAMPLE 模式
        self.jc.set_scan_mode(self.dev_num, 'sample')
        self.mode = 'sample'
        
        return success

    def test_extest_stability(self, monitor_pins=None, duration_s=5, interval_ms=100):
        """函数5: 测试 EXTEST 模式下引脚状态的稳定性（不主动设置任何引脚）"""
        if monitor_pins is None:
            monitor_pins = ['PS_MIO51', 'PS_MIO0', 'PS_MIO1']
        
        print(f'\n===== 函数5: EXTEST 模式稳定性测试 =====')
        print(f'监控引脚: {monitor_pins}')
        print(f'持续时间: {duration_s}s, 采样间隔: {interval_ms}ms')
        
        # 切换到 EXTEST 模式
        self.jc.set_scan_mode(self.dev_num, 'extest')
        self.mode = 'extest'
        
        # 将所有引脚设为高阻态 (Z)
        for pin_name in monitor_pins:
            try:
                reg = self.jc.bsdl[self.dev_num].io_regs.get(pin_name, {})
                if 'oe' in reg:
                    self.jc.set_pin_state(self.dev_num, pin_name, False, 'oe')
            except Exception:
                pass
        
        # 执行一次扫描
        self.jc.scan()
        
        # 读取初始状态
        initial_states = {}
        for pin in monitor_pins:
            try:
                state = self.jc.get_pin_state(self.dev_num, pin, 'output')
                initial_states[pin] = state
            except Exception:
                initial_states[pin] = -1
        
        print('初始状态:')
        for pin, state in initial_states.items():
            print(f'  {pin:15s} = {state}')
        
        # 开始监控
        total_samples = int(duration_s * 1000 / interval_ms)
        interval_s = interval_ms / 1000.0
        changes_detected = []
        
        for i in range(total_samples):
            t0 = time.perf_counter()
            self.jc.scan()
            
            for pin in monitor_pins:
                try:
                    state = self.jc.get_pin_state(self.dev_num, pin, 'output')
                    if state != initial_states[pin]:
                        changes_detected.append((i+1, pin, initial_states[pin], state))
                        initial_states[pin] = state
                except Exception:
                    pass
            
            sleep_time = interval_s - (time.perf_counter() - t0)
            if sleep_time > 0:
                time.sleep(sleep_time)
        
        # 统计结果
        print(f'\n--- 测试结果 ---')
        if len(changes_detected) == 0:
            print(f'✓ 测试通过: 所有引脚状态稳定')
            result = True
        else:
            print(f'✗ 测试失败: 检测到 {len(changes_detected)} 次变化')
            for sample, pin, old_val, new_val in changes_detected[:5]:
                print(f'  样本 {sample}: {pin} {old_val} -> {new_val}')
            result = False
        
        # 切回 SAMPLE 模式
        self.jc.set_scan_mode(self.dev_num, 'sample')
        self.mode = 'sample'
        
        return result
        
        Args:
            target_pin: 要设置的引脚名称
            monitor_pins: 要监控的其他引脚列表，None则监控几个关键引脚
        """
        if monitor_pins is None:
            monitor_pins = ['PS_MIO0', 'PS_MIO1', 'PS_MIO12', 'PS_MIO13']
        
        print(f'\n===== 函数6: EXTEST 单引脚设置测试 =====')
        print(f'目标引脚: {target_pin}')
        print(f'监控引脚: {monitor_pins}')
        print(f'预期: 只有 {target_pin} 的状态改变，其他引脚保持不变\n')
        
        # 切换到 EXTEST 模式
        self.jc.set_scan_mode(self.dev_num, 'extest')
        self.mode = 'extest'
        
        # 先将所有引脚设为高阻态 (Z)
        print('--- 步骤1: 将所有引脚设为高阻态 (Z) ---')
        all_pins = [target_pin] + monitor_pins
        for pin_name in all_pins:
            try:
                reg = self.jc.bsdl[self.dev_num].io_regs.get(pin_name, {})
                if 'oe' in reg:
                    self.jc.set_pin_state(self.dev_num, pin_name, False, 'oe')
                    print(f'  {pin_name}: 设置为 Z')
            except Exception as e:
                print(f'  {pin_name}: 设置失败 - {e}')
        
        # 执行一次扫描
        self.jc.scan()
        
        # 读取初始状态
        print('\n--- 步骤2: 读取初始状态 ---')
        initial_states = {}
        for pin in all_pins:
            try:
                state = self.jc.get_pin_state(self.dev_num, pin, 'output')
                oe_state = self.jc.get_pin_state(self.dev_num, pin, 'oe')
                initial_states[pin] = {'output': state, 'oe': oe_state}
            except Exception as e:
                print(f'警告: 无法读取 {pin}: {e}')
                initial_states[pin] = {'output': -1, 'oe': -1}
        
        print('初始状态:')
        for pin, states in initial_states.items():
            print(f'  {pin:15s}: output={states["output"]}, oe={states["oe"]}')
        
        # 记录 _output_bits 的快照
        print('\n--- 步骤3: 记录 _output_bits 快照 ---')
        before_output_bits = None
        if self.dev_num in self.jc._output_bits:
            before_output_bits = self.jc._output_bits[self.dev_num][:]
            print(f'  _output_bits 长度: {len(before_output_bits)}')
            num_ones = sum(1 for b in before_output_bits if b == 1)
            print(f'  1 的数量: {num_ones}/{len(before_output_bits)}')
        
        # 设置目标引脚为 1
        print(f'\n--- 步骤4: 设置 {target_pin} -> 1 ---')
        try:
            reg = self.jc.bsdl[self.dev_num].io_regs.get(target_pin, {})
            if 'output' in reg and 'oe' in reg:
                self.jc.set_pin_state(self.dev_num, target_pin, True, 'oe')
                self.jc.set_pin_state(self.dev_num, target_pin, True, 'output')
                print(f'  ✓ 已设置 {target_pin} 的 output=1, oe=enable')
        except Exception as e:
            print(f'  ✗ 设置失败: {e}')
            return False
        
        # 检查 _output_bits 的变化
        print('\n--- 步骤5: 检查 _output_bits 变化 ---')
        if before_output_bits and self.dev_num in self.jc._output_bits:
            after_output_bits = self.jc._output_bits[self.dev_num]
            changed_indices = [i for i in range(min(len(before_output_bits), len(after_output_bits))) 
                              if before_output_bits[i] != after_output_bits[i]]
            
            print(f'  变化的位数量: {len(changed_indices)}')
            if changed_indices:
                print(f'  变化的位索引: {changed_indices[:10]}...' if len(changed_indices) > 10 else f'  变化的位索引: {changed_indices}')
                # 检查是否只有目标引脚的 output 和 oe 位变化
                target_output_idx = reg['output']
                target_oe_idx = reg['oe']
                expected_changes = {target_output_idx, target_oe_idx}
                actual_changes = set(changed_indices)
                
                if actual_changes == expected_changes:
                    print(f'  ✓ 正确: 只有 {target_pin} 的 output[{target_output_idx}] 和 oe[{target_oe_idx}] 位发生变化')
                elif actual_changes.issubset(expected_changes):
                    print(f'  ⚠️ 部分正确: 只有部分位发生变化 {actual_changes}')
                else:
                    unexpected = actual_changes - expected_changes
                    print(f'  ✗ 错误: 有意外的位发生变化: {unexpected}')
                    return False
        
        # 执行 scan()
        print('\n--- 步骤6: 执行 scan() ---')
        self.jc.scan()
        print(f'  ✓ scan() 完成')
        
        # 读取最终状态
        print('\n--- 步骤7: 读取最终状态 ---')
        final_states = {}
        for pin in all_pins:
            try:
                state = self.jc.get_pin_state(self.dev_num, pin, 'output')
                oe_state = self.jc.get_pin_state(self.dev_num, pin, 'oe')
                final_states[pin] = {'output': state, 'oe': oe_state}
            except Exception as e:
                print(f'警告: 无法读取 {pin}: {e}')
                final_states[pin] = {'output': -1, 'oe': -1}
        
        print('最终状态:')
        for pin, states in final_states.items():
            init = initial_states[pin]
            print(f'  {pin:15s}: output={init["output"]}->{states["output"]}, oe={init["oe"]}->{states["oe"]}')
        
        # 检查结果
        print('\n--- 测试结果 ---')
        success = True
        
        # 检查目标引脚是否正确设置
        if final_states[target_pin]['output'] != 1:
            print(f'  ✗ {target_pin} 的 output 未正确设置为 1')
            success = False
        else:
            print(f'  ✓ {target_pin} 的 output 正确设置为 1')
        
        # 检查其他引脚是否保持不变
        for pin in monitor_pins:
            if pin in final_states and pin in initial_states:
                if (final_states[pin]['output'] != initial_states[pin]['output'] or
                    final_states[pin]['oe'] != initial_states[pin]['oe']):
                    print(f'  ✗ {pin} 的状态发生了意外变化!')
                    print(f'      output: {initial_states[pin]["output"]} -> {final_states[pin]["output"]}')
                    print(f'      oe:     {initial_states[pin]["oe"]} -> {final_states[pin]["oe"]}')
                    success = False
                else:
                    print(f'  ✓ {pin} 的状态保持不变')
        
        if success:
            print(f'\n✓ 测试通过: 只有 {target_pin} 的状态改变，其他引脚不受影响')
        else:
            print(f'\n✗ 测试失败: 设置 {target_pin} 影响了其他引脚')
        
        # 切回 SAMPLE 模式
        self.jc.set_scan_mode(self.dev_num, 'sample')
        self.mode = 'sample'
        
        return success
        """函数5: 测试 EXTEST 模式下引脚状态的稳定性（不主动设置任何引脚）
            
        验证进入 EXTEST 模式后，如果不主动设置引脚状态，引脚应该保持稳定
            
        Args:
            monitor_pins: 要监控的引脚列表，如 ['PS_MIO51', 'PS_MIO0']，None则监控所有引脚
            duration_s: 监控持续时间(秒)
            interval_ms: 采样间隔(毫秒)
        """
        if monitor_pins is None:
            # 默认监控几个关键引脚
            monitor_pins = ['PS_MIO51', 'PS_MIO0', 'PS_MIO1', 'PS_MIO12', 'PS_MIO13']
            
        total_samples = int(duration_s * 1000 / interval_ms)
        interval_s = interval_ms / 1000.0
            
        print(f'\n===== 函数5: EXTEST 模式稳定性测试 =====')
        print(f'监控引脚: {monitor_pins}')
        print(f'持续时间: {duration_s}s, 采样间隔: {interval_ms}ms, 总采样次数: {total_samples}')
        print(f'预期: 所有引脚状态应保持不变（除非外部电路改变）\n')
            
        # 切换到 EXTEST 模式
        self.jc.set_scan_mode(self.dev_num, 'extest')
        self.mode = 'extest'
            
        # ⚠️ 重要：先将所有引脚设为高阻态 (Z)
        print('--- 步骤1: 将所有引脚设为高阻态 (Z) ---')
        for pin_name in monitor_pins:
            try:
                reg = self.jc.bsdl[self.dev_num].io_regs.get(pin_name, {})
                if 'oe' in reg:
                    # 禁用输出使能 -> 高阻
                    self.jc.set_pin_state(self.dev_num, pin_name, False, 'oe')
                    print(f'  {pin_name}: 设置为 Z (高阻)')
            except Exception as e:
                print(f'  {pin_name}: 设置失败 - {e}')
            
        # 执行一次扫描，应用高阻态设置
        print('\n--- 步骤2: 执行 scan() 应用高阻态设置 ---')
        self.jc.scan()
            
        # 读取并记录 _output_bits 的初始状态
        print('\n--- 步骤3: 记录 _output_bits 初始状态 ---')
        output_bits_snapshot = {}
        if self.dev_num in self.jc._output_bits:
            output_bits_snapshot = self.jc._output_bits[self.dev_num][:]  # 复制一份
            print(f'  _output_bits 长度: {len(output_bits_snapshot)}')
            # 显示前 20 位的值
            sample_bits = output_bits_snapshot[:min(20, len(output_bits_snapshot))]
            print(f'  前 20 位: {sample_bits}')
        else:
            print(f'  警告: 设备 {self.dev_num} 没有 _output_bits')
            
        # 读取初始状态
        print('\n--- 步骤4: 读取初始引脚状态 ---')
        initial_states = {}
        for pin in monitor_pins:
            try:
                state = self.jc.get_pin_state(self.dev_num, pin, 'output')
                oe_state = self.jc.get_pin_state(self.dev_num, pin, 'oe')
                initial_states[pin] = {'output': state, 'oe': oe_state}
            except Exception as e:
                print(f'警告: 无法读取 {pin}: {e}')
                initial_states[pin] = {'output': -1, 'oe': -1}
            
        print('初始状态:')
        for pin, states in initial_states.items():
            print(f'  {pin:15s}: output={states["output"]}, oe={states["oe"]}')
            
        # 开始监控
        print(f'\n--- 步骤5: 开始监控 ({duration_s}s, 每 {interval_ms}ms 采样一次) ---')
        state_history = {pin: [] for pin in monitor_pins}
        start_time = time.perf_counter()
            
        changes_detected = []
            
        for i in range(total_samples):
            t0 = time.perf_counter()
                
            # 只执行扫描，不设置任何引脚状态
            self.jc.scan()
                
            # 检查 _output_bits 是否发生变化
            if self.dev_num in self.jc._output_bits and output_bits_snapshot:
                current_output_bits = self.jc._output_bits[self.dev_num]
                if current_output_bits != output_bits_snapshot:
                    # 找到变化的位
                    changed_indices = [idx for idx in range(len(current_output_bits)) 
                                      if idx < len(output_bits_snapshot) and current_output_bits[idx] != output_bits_snapshot[idx]]
                    if changed_indices:
                        print(f'  ⚠️ 样本 {i+1}: _output_bits 发生变化! 变化的位索引: {changed_indices[:10]}...')
                        # 更新快照
                        output_bits_snapshot = current_output_bits[:]
                
            # 读取所有监控引脚的状态
            current_states = {}
            for pin in monitor_pins:
                try:
                    state = self.jc.get_pin_state(self.dev_num, pin, 'output')
                    oe_state = self.jc.get_pin_state(self.dev_num, pin, 'oe')
                    current_states[pin] = {'output': state, 'oe': oe_state}
                    state_history[pin].append(current_states[pin])
                except Exception:
                    current_states[pin] = {'output': -1, 'oe': -1}
                    state_history[pin].append(current_states[pin])
                
            elapsed_s = time.perf_counter() - start_time
                
            # 检查是否有变化
            has_change = any(
                current_states[pin]['output'] != initial_states[pin]['output'] or
                current_states[pin]['oe'] != initial_states[pin]['oe']
                for pin in monitor_pins 
                if initial_states[pin]['output'] != -1 and current_states[pin]['output'] != -1
            )
                
            if i == 0 or has_change:
                change_flags = []
                for pin in monitor_pins:
                    init = initial_states[pin]
                    curr = current_states[pin]
                    if init['output'] != -1 and curr['output'] != -1:
                        if curr['output'] != init['output'] or curr['oe'] != init['oe']:
                            change_flags.append(f'{pin}:out({init["output"]}->{curr["output"]}),oe({init["oe"]}->{curr["oe"]})')
                    
                flag_str = f' <<< 变化: {", ".join(change_flags)}' if change_flags else ''
                if change_flags:
                    changes_detected.extend(change_flags)
                    print(f'[{i+1:3d}] t={elapsed_s:.2f}s{flag_str}')
                
            # 等待剩余时间
            sleep_time = interval_s - (time.perf_counter() - t0)
            if sleep_time > 0:
                time.sleep(sleep_time)
            
        # 统计结果
        print(f'\n--- 测试结果统计 ---')
        all_stable = True
        for pin in monitor_pins:
            history = [s for s in state_history[pin] if s['output'] != -1]
            if len(history) == 0:
                continue
                
            unique_output = set(h['output'] for h in history)
            unique_oe = set(h['oe'] for h in history)
                
            output_changes = sum(1 for i in range(1, len(history)) if history[i]['output'] != history[i-1]['output'])
            oe_changes = sum(1 for i in range(1, len(history)) if history[i]['oe'] != history[i-1]['oe'])
                
            print(f'{pin:15s}:')
            print(f'  初始值: output={initial_states[pin]["output"]}, oe={initial_states[pin]["oe"]}')
            print(f'  最终值: output={history[-1]["output"]}, oe={history[-1]["oe"]}')
            print(f'  output 唯一状态: {unique_output}, 变化次数: {output_changes}/{len(history)-1}')
            print(f'  oe     唯一状态: {unique_oe}, 变化次数: {oe_changes}/{len(history)-1}')
                
            if len(unique_output) > 1 or len(unique_oe) > 1:
                print(f'  ✗ 不稳定: 状态发生了变化')
                all_stable = False
            else:
                print(f'  ✓ 稳定: 状态保持不变')
            
        if all_stable:
            print(f'\n✓ 测试通过: EXTEST 模式下所有引脚状态稳定，未发生自发变化')
        else:
            print(f'\n✗ 测试失败: 部分引脚状态发生了自发变化')
            print(f'  检测到的变化: {changes_detected[:5]}...' if len(changes_detected) > 5 else f'  检测到的变化: {changes_detected}')
            
        # 切回 SAMPLE 模式
        self.jc.set_scan_mode(self.dev_num, 'sample')
        self.mode = 'sample'
            
        return all_stable


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
    函数3: EXTEST模式 - PS_MIO51 以 1Hz (间隔0.5s) 闪烁 3秒，同时监控 PS_MIO0
    '''
    print('\n===== 函数3: EXTEST闪烁 PS_MIO51 (1Hz, 3秒) 并监控 PS_MIO0 =====')
    jw.extest_blink_with_monitor('PS_MIO51', 'PS_MIO0', freq_hz=1, duration_s=3)

    '''
    函数4: EXTEST闪烁时监控 PS_MIO0 状态（验证不受影响）
    '''
    print('\n===== 函数4: 监控 PS_MIO0 当 PS_MIO51 闪烁时 =====')
    changes = jw.monitor_pin_during_extest('PS_MIO0', 'PS_MIO51', freq_hz=2, duration_s=3)

    '''
    函数5: EXTEST 模式稳定性测试 - 验证不主动设置时引脚状态是否稳定
    '''
    print('\n===== 函数5: EXTEST 模式稳定性测试 =====')
    stable = jw.test_extest_stability(
        monitor_pins=['PS_MIO51', 'PS_MIO0', 'PS_MIO1'],
        duration_s=5,
        interval_ms=100
    )

    '''
    函数6: EXTEST 单引脚设置测试 - 验证设置一个引脚时是否影响其他引脚
    '''
    print('\n===== 函数6: EXTEST 单引脚设置测试 =====')
    success = jw.test_extest_single_pin_set(
        target_pin='PS_MIO51',
        monitor_pins=['PS_MIO0', 'PS_MIO1']
    )
