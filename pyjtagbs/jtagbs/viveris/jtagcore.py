# JTAG Core library
# Copyright (c) 2008 - 2021 Viveris Technologies
# Copyright (c) 2021 Colin O'Flynn [Python interface portion]
#
# JTAG Core library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# JTAG Core library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with JTAG Core library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA

import ctypes
from ctypes import create_string_buffer, CFUNCTYPE, POINTER, c_char, c_void_p, c_char_p, c_int, byref, c_ubyte
import platform
import os
import sys
import time

dirname = os.path.dirname(__file__)


class JTAGCore(object):
    """Python interface for JTAG Boundary Scanner library.

    This is a wrapper around the C DLL"""

    error_codes = {
        0: "JTAG_CORE_NO_ERROR",
        -1: "JTAG_CORE_BAD_PARAMETER",
        -2: "JTAG_CORE_ACCESS_ERROR",
        -3: "JTAG_CORE_IO_ERROR",
        -4: "JTAG_CORE_MEM_ERROR",
        -5: "JTAG_CORE_NO_PROBE",
        -6: "JTAG_CORE_NOT_FOUND",
        -7: "JTAG_CORE_CMD_NOT_FOUND",
        -8: "JTAG_CORE_INTERNAL_ERROR",
        -9: "JTAG_CORE_BAD_CMD",
        -10: "JTAG_CORE_I2C_BUS_NOTFREE"
    }
    _standard_calls_ = [
        'jtagcore_init',
        'jtagcore_deinit',
        'jtagcore_set_logs_callback',
        'jtagcore_set_logs_level',
        'jtagcore_get_logs_level',
        'jtagcore_set_logs_file',
        'jtagcore_get_logs_file',
        'jtagcore_get_number_of_probes_drv',
        'jtagcore_get_number_of_probes',
        'jtagcore_get_probe_name',
        'jtagcore_select_and_open_probe',
        'jtagcore_scan_and_init_chain',
        'jtagcore_get_number_of_devices',
        'jtagcore_get_dev_id',
        'jtagcore_loadbsdlfile',
        'jtagcore_get_dev_name',
        'jtagcore_get_bsdl_id',
        'jtagcore_get_number_of_pins',
        'jtagcore_get_pin_properties',
        'jtagcore_get_pin_id',
        'jtagcore_get_pin_state',
        'jtagcore_set_pin_state',
        'jtagcore_set_scan_mode',
        'jtagcore_push_and_pop_chain',
        'jtagcore_i2c_set_scl_pin',
        'jtagcore_i2c_set_sda_pin',
        'jtagcore_i2c_write_read',
        'jtagcore_spi_set_cs_pin',
        'jtagcore_spi_set_clk_pin',
        'jtagcore_spi_set_miso_pin',
        'jtagcore_spi_set_mosi_pin',
        'jtagcore_spi_write_read',
        'jtagcore_spi_set_bitorder',
        'jtagcore_mdio_set_mdio_pin',
        'jtagcore_mdio_set_mdc_pin',
        'jtagcore_mdio_read',
        'jtagcore_mdio_write',
        'jtagcore_memory_clear_pins',
        'jtagcore_memory_set_address_pin',
        'jtagcore_memory_set_data_pin',
        'jtagcore_memory_set_ctrl_pin',
        'jtagcore_memory_read',
        'jtagcore_memory_write',
        'jtagcore_setEnvVar',
        'jtagcore_getEnvVar',
        'jtagcore_getEnvVarValue',
        'jtagcore_getEnvVarIndex',
        'jtagcore_initScript',
        'jtagcore_setScriptOutputFunc',
        'jtagcore_execScriptLine',
        'jtagcore_execScriptFile',
        'jtagcore_execScriptRam',
        'jtagcore_deinitScript',
        'jtagcore_savePinsStateScript'
    ]
    INPUT = 1
    OUTPUT = 2
    TRISTATE = 4
    OE = 4

    def __init__(self):
        if platform.system() == 'Linux':
            from ctypes import cdll
            self.lib = cdll.LoadLibrary(os.path.abspath("lib_jtag_core.so"))

        else:
            if sys.maxsize > 2 ** 32:
                liblocation = os.path.join(dirname, 'libjtagcore_x64.dll')
            else:
                liblocation = os.path.join(dirname, 'libjtagcore_x86.dll')

            # self.lib = ctypes.windll.LoadLibrary(
            #     ctypes.find_library(liblocation)
            # )

            self.lib = ctypes.cdll.LoadLibrary(liblocation)

            # if sys.maxsize > 2 ** 32:
            #     _winlib = ctypes.windll.LoadLibrary(liblocation)
            #     for stdcall in self._standard_calls_:
            #         if hasattr(_winlib, stdcall):
            #             setattr(self.lib, stdcall, getattr(_winlib, stdcall))

        self.lib.jtagcore_init.restype = ctypes.POINTER(c_void_p)

        self._print_callback = CFUNCTYPE(c_int, POINTER(c_void_p), POINTER(c_char))(self._loggingprint)

        self._jtag = self.lib.jtagcore_init()
        # self.lib.jtagcore_set_logs_callback(self._jtag, self._print_callback)

    def __del__(self):
        self.deinit()

    def _loggingprint(self, cvoid, cpoint):
        print("hitting callback")  # never seen this yet?
        # print(cpoint.value)

    def set_debug_level(self, level):
        """Set level from 0 (verbose) to 4 (little)"""
        self.lib.jtagcore_set_logs_level(self._jtag, level)

    def _check_return(self, rv):
        """Check if return value is not 0, report error."""

        if rv >= 0:
            return

        if rv < 0:
            raise IOError("Exception: %s" % self.error_codes[rv])

    def deinit(self):
        """Close connection - on some devices code will hang if you don't call this"""
        if hasattr(self, 'lib') and hasattr(self, '_jtag'):
            self.lib.jtagcore_deinit(self._jtag)

    def get_probe_names(self):
        """Get the name of returned probes"""

        probes = {}

        probe_driver_ids = self.lib.jtagcore_get_number_of_probes_drv(self._jtag)
        self._check_return(probe_driver_ids)

        for probe_driver_id in range(probe_driver_ids):
            probe_indexes = self.lib.jtagcore_get_number_of_probes(self._jtag, probe_driver_id)
            self._check_return(probe_indexes)

            for probe_index in range(probe_indexes):
                probename = create_string_buffer(256)
                probeid = (probe_driver_id << 8) | probe_index
                self.lib.jtagcore_get_probe_name(self._jtag, probeid, probename)
                probes[probename.value.decode()] = probeid

        print(probes)
        return probes

    def open_probe(self, probeid):
        """Try to open the given probe"""
        rv = self.lib.jtagcore_select_and_open_probe(self._jtag, probeid)
        self._check_return(rv)

    def scan_init_chain(self, verbose=False):
        """Init the scan chain"""
        if verbose:
            raise NotImplementedError("No verbose option for this interface")
        rv = self.lib.jtagcore_scan_and_init_chain(self._jtag)
        self._check_return(rv)

        return rv

    def get_number_devices(self):
        """Get number of devices detected in the chain"""

        rv = self.lib.jtagcore_get_number_of_devices(self._jtag)
        self._check_return(rv)

        return rv

    def get_devid(self, device_number):
        """Get a given device ID in the chain"""

        rv = self.lib.jtagcore_get_dev_id(self._jtag, device_number)
        self._check_return(rv)

        return rv

    def bsdl_attach(self, filepath, device_number):
        """Attach a BSDL file to a given device on the chain"""

        if not os.path.exists(filepath):
            raise IOError("Check path: %s" % filepath, filepath)

        try:
            cpath = c_char_p(filepath)
        except TypeError:
            cpath = c_char_p(filepath.encode('utf-8'))

        rv = self.lib.jtagcore_loadbsdlfile(self._jtag, cpath, device_number)
        self._check_return(rv)

    def get_bsdl_id(self, filepath):
        """Find the device id in a BSDL file (useful to match files)"""

        if not os.path.exists(filepath):
            raise IOError("Check path: %s" % filepath, filepath)

        try:
            cpath = c_char_p(filepath)
        except TypeError:
            cpath = c_char_p(filepath.encode('utf-8'))

        rv = self.lib.jtagcore_get_bsdl_id(self._jtag, cpath)
        self._check_return(rv)

        return rv

    def get_number_of_pins(self, device_number):
        """Get total number of pins in device"""

        rv = self.lib.jtagcore_get_number_of_pins(self._jtag, device_number)
        self._check_return(rv)

        return rv

    def get_pin_id(self, device_number, pinname):
        """Convert a pin name to a pin number/id"""

        if isinstance(pinname, str):
            pinname = pinname.encode("utf-8")

        rv = self.lib.jtagcore_get_pin_id(self._jtag, device_number, c_char_p(pinname))
        self._check_return(rv)
        return rv

    def get_pin_state(self, device_number, pinid, pintype="input"):
        """Get state of a pin register (normally input)"""

        if isinstance(pinid, str):
            pinid = self.get_pin_id(device_number, pinid)

        if pintype == "input":
            pintype = self.INPUT
        elif pintype == "output":
            pintype = self.OUTPUT
        elif pintype == "oe":
            pintype = self.OE
        else:
            raise ValueError("Invalid pintype", pintype)

        rv = self.lib.jtagcore_get_pin_state(self._jtag, device_number, pinid, pintype)
        self._check_return(rv)

        return rv

    def get_pin_properties(self, device_number, pinid):
        """Get pin name & type from numeric pin id"""

        if isinstance(pinid, str):
            raise ValueError("pinid must be integer for thiscall")

        buf = create_string_buffer(500)

        pt = c_int(0)

        rv = self.lib.jtagcore_get_pin_properties(self._jtag, device_number, pinid, buf, 500, byref(pt))
        self._check_return(rv)

        pintype = []

        if pt.value & self.INPUT:
            pintype.append("input")

        if pt.value & self.OUTPUT:
            pintype.append("output")

        if pt.value & self.OE:
            pintype.append("oe")

        return {"name": buf.value.decode(), "location": "", "type": pintype}

    def set_pin_state(self, device, pinid, state, pintype):
        """Set or clear a bit in a given output register (output or oe)"""

        if pintype == "oe":
            pintype = self.OE
        elif pintype == "input":
            pintype = self.INPUT
        elif pintype == "output":
            pintype = self.OUTPUT
        else:
            raise ValueError("Unknown typ", pintype)

        if state == True or state == 1:
            state = 1
        elif state == False or state == 0:
            state = 0
        else:
            raise ValueError("Unknown state", state)

        rv = self.lib.jtagcore_set_pin_state(self._jtag, device, pinid, pintype, state)
        self._check_return(rv)

    def set_scan_mode(self, device_number, mode):
        """Set scan mode to passive (sample) or active (extest)"""
        if mode == "passive" or mode == "sample":
            mode = 0
        elif mode == "active" or mode == "extest":
            mode = 1

        rv = self.lib.jtagcore_set_scan_mode(self._jtag, device_number, mode)
        self._check_return(rv)

    def scan(self, write_only=False):
        """Perform an update of the JTAG chain status, can do writeonly to ignore inputs"""

        if write_only:
            mode = 1
        else:
            mode = 0

        rv = self.lib.jtagcore_push_and_pop_chain(self._jtag, mode)
        # print(rv)
        self._check_return(rv)

    def set_i2c_pins(self, device, clk_pin, sda_pin):
        if isinstance(clk_pin, str):
            clk_pin = self.get_pin_id(device, clk_pin)
            sda_pin = self.get_pin_id(device, sda_pin)

        self.lib.jtagcore_i2c_set_scl_pin(self._jtag, device, c_int(clk_pin))
        self.lib.jtagcore_i2c_set_sda_pin(self._jtag, device, c_int(sda_pin))

    def i2c_write_read(self, address, address10bits, write_data, read_size):   # 暂未调通
        # 准备写入数据
        write_size = len(write_data)
        wr_buffer = (c_ubyte * write_size)(*write_data)  # 将 Python 列表转换为 C 数组

        # 准备读取缓冲区
        rd_buffer = (c_ubyte * read_size)()  # 分配读取缓冲区

        # 调用函数
        result = self.lib.jtagcore_i2c_write_read(
            self._jtag,
            address,  # I2C 地址
            address10bits,  # 10 位地址标志
            write_size,  # 写入数据大小
            wr_buffer,  # 写入缓冲区
            read_size,  # 读取数据大小
            rd_buffer  # 读取缓冲区
        )

        # 检查返回值
        print(result)
        if result != 0:
            raise Exception(f"I2C operation failed with error code: {result}")

        # 将读取缓冲区转换为 Python 列表
        read_data = list(rd_buffer)
        return read_data


