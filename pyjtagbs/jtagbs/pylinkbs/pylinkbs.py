# PyJtagBS J-Link interface via PyLink
# Copyright (c) 2021 Colin O'Flynn
#
# This file is HEAVILY based on code from "JTAG Core library", which is:
# Copyright (c) 2008 - 2021 Viveris Technologies (but also LGPGv2.1)
#
# PyJTAGBS is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# PyJTAGBS is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with PyJTAGBS; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#
# ============================================================
# 修改记录
# 修改人: claude code
# 修改日期: 2025-06-12
# 修改内容:
#   1. [Bug Fix] scan() - EXTEST 模式下每次 scan() 都重新加载 IR 导致 TAP
#      状态机在非 IDLE 状态时跑飞，从而自动退出 EXTEST 模式。
#      修复: scan() 通过 _last_ir_opcode 缓存上一次写入的 IR 操作码，
#      仅在模式发生变化时才重新写入 IR，避免频繁 IR 写入扰动 TAP 状态。
#   2. [Bug Fix] scan() - _output_bits 初始化为全 0，进入 EXTEST 后首次
#      scan() 将全 0 推入 BSR，导致所有引脚状态归零。
#      修复: 新增 capture_current_state() 方法，进入 EXTEST 前先以
#      SAMPLE 模式做一次 Capture-DR，将当前硬件引脚电平读入 _input_bits，
#      再将 _input_bits 同步到 _output_bits，保证首次推送的值与引脚原
#      状态一致，消除进入 EXTEST 瞬间的电平扰动。
#   3. [Bug Fix] write_IR() 在 jtagraw.py 中未判断当前 TAP 状态就固定
#      发送 TMS=0b00110（从 IDLE 跳到 Shift-IR 的序列），当 state 不在
#      IDLE 时会导致 TAP 状态机跑飞。pylinkbs.py 的 scan() 现在在调用
#      write_IR 前确保 TAP 处于 RUN_TEST_IDLE。
# ============================================================

import pylink
import struct
import math
import os

from ..bsdl import BSDLFile
from ..jtagraw import JTAGRawBS

# JTAG标准指令码（大部分设备通用）
IR_SAMPLE  = 0b00001
IR_EXTEST  = 0b00000
IR_BYPASS  = 0b11111


