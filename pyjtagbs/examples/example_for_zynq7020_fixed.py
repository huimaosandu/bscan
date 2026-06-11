'''
description :  a example to get state of pins
author:fanc21
create date:2025-0506
'''

import os
import json
from grako.util import asjson

from jtagbs.viveris.jtagcore import JTAGCore
from jtagbs.bsdlparser.bsdl import main


class jtag_worker:  # 解析bsdl文件， 基于bsdl从jtag获取数据
    def __init__(self, path):
        self.bsdl_path = path
        self.pin_map = {}
        self.pin_states = {}
        # self.list_all_pins()  # 比较耗时，想要通过引脚的位置号来控制时，需要打开
        self.dev_num = ''

        # 从JTAG口获取硬件信息
        self.jc = JTAGCore()
        self.mode = 'sample'
        self.isPrintLog = False

    '''
    ============================bsdl文件处理部分=========================================
    '''

    # get zynq xc7z020 pin map
    def transfer_bsdl_to_json(self, isWrite2Local=True):  # 把芯片的bsdl描述文件中的pin信息提取出来，转为json格式
        ast = main(self.bsdl_path, '_bsdl_description_')
        # ast = generic_main(main, bsdlParser, name='bsdl')

        ast_json = (json.dumps(asjson(ast), indent=4))
        bsdl_json_path = self.bsdl_path[:-3] + 'json'

        with open(bsdl_json_path, 'w', encoding='utf-8') as f:
            f.write(ast_json)

        ast = main(self.bsdl_path, '_bsdl_description_')
        # ast = generic_main(main, bsdlParser, name='bsdl')

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
    ============================ JTAG =========================================
    '''

    def init_jc(self):  # 初始化jtag设备，获取设备号，给设备添加bsdl文件
        probes = self.jc.get_probe_names()
        if not probes:
            raise RuntimeError('未检测到JTAG探头，请检查硬件连接')
        if self.isPrintLog:
            print(f'检测到jtag探头: {probes}')

        # 动态获取第一个探头的真实probe ID，格式为(driver_id << 8) | probe_index
        probe_id = next(iter(probes.values()))
        self.jc.open_probe(probe_id)
        if self.isPrintLog:
            print(f'打开jtag探头 ID={probe_id}')

        res = self.jc.scan_init_chain()
        if self.isPrintLog:
            print(f'初始化jtag扫描链: {res}')

        numDevs = self.jc.get_number_devices()
        if self.isPrintLog:
            print(f'检测到{numDevs}个设备')

        for i in range(numDevs):
            bsdl_dev_id = str(hex(self.jc.get_bsdl_id(self.bsdl_path)))[2:]
            if self.isPrintLog:
                print(f'从bsdl文件中获取到的设备名称为:{bsdl_dev_id}')

            dev_id = str(hex(self.jc.get_devid(i)))[2:]  # 获取设备名称

            if bsdl_dev_id in dev_id:  # 设备名称和bsdl名称一致时，给设备加载bsdl文件
                self.dev_num = i
                self.jc.bsdl_attach(self.bsdl_path, i)
                if self.isPrintLog:
                    print(f'给设编号为{dev_id}的设备加载bsdl文件')

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
        # print(state)
        return state

    def set_single_pin(self, pin, state, isPinLocation=False):  # 设置单个管脚; pin_type:input, output, oe
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
        self.jc.scan()  # 读数据前和写数据后需要scan

    def set_i2c_pins(self, scl_pin, sda_pin, isPinLocation=False):
        if self.mode == 'sample':
            self.jc.set_scan_mode(self.dev_num, "extest")
            self.mode = 'extest'

        if isPinLocation:
            for k in self.pin_map.keys():
                if self.pin_map[k].strip(',') == scl_pin:
                    scl_pin = k

                if self.pin_map[k].strip(',') == sda_pin:
                    sda_pin = k

        if isinstance(scl_pin, str):
            scl_pin = self.jc.get_pin_id(self.dev_num, scl_pin)
            sda_pin = self.jc.get_pin_id(self.dev_num, sda_pin)

        self.jc.set_i2c_pins(self.dev_num, scl_pin, sda_pin)

    def i2c_write_read(self, address, address10bits, write_data, read_size):
        return self.jc.i2c_write_read(address, address10bits, write_data, read_size)


if __name__ == '__main__':
    # 文件路径
    bsdl_path = os.path.join(os.path.dirname(os.getcwd()),
                             r'bsdl_files/xilinx/zynq/xc7z020_clg400.bsd')
    jw = jtag_worker(bsdl_path)
    # jw.transfer_bsdl_to_json()
    jw.init_jc()
    jw.get_properties_for_all_pins()

    print('-----------------')
    print(jw.pin_map)
    print(jw.pin_states)
    print(jw.dev_num)

    '''
    控制单个引脚的电平状态， 如果jtagcore.scan()没有用writeonly模式，需要
    '''
    # jw.set_single_pin('E6', 0, isPinLocation=True)
    # jw.read_single_pin('E6', pin_type='output', isPinLocation=True)

    # jw.set_single_pin('PS_MIO0', 0, isPinLocation=False)
    # jw.read_single_pin('PS_MIO0', pin_type='output', isPinLocation=False)

    '''
    控制单个引脚，循环100次
    '''
    # for i in range(100):
    #     jw.set_single_pin('PS_MIO0', 1, isPinLocation=False)   # 如果使用引脚的location， islocation字段需为True
    #     # jw.read_single_pin('PS_MIO0', pin_type='output', isLocation=False)
    #
    #     jw.set_single_pin('PS_MIO0', 0,  isPinLocation=False)
    #     # jw.read_single_pin('PS_MIO0', pin_type='output', isPinLocation=False)

    '''
    i2c总线读写
    '''
    jw.set_i2c_pins('PS_MIO12', 'PS_MIO13')
    print(jw.i2c_write_read(address=0x56, address10bits=0, write_data=[0x55, 0x55, 0x55]*1000, read_size=0))
    # res = jw.i2c_write_read(address=0x56, address10bits=0, write_data=[], read_size=10)
    # print(res)