class PyLinkRawBS(JTAGRawBS):
    """Python interface for JTAG via PyLink Library (which talks to J-Link).

    相比 Viveris JTAGCore DLL，此接口直接通过 pylink 操作 J-Link 硬件，
    实现了完整的引脚级 boundary scan 操作。
    """

    def __init__(self, dll_path=None, speed_khz=4000):
        if dll_path is not None:
            lib = pylink.library.Library(dllpath=dll_path)
            self.jlink = pylink.JLink(lib=lib)
        else:
            self.jlink = pylink.JLink()
        self._speed_khz = speed_khz
        self._scan_mode = 'sample'  # 当前扫描模式
        self._output_bits = {}      # device_number -> list of output bits (boundary scan register)
        self._input_bits = {}       # device_number -> list of input bits (最近一次 scan 读回)
        self._ir_lengths = []       # 每个设备的 IR 长度
        self._ir_opcodes = {}        # device_number -> {'sample': int, 'extest': int, 'bypass': int, 'ir_length': int}
        self._pin_name_map = {}     # device_number -> set of port names
        self._last_ir_opcode = None  # 上次写入的 IR 操作码名称，用于避免重复写 IR

    # ================================================================
    # 探头管理
    # ================================================================

    def get_probe_names(self):
        """Get the name of returned probes"""
        probes = {}
        ems = self.jlink.connected_emulators()
        for i, em in enumerate(ems):
            em = str(em)
            probes[em] = i
        return probes

    def open_probe(self, probeid=None):
        """Open the J-Link probe and configure JTAG interface"""
        self.jlink.open()
        self.jlink.set_speed(self._speed_khz)
        self.jlink.set_tif(pylink.enums.JLinkInterfaces.JTAG)
        self.jlink.jtag_create_clock()

    def jtag_rawrw(self, tdo, tms, num_bits=None, write_only=False):
        """Send TDI/TMS bits and capture TDO data using pylink buffered API.

        Uses jtag_send() in <=32-bit chunks, then jtag_sync_bits() + jtag_read()
        to replicate the synchronous jtag_rawrw behavior from Viveris DLL.
        """
        if num_bits is None:
            num_bits = len(tdo) * 8

        if num_bits == 0:
            return []

        # 将字节列表打包为整数（LSB first），分块发送（每块最多32位）
        offset = 0
        while offset < num_bits:
            chunk_bits = min(32, num_bits - offset)

            # 从字节列表中提取当前块的 TDI/TMS 整数
            tdi_int = 0
            tms_int = 0
            for i in range(chunk_bits):
                bit_pos = offset + i
                byte_idx = bit_pos // 8
                bit_in_byte = bit_pos % 8
                if byte_idx < len(tdo):
                    tdi_int |= ((tdo[byte_idx] >> bit_in_byte) & 1) << i
                if byte_idx < len(tms):
                    tms_int |= ((tms[byte_idx] >> bit_in_byte) & 1) << i

            self.jlink.jtag_send(tms_int, tdi_int, chunk_bits)
            offset += chunk_bits

        # 刷新缓冲区到设备，触发实际 JTAG 传输
        self.jlink.jtag_sync_bits()

        if write_only:
            return [0] * int(math.ceil(num_bits / 8.0))

        # 从设备读回 TDO 捕获数据
        byte_count = int(math.ceil(num_bits / 8.0))
        # 读起始偏移为0（sync 后缓冲区重置）
        result = self.jlink.jtag_read(0, num_bits)

        # 截断到预期字节数
        return result[:byte_count]

    # ================================================================
    # BSDL 相关
    # ================================================================

    def get_bsdl_id(self, filepath):
        """Find the device id in a BSDL file"""
        file = BSDLFile(filepath)
        idmask, fileidcode = file.get_idcode()
        return fileidcode

    def bsdl_attach(self, filepath, device_number, force=False):
        """Attach a BSDL file to a given device on the chain"""
        file = BSDLFile(filepath)
        idmask, fileidcode = file.get_idcode()
        scanchainidcode = self.get_devid(device_number)

        if (fileidcode & idmask) != (scanchainidcode & idmask):
            if not force:
                raise IOError("BSDL file idcode: %s, detected idcode %s" % (hex(fileidcode), hex(scanchainidcode)))

        self.bsdl[device_number] = file

        # 初始化该设备的 output bit buffer（全0）
        chain_bits = file.number_of_chainbits()
        self._output_bits[device_number] = [0] * chain_bits
        self._input_bits[device_number] = [0] * chain_bits

        # 构建 pin name 集合（io_regs 的键就是端口名）
        self._pin_name_map[device_number] = set(file.io_regs.keys())

        # 从 BSDL 提取 IR 长度和指令操作码
        self._extract_ir_opcodes(file, device_number)

    def _extract_ir_opcodes(self, bsdl_file, device_number):
        """从 BSDL 文件中提取 IR 长度和 SAMPLE/EXTEST/BYPASS 操作码"""
        try:
            ird = bsdl_file.bsdl['instruction_register_description']
            ir_length = int(ird['instruction_length'])

            opcodes = {}
            for instr in ird['instruction_opcodes']:
                name = instr['instruction_name'].upper()
                if name in ('SAMPLE', 'EXTEST', 'BYPASS'):
                    opcodes[name.lower()] = int(instr['opcode_list'][0], 2)

            self._ir_opcodes[device_number] = {
                'ir_length': ir_length,
                'sample': opcodes.get('sample', 1),
                'extest': opcodes.get('extest', 0),
                'bypass': opcodes.get('bypass', (1 << ir_length) - 1),
            }

            # 更新该设备的 IR 长度
            while len(self._ir_lengths) <= device_number:
                self._ir_lengths.append(5)  # 默认5位
            self._ir_lengths[device_number] = ir_length

            # 重新计算没有 BSDL 的设备 IR 长度
            corrected_total = self.total_IR_length  # scan_init_chain 已修正
            known_sum = sum(self._ir_lengths[d]
                            for d in self._ir_opcodes)
            remaining = corrected_total - known_sum
            for d in range(self.num_devices):
                if d not in self._ir_opcodes and remaining > 0:
                    self._ir_lengths[d] = remaining
                    remaining = 0
        except Exception as e:
            print("Warning: 无法从BSDL提取IR操作码: %s" % e)

    # ================================================================
    # 扫描模式
    # ================================================================

    def _get_ir_opcodes(self, device_number):
        """获取设备的 IR 操作码 {sample, extest, bypass, ir_length}"""
        if device_number in self._ir_opcodes:
            return self._ir_opcodes[device_number]
        # 默认 5 位 IR（大多数通用设备）
        return {'ir_length': 5, 'sample': 0b00001, 'extest': 0b00000, 'bypass': 0b11111}

    def set_scan_mode(self, device_number, mode):
        """Set scan mode to passive (sample) or active (extest)"""
        if mode in ('passive', 'sample'):
            new_mode = 'sample'
        elif mode in ('active', 'extest'):
            new_mode = 'extest'
        else:
            raise ValueError("Unknown mode: %s, use 'sample' or 'extest'" % mode)

        # 只有模式真正变化时才重置 IR 缓存，强制下次 scan() 重新加载 IR
        if new_mode != self._scan_mode:
            self._last_ir_opcode = None

        self._scan_mode = new_mode

    def _load_ir_for_device(self, device_number, ir_opcode_name):
        """将指定指令加载到目标设备的 IR（使用 BSDL 提取的操作码）"""
        total_ir_bits = sum(self._ir_lengths)

        # 构建 IR 数据：设备顺序从近到远
        ir_bits = []
        for d in range(self.num_devices):
            ops = self._get_ir_opcodes(d)
            ir_len = self._ir_lengths[d]
            if d == device_number:
                val = ops[ir_opcode_name]
            else:
                val = ops['bypass']  # 其他设备设为 BYPASS
            for bit in range(ir_len):
                ir_bits.append((val >> bit) & 1)

        # 转为字节
        bcount = math.ceil(total_ir_bits / 8)
        ir_bytes = [0] * bcount
        for i, bit in enumerate(ir_bits):
            ir_bytes[i // 8] |= (bit << (i % 8))

        self.write_IR(ir_bytes, total_ir_bits)

    def capture_current_state(self, device_number):
        """在切换到 EXTEST 之前，先用 SAMPLE 模式读取当前引脚电平，
        并将其同步到 _output_bits，确保首次 EXTEST scan() 推送的值
        与当前硬件状态一致，避免进入 EXTEST 瞬间其他引脚状态跳变。

        调用时机：set_scan_mode(dev, 'extest') 之前调用。
        """
        if device_number not in self._output_bits:
            return
        if device_number not in self._input_bits:
            return

        # 临时切到 SAMPLE 模式读取当前电平
        old_mode = self._scan_mode
        self._scan_mode = 'sample'
        self._last_ir_opcode = None  # 强制重新写 IR

        try:
            self.scan()
        except Exception as e:
            print("Warning: capture_current_state scan failed: %s" % e)
            self._scan_mode = old_mode
            return

        # 将读到的 input_bits 同步到 output_bits
        # 对每个有 BSDL 信息的设备做同步
        bsdl_file = self.bsdl[device_number] if device_number < len(self.bsdl) else None
        if bsdl_file is None:
            self._scan_mode = old_mode
            return

        input_bits = self._input_bits[device_number]
        output_bits = self._output_bits[device_number]

        for pin_name, reg in bsdl_file.io_regs.items():
            # 对于有 input + output 的引脚，将 input 值复制到 output
            if 'input' in reg and 'output' in reg:
                in_cell = reg['input']
                out_cell = reg['output']
                if in_cell < len(input_bits) and out_cell < len(output_bits):
                    output_bits[out_cell] = input_bits[in_cell]

            # 对于有 OE 的引脚，默认设为高阻（disable output）
            if 'oe' in reg:
                oe_cell = reg['oe']
                oe_disable = reg.get('oe_disable', 1)  # 默认禁用值为 1
                if oe_cell < len(output_bits):
                    output_bits[oe_cell] = oe_disable  # 高阻态

        # 恢复模式，下次 scan() 会因 _last_ir_opcode != opcode_name 而重新写 IR
        self._scan_mode = old_mode
        self._last_ir_opcode = None



    def scan(self, write_only=False):
        """Perform an update of the JTAG chain status.

        对所有设备执行 SAMPLE/EXTEST DR 扫描，读回输入引脚状态。

        修复说明：
        - 使用 _last_ir_opcode 缓存，仅在模式切换时才重新写入 IR，
          避免频繁 IR 写入在 TAP 非 IDLE 状态时引起状态机跑飞（自动退出 EXTEST）。
        - 在写 IR 之前确保 TAP 处于 RUN_TEST_IDLE 状态。
        """
        if not self.num_devices:
            return

        opcode_name = 'extest' if self._scan_mode == 'extest' else 'sample'

        # 仅在 IR 操作码发生变化（模式切换）时才重新写 IR
        # 避免每次 scan() 都发送 IR 序列扰动 TAP 状态机
        if self._last_ir_opcode != opcode_name:
            # 确保 TAP 处于 RUN_TEST_IDLE，write_IR 从该状态出发
            from ..jtagraw import Jtagstate
            if self.state != Jtagstate.RUN_TEST_IDLE:
                self._jtag_reset()
                self.tdo_flush(0, 6)
                self.state = Jtagstate.RUN_TEST_IDLE

            total_ir_bits = sum(self._ir_lengths)
            ir_bits = []
            for d in range(self.num_devices):
                ops = self._get_ir_opcodes(d)
                ir_len = self._ir_lengths[d]
                val = ops[opcode_name]
                for bit in range(ir_len):
                    ir_bits.append((val >> bit) & 1)

            bcount = math.ceil(total_ir_bits / 8)
            ir_bytes = [0] * bcount
            for i, bit in enumerate(ir_bits):
                ir_bytes[i // 8] |= (bit << (i % 8))
            self.write_IR(ir_bytes, total_ir_bits)

            self._last_ir_opcode = opcode_name

        # 构建 DR 数据（所有设备的 output bits 拼接）
        total_dr_bits = 0
        for d in range(self.num_devices):
            if d in self._output_bits:
                total_dr_bits += len(self._output_bits[d])
            else:
                total_dr_bits += 1  # BYPASS 设备占1位

        dr_bits_out = []
        for d in range(self.num_devices):
            if d in self._output_bits:
                dr_bits_out.extend(self._output_bits[d])
            else:
                dr_bits_out.append(0)  # BYPASS

        dr_bytes_out = [0] * math.ceil(total_dr_bits / 8)
        for i, bit in enumerate(dr_bits_out):
            dr_bytes_out[i // 8] |= (bit << (i % 8))

        # 执行 DR 扫描
        result = self.read_DR_raw(dr_bytes_out, total_dr_bits)

        # 解析读回数据
        if result is not None and not write_only:
            offset = 0
            for d in range(self.num_devices):
                if d in self._input_bits:
                    nbits = len(self._input_bits[d])
                    for bit_idx in range(nbits):
                        byte_idx = (offset + bit_idx) // 8
                        bit_pos = (offset + bit_idx) % 8
                        if byte_idx < len(result):
                            self._input_bits[d][bit_idx] = (result[byte_idx] >> bit_pos) & 1
                    offset += nbits
                else:
                    offset += 1  # BYPASS

    def read_DR_raw(self, tdi_bytes, num_bits):
        """Shift DR register, send tdi and capture tdo"""
        tms_bytes = [0] * len(tdi_bytes)
        # TMS 最后一位设为1，进入 Update-DR
        if num_bits > 0:
            last_byte = (num_bits - 1) // 8
            last_bit = (num_bits - 1) % 8
            tms_bytes[last_byte] |= (1 << last_bit)

        # 进入 Shift-DR 状态
        if self.state == self.state.__class__.RUN_TEST_IDLE:
            self.tms_write(0b001, 3)
        elif self.state == self.state.__class__.TEST_LOGIC_RESET:
            self.tms_write(0b0010, 4)

        # 执行移位
        result = self.jtag_rawrw(tdi_bytes, tms_bytes, num_bits)

        # 回到 Run-Test/Idle
        self.tms_write(0b01, 2)
        from ..jtagraw import Jtagstate
        self.state = Jtagstate.RUN_TEST_IDLE

        return result

    # ================================================================
    # 引脚级操作
    # ================================================================

    def get_pin_id(self, device_number, pinname):
        """Convert a pin name to a port id (io_regs key)"""
        if device_number not in self._pin_name_map:
            raise ValueError("No BSDL file attached for device %d" % device_number)
        names = self._pin_name_map[device_number]
        # 精确匹配
        if pinname in names:
            return pinname
        # 部分匹配
        for name in names:
            if pinname.upper() in name.upper():
                return name
        raise ValueError("Pin '%s' not found" % pinname)

    def get_pin_state(self, device_number, pinid, pintype="input"):
        """Get state of a pin register"""
        if isinstance(pinid, str):
            pinid = self.get_pin_id(device_number, pinid)

        if device_number not in self._input_bits:
            raise ValueError("Device %d has no BSDL file attached" % device_number)

        io_reg = self.bsdl[device_number].io_regs[pinid]

        if pintype == "input" or pintype == 1:
            if 'input' not in io_reg:
                raise ValueError("Pin %d has no input cell" % pinid)
            cell = io_reg['input']
            if cell < len(self._input_bits[device_number]):
                return self._input_bits[device_number][cell]
            return 0
        elif pintype == "output" or pintype == 2:
            if 'output' not in io_reg:
                raise ValueError("Pin %d has no output cell" % pinid)
            cell = io_reg['output']
            if cell < len(self._output_bits[device_number]):
                return self._output_bits[device_number][cell]
            return 0
        elif pintype == "oe" or pintype == 4:
            if 'oe' not in io_reg:
                raise ValueError("Pin %d has no oe cell" % pinid)
            cell = io_reg['oe']
            if cell < len(self._output_bits[device_number]):
                return self._output_bits[device_number][cell]
            return 0
        else:
            raise ValueError("Unknown pintype: %s" % pintype)

    def get_pin_properties(self, device_number, pinid):
        """Get pin name & type from port id"""
        if isinstance(pinid, str) and pinid not in self.bsdl[device_number].io_regs:
            pinid = self.get_pin_id(device_number, pinid)

        io_regs = self.bsdl[device_number].io_regs
        if pinid not in io_regs:
            return {"name": "unknown", "location": "", "type": []}

        reg = io_regs[pinid]
        pintype = []
        if 'input' in reg:
            pintype.append("input")
        if 'output' in reg:
            pintype.append("output")
        if 'oe' in reg:
            pintype.append("oe")

        return {"name": pinid, "location": "", "type": pintype}

    def set_pin_state(self, device, pinid, state, pintype):
        """Set or clear a bit in a given output register (output or oe)"""
        if isinstance(pinid, str):
            pinid = self.get_pin_id(device, pinid)

        if device not in self._output_bits:
            raise ValueError("Device %d has no BSDL file attached" % device)

        io_reg = self.bsdl[device].io_regs[pinid]
        value = 1 if state else 0

        if pintype == "oe" or pintype == 4:
            if 'oe' not in io_reg:
                raise ValueError("Pin %d has no oe cell" % pinid)
            cell = io_reg['oe']
            # oe_disable 值表示禁用输出的值，使能输出应设为相反值
            oe_disable = io_reg.get('oe_disable', 1)
            actual_value = (1 - oe_disable) if value else oe_disable
            if cell < len(self._output_bits[device]):
                self._output_bits[device][cell] = actual_value
        elif pintype == "output" or pintype == 2:
            if 'output' not in io_reg:
                raise ValueError("Pin %d has no output cell" % pinid)
            cell = io_reg['output']
            if cell < len(self._output_bits[device]):
                self._output_bits[device][cell] = value
        elif pintype == "input" or pintype == 1:
            pass  # 输入引脚不能设置
        else:
            raise ValueError("Unknown pintype: %s" % pintype)

    # ================================================================
    # 初始化增强：记录每个设备的 IR 长度
    # ================================================================

    def scan_init_chain(self, verbose=False):
        """Init the scan chain, and record IR lengths for each device.

        重写基类方法，修正 IR 长度测量算法。
        基类使用 bin(r).count('0') 按 MSB-first 字符串计数，
        但 JTAG 数据是 LSB-first 字节，导致计数错误。
        """
        result = super().scan_init_chain(verbose)

        # 重新测量 IR 长度（使用 LSB-first 位计数）
        self._jtag_reset()
        self.tms_write(0b00110, 5)  # Go to Shift-IR
        self.tdo_flush(0, self.flush_len * 2, write_only=True)  # Clear IR
        ir_result = self.tdo_flush(1, self.flush_len)  # Fill with 1s

        # 找到第一个 1-bit 的位置 = IR 长度
        total_ir = 0
        for byte_val in ir_result:
            if byte_val == 0:
                total_ir += 8
            else:
                # 计算该字节中第一个 1-bit 的位置 (LSB-first)
                for bit in range(8):
                    if (byte_val >> bit) & 1:
                        total_ir += bit
                        break
                break

        self.total_IR_length = total_ir
        if verbose:
            print("Corrected IR Length: %d" % self.total_IR_length)

        # 均分 IR 长度作为初始估计（BSDL 加载后会用真实值替换）
        self._ir_lengths = [total_ir // self.num_devices] * self.num_devices if self.num_devices else []

        return result