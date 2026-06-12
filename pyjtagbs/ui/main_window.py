"""JTAG 边界扫描主窗口 - tkinter/ttkbootstrap 版本"""
import os
import sys
import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog

import ttkbootstrap as ttkb
from ttkbootstrap.constants import *

# 后端导入
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from jtagbs.pylinkbs.pylinkbs import PyLinkRawBS
from jtagbs.bsdl import BSDLFile

from .widgets import SampleGrid, WaveformCanvas


class MainWindow:
    """JTAG 边界扫描主窗口"""

    def __init__(self, root):
        self.root = root
        self.root.title('BScanner - JTAG 边界扫描工具')
        self.root.geometry('1200x800')
        self.root.minsize(900, 600)

        # JTAG 后端
        self._jc = None
        self._dev_num = None
        self._bsdl_path = ''
        self._dll_path = ''
        self._pin_names = []
        self._pin_map = {}
        self._io_regs = {}

        # EXTEST 状态
        self._extest_mode = False
        self._pin_z_set = set()        # 当前设为 Z 的引脚
        self._linked_pins = set()      # linkage 引脚集合

        # BSDL 自动匹配
        self._device_infos = []        # 链中所有设备信息

        # 扫描状态
        self._scan_count = 0
        self._continuous_running = False
        self._continuous_thread = None

        # 路径
        self._init_paths()
        self._build_ui()

    def _init_paths(self):
        """初始化路径"""
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.normpath(os.path.join(script_dir, '..', '..'))
        self._dll_path = os.path.join(project_root, 'tools', 'JLink_x64.dll')
        if not os.path.exists(self._dll_path):
            self._dll_path = os.path.join(project_root, 'tools', 'JLinkARM.dll')
        self._bsdl_path = os.path.join(
            project_root, 'pyjtagbs', 'bsdl_files', 'xilinx', 'zynq', 'xc7z020_clg400.bsd'
        )

    def _build_ui(self):
        """构建 UI"""
        self._build_menu()
        self._build_toolbar()
        self._build_main_area()
        self._build_statusbar()

    # ================================================================
    # 菜单栏
    # ================================================================

    def _build_menu(self):
        """构建菜单栏"""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        # File 菜单
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label='连接设备', command=self._on_connect, accelerator='Ctrl+J')
        file_menu.add_separator()
        file_menu.add_command(label='选择 BSDL 文件...', command=self._on_change_bsdl)
        file_menu.add_separator()
        file_menu.add_command(label='退出', command=self.root.quit)
        menubar.add_cascade(label='文件', menu=file_menu)

        # Scan 菜单
        scan_menu = tk.Menu(menubar, tearoff=0)
        scan_menu.add_command(label='单次扫描', command=self._on_sample, accelerator='Ctrl+S')
        scan_menu.add_command(label='Run/Stop', command=self._on_run_toggle)
        menubar.add_cascade(label='扫描', menu=scan_menu)

        # View 菜单
        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_command(label='清除波形数据', command=lambda: self._waveform.clear_data())
        view_menu.add_command(label='清除所有波形', command=lambda: self._waveform.clear_all())
        menubar.add_cascade(label='视图', menu=view_menu)

        # 快捷键绑定
        self.root.bind('<Control-j>', lambda e: self._on_connect())
        self.root.bind('<Control-s>', lambda e: self._on_sample())

    # ================================================================
    # 工具栏
    # ================================================================

    def _build_toolbar(self):
        """构建工具栏"""
        toolbar = ttk.Frame(self.root, padding=5)
        toolbar.pack(fill=X, side=TOP)

        # 连接按钮
        self._connect_btn = ttkb.Button(toolbar, text='连接', bootstyle=PRIMARY,
                                        command=self._on_connect)
        self._connect_btn.pack(side=LEFT, padx=2)

        ttk.Separator(toolbar, orient=VERTICAL).pack(side=LEFT, fill=Y, padx=5)

        # 模式选择
        ttk.Label(toolbar, text='模式:').pack(side=LEFT, padx=(5, 2))
        self._mode_var = tk.StringVar(value='sample')
        self._mode_var.trace_add('write', lambda *a: self._on_mode_changed())
        mode_combo = ttkb.Combobox(toolbar, textvariable=self._mode_var,
                                    values=['sample', 'extest'],
                                    width=8, state='readonly')
        mode_combo.pack(side=LEFT, padx=2)

        # Run / Stop
        self._run_btn = ttkb.Button(toolbar, text='Run', bootstyle=SUCCESS,
                                     command=self._on_run_toggle, state=DISABLED)
        self._run_btn.pack(side=LEFT, padx=2)

        ttk.Separator(toolbar, orient=VERTICAL).pack(side=LEFT, fill=Y, padx=5)

        # 间隔设置
        ttk.Label(toolbar, text='间隔:').pack(side=LEFT, padx=(5, 2))
        self._interval_var = tk.StringVar(value='100ms')
        interval_combo = ttkb.Combobox(toolbar, textvariable=self._interval_var,
                                       values=['50ms', '100ms', '200ms', '500ms', '1s'],
                                       width=6, state='readonly')
        interval_combo.pack(side=LEFT, padx=2)

    # ================================================================
    # 主区域
    # ================================================================

    def _build_main_area(self):
        """构建主区域"""
        # 使用 PanedWindow 分左右
        main_pane = ttk.PanedWindow(self.root, orient=HORIZONTAL)
        main_pane.pack(fill=BOTH, expand=True, padx=2, pady=2)

        # ---- 左侧：引脚表 ----
        left_frame = ttkb.Labelframe(main_pane, text='引脚', padding=5)
        main_pane.add(left_frame, weight=1)

        # 搜索框
        search_frame = ttk.Frame(left_frame)
        search_frame.pack(fill=X, pady=(0, 5))
        ttk.Label(search_frame, text='搜索:').pack(side=LEFT)
        self._search_var = tk.StringVar()
        self._search_var.trace_add('write', lambda *a: self._on_search_changed())
        search_entry = ttkb.Entry(search_frame, textvariable=self._search_var)
        search_entry.pack(side=LEFT, fill=X, expand=True, padx=(5, 0))

        # 引脚表 (Treeview)
        columns = ('name', 'location', 'io', 'value', 'type')
        self._tree = ttkb.Treeview(left_frame, columns=columns, show='headings',
                                   bootstyle=INFO, selectmode='browse')
        self._tree.heading('name', text='名称')
        self._tree.heading('location', text='位置')
        self._tree.heading('io', text='I/O')
        self._tree.heading('value', text='值')
        self._tree.heading('type', text='类型')

        self._tree.column('name', width=120, minwidth=80)
        self._tree.column('location', width=50, minwidth=40)
        self._tree.column('io', width=60, minwidth=40)
        self._tree.column('value', width=40, minwidth=30)
        self._tree.column('type', width=50, minwidth=40)

        # linkage 引脚灰色显示
        self._tree.tag_configure('linkage', foreground='#999999')

        # 滚动条
        tree_scroll = ttk.Scrollbar(left_frame, orient=VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=tree_scroll.set)

        self._tree.pack(side=LEFT, fill=BOTH, expand=True)
        tree_scroll.pack(side=RIGHT, fill=Y)

        # 右键菜单
        self._tree_menu = tk.Menu(self._tree, tearoff=0)
        self._tree.bind('<Button-3>', self._on_tree_right_click)

        # ---- 右侧：设备信息 + 网格 + 波形 ----
        right_pane = ttk.PanedWindow(main_pane, orient=VERTICAL)
        main_pane.add(right_pane, weight=3)

        # 设备信息面板
        device_frame = ttkb.Labelframe(right_pane, text='设备信息', padding=5)
        right_pane.add(device_frame, weight=0)
        self._device_info_text = tk.Text(device_frame, height=4, bg='#F5F5F5',
                                          font=('Consolas', 9), relief=tk.FLAT,
                                          state=DISABLED, wrap=tk.WORD)
        self._device_info_text.pack(fill=X, expand=False)

        # SAMPLE 网格
        grid_frame = ttkb.Labelframe(right_pane, text='SAMPLE 网格', padding=5)
        right_pane.add(grid_frame, weight=2)
        self._sample_grid = SampleGrid(grid_frame, height=250)
        self._sample_grid.pack(fill=BOTH, expand=True)

        # 波形
        wave_frame = ttkb.Labelframe(right_pane, text='波形', padding=5)
        right_pane.add(wave_frame, weight=3)
        self._waveform = WaveformCanvas(wave_frame, height=200)
        self._waveform.pack(fill=BOTH, expand=True)

    # ================================================================
    # 状态栏
    # ================================================================

    def _build_statusbar(self):
        """构建状态栏"""
        status_frame = ttk.Frame(self.root, padding=(5, 2))
        status_frame.pack(fill=X, side=BOTTOM)

        self._status_var = tk.StringVar(value='就绪')
        self._device_var = tk.StringVar(value='')
        self._count_var = tk.StringVar(value='')

        ttk.Label(status_frame, textvariable=self._status_var).pack(side=LEFT)
        ttk.Label(status_frame, textvariable=self._count_var).pack(side=RIGHT, padx=(10, 0))
        ttk.Label(status_frame, textvariable=self._device_var).pack(side=RIGHT)

    # ================================================================
    # 事件处理
    # ================================================================

    def _on_connect(self):
        """连接 J-Link"""
        if self._jc is not None:
            return

        self._connect_btn.configure(state=DISABLED, text='连接中...')
        self._status_var.set('正在连接 J-Link...')
        self.root.update_idletasks()

        try:
            self._jc = PyLinkRawBS(dll_path=self._dll_path)
            probes = self._jc.get_probe_names()
            if not probes:
                raise RuntimeError('未检测到 J-Link 探头')

            self._jc.open_probe()
            self._status_var.set(f'已连接: {list(probes.keys())[0]}')

            self._jc.scan_init_chain(verbose=False)
            num_devs = self._jc.get_number_devices()

            # 收集所有设备 IDCODE
            self._device_infos = []
            for i in range(num_devs):
                devid = self._jc.get_devid(i)
                self._device_infos.append({'index': i, 'idcode': devid})

            # 使用硬编码 BSDL 匹配设备
            bsdl_path = self._bsdl_path
            bsdl_file = BSDLFile(bsdl_path)
            idmask, fileidcode = bsdl_file.get_idcode()
            dev_num = None
            for i in range(num_devs):
                if (fileidcode & idmask) == (self._jc.get_devid(i) & idmask):
                    dev_num = i
                    break
            if dev_num is None:
                raise RuntimeError('未找到匹配的 BSDL 设备')

            self._dev_num = dev_num
            self._jc.bsdl_attach(bsdl_path, dev_num)

            # 加载引脚信息
            self._io_regs = self._jc.bsdl[dev_num].io_regs
            self._pin_names = sorted(self._io_regs.keys())

            # 构建 pin_map
            self._pin_map = {}
            try:
                bsdldict = self._jc.bsdl[dev_num].bsdl
                mappings = bsdldict.get('device_package_pin_mappings', [])
                if mappings:
                    for pm in mappings[0].get('pin_map', []):
                        if ':' in pm:
                            parts = pm.split(':')
                            self._pin_map[parts[0]] = parts[1]
            except Exception:
                pass

            # 更新设备信息面板
            self._update_device_panel()

            # 更新 UI
            self._load_pin_table()
            self._sample_grid.load_pins(self._pin_names, self._linked_pins)
            self._sample_grid.set_pin_locations(self._pin_map)

            # 设置网格设备信息
            try:
                dev_name = self._jc.bsdl[dev_num].get_name()
            except Exception:
                dev_name = os.path.basename(bsdl_path)
            self._sample_grid.set_device_info({
                'name': f'Dev{dev_num}: {dev_name}',
                'idcode': self._jc.get_devid(dev_num),
                'pins': len(self._pin_names),
                'mode': 'SAMPLE'
            })

            # 启用按钮
            self._run_btn.configure(state=NORMAL)
            self._connect_btn.configure(text='已连接', bootstyle=SUCCESS)

            # 状态栏显示 IDCODE 详情
            devid = self._jc.get_devid(dev_num)
            part_num = (devid >> 12) & 0xFFFF
            version = (devid >> 28) & 0xF
            self._device_var.set(
                f'设备: {num_devs} | 目标: Dev{dev_num} | '
                f'IDCODE: 0x{devid:08X} (Part:0x{part_num:04X} Ver:{version})')
            self._status_var.set(f'已连接，加载 {len(self._pin_names)} 个引脚')

        except Exception as e:
            self._connect_btn.configure(state=NORMAL, text='连接')
            self._status_var.set(f'连接失败: {e}')
            messagebox.showerror('连接错误', str(e))
            self._jc = None

    def _load_pin_table(self):
        """加载引脚表"""
        self._tree.delete(*self._tree.get_children())
        self._linked_pins = set()
        for name in self._pin_names:
            reg = self._io_regs[name]
            loc = self._pin_map.get(name, '').strip(',')
            types = []
            if 'input' in reg:
                types.append('in')
            if 'output' in reg:
                types.append('out')
            if 'oe' in reg:
                types.append('oe')
            io_str = '/'.join(types) if types else '-'
            type_str = 'inout' if len(types) > 1 else io_str
            # 仅有 input 无 output/oe 的引脚标记为 linkage
            is_linkage = ('input' in reg and 'output' not in reg and 'oe' not in reg)
            if is_linkage:
                self._linked_pins.add(name)
            tag = 'linkage' if is_linkage else ''
            self._tree.insert('', 'end', iid=name,
                              values=(name, loc, io_str, '-', type_str),
                              tags=(tag,) if tag else ())

    def _on_mode_changed(self):
        """模式切换时立即进入对应模式"""
        if not self._jc or self._dev_num is None:
            return
        
        # 如果正在连续扫描中，不处理（由 Run/Stop 控制）
        if self._continuous_running:
            return
        
        mode = self._mode_var.get().strip().lower()
        try:
            if mode == 'extest':
                # 进入 EXTEST 模式
                self._extest_mode = True
                self._jc.set_scan_mode(self._dev_num, 'extest')
                
                # ⚠️ 重要：进入 EXTEST 后，先将所有引脚设为高阻态（Z），避免意外驱动
                # 这样可以防止 _output_bits 中的默认值导致引脚闪烁
                self._pin_z_set.clear()
                for pin_name in self._pin_names:
                    reg = self._io_regs.get(pin_name, {})
                    # 只对具有 output 和 oe 的引脚设置 Z
                    if 'output' in reg and 'oe' in reg:
                        try:
                            # 禁用输出使能 -> 高阻
                            self._jc.set_pin_state(self._dev_num, pin_name, False, 'oe')
                            self._pin_z_set.add(pin_name)
                        except Exception:
                            pass  # 部分引脚可能不支持
                
                # 执行一次扫描，将所有引脚设为高阻态
                self._jc.scan()
                
                # 🔍 调试：打印 _output_bits 的状态
                print(f'\n=== DEBUG: 进入 EXTEST 模式后的 _output_bits ===')
                if self._dev_num in self._jc._output_bits:
                    bits = self._jc._output_bits[self._dev_num]
                    print(f'  长度: {len(bits)}')
                    # 统计 0 和 1 的数量
                    num_zeros = sum(1 for b in bits if b == 0)
                    num_ones = sum(1 for b in bits if b == 1)
                    print(f'  0 的数量: {num_zeros}, 1 的数量: {num_ones}')
                    # 显示前 50 位
                    sample = bits[:min(50, len(bits))]
                    print(f'  前 50 位: {sample}')
                    # 检查是否有交替的模式（0,1,0,1...）
                    if len(bits) > 1:
                        alternating = all(bits[i] != bits[i+1] for i in range(min(20, len(bits)-1)))
                        print(f'  是否交替模式 (0,1,0,1...): {alternating}')
                    
                    # 🎯 关键：打印 PS_MIO0 和 PS_MIO51 的具体值
                    for pin_name in ['PS_MIO0', 'PS_MIO51']:
                        reg = self._io_regs.get(pin_name, {})
                        if 'output' in reg and 'oe' in reg:
                            output_idx = reg['output']
                            oe_idx = reg['oe']
                            output_val = bits[output_idx] if output_idx < len(bits) else 'N/A'
                            oe_val = bits[oe_idx] if oe_idx < len(bits) else 'N/A'
                            print(f'  {pin_name}: output[{output_idx}]={output_val}, oe[{oe_idx}]={oe_val}')
                print('=== END DEBUG ===\n')
                
                # ✅ 关键修复：读取当前 BSR 状态并同步到 _output_bits
                # 这样可以确保后续 scan() 不会改变其他引脚的状态
                print('--- 步骤: 读取当前 BSR 状态并同步到 _output_bits ---')
                try:
                    # 执行一次 SAMPLE 模式的 scan() 来读取当前硬件状态
                    self._jc.set_scan_mode(self._dev_num, 'sample')
                    self._jc.scan()
                    
                    # 将读取到的 input 状态复制到 output_bits 中
                    # 这样后续 EXTEST 模式的 scan() 会保持这些值不变
                    if self._dev_num in self._jc._input_bits and self._dev_num in self._jc._output_bits:
                        input_bits = self._jc._input_bits[self._dev_num]
                        output_bits = self._jc._output_bits[self._dev_num]
                        
                        # 对于每个引脚，将其 input 状态复制到 output 和 oe 位置
                        for pin_name in self._pin_names:
                            reg = self._io_regs.get(pin_name, {})
                            if 'input' in reg and 'output' in reg:
                                input_cell = reg['input']
                                output_cell = reg['output']
                                oe_cell = reg.get('oe', None)
                                
                                if input_cell < len(input_bits):
                                    input_val = input_bits[input_cell]
                                    # 将 input 值复制到 output
                                    if output_cell < len(output_bits):
                                        output_bits[output_cell] = input_val
                                    # 将 oe 设为使能（如果存在）
                                    if oe_cell is not None and oe_cell < len(output_bits):
                                        # oe_disable=1 表示 oe=0 时使能输出，所以设置为 0
                                        oe_disable = reg.get('oe_disable', 1)
                                        output_bits[oe_cell] = 0 if oe_disable else 1
                        
                        print(f'  ✓ 已将 {len(self._pin_names)} 个引脚的 input 状态同步到 _output_bits')
                        
                        # 🎯 关键：打印同步后 PS_MIO0 和 PS_MIO51 的具体值
                        for pin_name in ['PS_MIO0', 'PS_MIO51']:
                            reg = self._io_regs.get(pin_name, {})
                            if 'output' in reg and 'oe' in reg:
                                output_idx = reg['output']
                                oe_idx = reg['oe']
                                output_val = output_bits[output_idx] if output_idx < len(output_bits) else 'N/A'
                                oe_val = output_bits[oe_idx] if oe_idx < len(output_bits) else 'N/A'
                                print(f'  [同步后] {pin_name}: output[{output_idx}]={output_val}, oe[{oe_idx}]={oe_val}')
                except Exception as e:
                    print(f'  ⚠️ 同步失败: {e}')
                
                # 切回 EXTEST 模式
                self._jc.set_scan_mode(self._dev_num, 'extest')
                
                self._last_scan_mode = 'extest'  # 记录当前模式
                self._status_var.set(f'已进入 EXTEST 模式 - 所有引脚已设为高阻态 (Z)')
                self._update_grid_mode_label()
            else:
                # 退出 EXTEST 模式，回到 SAMPLE
                if self._extest_mode:
                    self._extest_mode = False
                    self._pin_z_set.clear()
                self._jc.set_scan_mode(self._dev_num, 'sample')
                self._jc.scan()
                self._last_scan_mode = 'sample'  # 记录当前模式
                self._status_var.set('已进入 SAMPLE 模式')
                self._update_grid_mode_label()
        except Exception as e:
            messagebox.showerror('模式切换错误', str(e))

    def _on_sample(self):
        """执行一次 SAMPLE 扫描"""
        if not self._jc or self._dev_num is None:
            return

        try:
            t0 = time.perf_counter()
            self._jc.set_scan_mode(self._dev_num, 'sample')
            self._jc.scan()

            states = {}
            for name in self._pin_names:
                try:
                    states[name] = self._jc.get_pin_state(self._dev_num, name, 'input')
                except Exception:
                    states[name] = -1

            scan_ms = (time.perf_counter() - t0) * 1000
            self._scan_count += 1

            # 更新引脚表值列
            for name, val in states.items():
                self._tree.set(name, 'value', str(val))

            # 更新网格
            self._sample_grid.update_states(states)

            # 更新波形
            waveform_data = {n: {'input': v} for n, v in states.items()}
            self._waveform.append_samples(waveform_data)

            self._count_var.set(f'扫描: #{self._scan_count} ({scan_ms:.1f}ms)')

        except Exception as e:
            self._status_var.set(f'扫描错误: {e}')

    def _on_run_toggle(self):
        """Run/Stop 切换"""
        if not self._jc or self._dev_num is None:
            return

        if self._continuous_running:
            # Stop
            self._continuous_running = False
            self._run_btn.configure(text='Run', bootstyle=SUCCESS)
            self._status_var.set('已停止采集')
            # 如果之前在 EXTEST 模式，退出
            if self._extest_mode:
                self._extest_mode = False
                self._pin_z_set.clear()
                self._jc.set_scan_mode(self._dev_num, 'sample')
                self._jc.scan()
                self._mode_var.set('sample')
                self._last_scan_mode = 'sample'  # 重置模式记录
            self._update_grid_mode_label()
        else:
            # Run
            mode = self._mode_var.get().strip().lower()
            if mode == 'extest':
                self._extest_mode = True
                self._jc.set_scan_mode(self._dev_num, 'extest')
                
                # ⚠️ 重要：进入 EXTEST 后，先将所有引脚设为高阻态（Z）
                self._pin_z_set.clear()
                for pin_name in self._pin_names:
                    reg = self._io_regs.get(pin_name, {})
                    if 'output' in reg and 'oe' in reg:
                        try:
                            self._jc.set_pin_state(self._dev_num, pin_name, False, 'oe')
                            self._pin_z_set.add(pin_name)
                        except Exception:
                            pass
                
                # 执行一次扫描，将所有引脚设为高阻态
                self._jc.scan()
                
                self._last_scan_mode = 'extest'  # 记录当前模式
            else:
                self._extest_mode = False
                self._jc.set_scan_mode(self._dev_num, 'sample')
                self._jc.scan()
                self._last_scan_mode = 'sample'  # 记录当前模式

            interval_text = self._interval_var.get()
            interval_ms = self._parse_interval(interval_text)

            self._continuous_running = True
            self._run_btn.configure(text='Stop', bootstyle=DANGER)
            self._status_var.set(f'连续采集中... 模式:{mode} 间隔:{interval_text}')
            self._update_grid_mode_label()

            self._continuous_thread = threading.Thread(
                target=self._continuous_scan_loop, args=(interval_ms,), daemon=True
            )
            self._continuous_thread.start()

    def _continuous_scan_loop(self, interval_ms):
        """连续扫描线程"""
        while self._continuous_running:
            self._do_sample_scan()
            time.sleep(interval_ms / 1000.0)

    def _do_sample_scan(self):
        """执行一次扫描（线程安全，通过 after 更新 UI）"""
        if not self._jc or self._dev_num is None:
            return
        try:
            t0 = time.perf_counter()
            # 根据当前模式扫描
            scan_mode = 'extest' if self._extest_mode else 'sample'
            
            # EXTEST 模式下：只在首次或模式切换时设置 scan_mode
            # 避免每次扫描都重置，覆盖用户手动设置的引脚状态
            if not hasattr(self, '_last_scan_mode') or self._last_scan_mode != scan_mode:
                print(f'[DO_SAMPLE_SCAN] 切换模式: {self._last_scan_mode} -> {scan_mode}')
                self._jc.set_scan_mode(self._dev_num, scan_mode)
                self._last_scan_mode = scan_mode
            
            # ⚠️ 重要：EXTEST 模式下，不执行 scan()，避免将 _output_bits 中的值加载到 BSR
            # 只有用户手动 Set to 0/1/Z 时才执行 scan()
            if self._extest_mode:
                # EXTEST 模式：只读取当前状态，不执行 scan()
                # 这样可以避免 _output_bits 中的默认值驱动引脚
                print(f'[DO_SAMPLE_SCAN] EXTEST 模式，跳过 scan()')
                pass
            else:
                # SAMPLE 模式：正常执行 scan()
                print(f'[DO_SAMPLE_SCAN] SAMPLE 模式，执行 scan()')
                self._jc.scan()

            states = {}
            # EXTEST 模式下读取 output 状态（反映边界扫描寄存器中的输出值）
            # SAMPLE 模式下读取 input 状态
            pin_type = 'output' if self._extest_mode else 'input'
            
            for name in self._pin_names:
                if name in self._pin_z_set:
                    states[name] = 2  # Z 状态
                else:
                    try:
                        states[name] = self._jc.get_pin_state(self._dev_num, name, pin_type)
                    except Exception:
                        states[name] = -1

            scan_ms = (time.perf_counter() - t0) * 1000
            self._scan_count += 1

            # 通过 after 在主线程更新 UI
            self.root.after(0, self._update_ui_after_scan, states, scan_ms)

        except Exception as e:
            print(f'[DO_SAMPLE_SCAN] 异常: {e}')
            import traceback
            traceback.print_exc()
            pass

    def _update_ui_after_scan(self, states, scan_ms):
        """在主线程更新 UI"""
        for name, val in states.items():
            self._tree.set(name, 'value', str(val))
        self._sample_grid.update_states(states)
        
        # EXTEST 模式下使用 output 数据，SAMPLE 模式下使用 input 数据
        data_key = 'output' if self._extest_mode else 'input'
        waveform_data = {n: {data_key: v} for n, v in states.items()}
        self._waveform.append_samples(waveform_data)
        
        self._count_var.set(f'扫描: #{self._scan_count} ({scan_ms:.1f}ms)')

    def _update_grid_mode_label(self):
        """更新网格设备信息的模式标签"""
        try:
            info = self._sample_grid._device_info or {}
            mode_str = 'EXTEST' if self._extest_mode else 'SAMPLE'
            self._sample_grid.set_device_info({
                'name': info.get('name', ''),
                'idcode': info.get('idcode', 0),
                'pins': info.get('pins', 0),
                'mode': mode_str
            })
        except Exception:
            pass

    def _extest_set_pin(self, pin_name, mode):
        """EXTEST 模式下设置引脚状态: '0', '1', 'Z'"""
        if not self._jc or self._dev_num is None:
            messagebox.showwarning('提示', '请先连接设备')
            return
        
        # 如果不在 EXTEST 模式，自动切换
        if not self._extest_mode:
            self._extest_mode = True
            self._jc.set_scan_mode(self._dev_num, 'extest')
            self._mode_var.set('extest')
            self._update_grid_mode_label()
        
        dev = self._dev_num
        try:
            # 🔍 调试：打印设置前的 _output_bits 状态
            print(f'\n=== DEBUG: 设置 {pin_name} -> {mode} 前 ===')
            before_bits = None
            if dev in self._jc._output_bits:
                bits = self._jc._output_bits[dev]
                before_bits = bits[:]  # 复制一份
                num_ones = sum(1 for b in bits if b == 1)
                print(f'  _output_bits 中 1 的数量: {num_ones}/{len(bits)}')
                # 获取该引脚对应的 output 和 oe 位索引
                reg = self._io_regs.get(pin_name, {})
                if 'output' in reg and 'oe' in reg:
                    output_idx = reg['output']
                    oe_idx = reg['oe']
                    print(f'  {pin_name}: output[{output_idx}]={bits[output_idx] if output_idx < len(bits) else "N/A"}, oe[{oe_idx}]={bits[oe_idx] if oe_idx < len(bits) else "N/A"}')
            print('=== END DEBUG ===\n')
            
            if mode == 'Z':
                # 禁用输出使能 -> 高阻
                self._jc.set_pin_state(dev, pin_name, False, 'oe')
                self._pin_z_set.add(pin_name)
                val = 2  # Z 状态标记
            elif mode == '1':
                # 使能输出 + 输出高
                self._jc.set_pin_state(dev, pin_name, True, 'oe')
                self._jc.set_pin_state(dev, pin_name, True, 'output')
                self._pin_z_set.discard(pin_name)
                val = 1
            elif mode == '0':
                # 使能输出 + 输出低
                self._jc.set_pin_state(dev, pin_name, True, 'oe')
                self._jc.set_pin_state(dev, pin_name, False, 'output')
                self._pin_z_set.discard(pin_name)
                val = 0
            else:
                return

            # 🔍 调试：打印设置后的 _output_bits 状态，并找出变化的位
            print(f'=== DEBUG: 设置 {pin_name} -> {mode} 后 ===')
            if dev in self._jc._output_bits and before_bits:
                after_bits = self._jc._output_bits[dev]
                num_ones = sum(1 for b in after_bits if b == 1)
                print(f'  _output_bits 中 1 的数量: {num_ones}/{len(after_bits)}')
                
                # 找出所有变化的位
                changed_indices = []
                for i in range(min(len(before_bits), len(after_bits))):
                    if before_bits[i] != after_bits[i]:
                        changed_indices.append(i)
                
                if changed_indices:
                    print(f'  ⚠️ 发现 {len(changed_indices)} 个位发生变化: {changed_indices[:20]}...' if len(changed_indices) > 20 else f'  ⚠️ 发现 {len(changed_indices)} 个位发生变化: {changed_indices}')
                    # 显示每个变化的位的详细信息
                    for idx in changed_indices[:5]:  # 只显示前 5 个
                        print(f'    位 {idx}: {before_bits[idx]} -> {after_bits[idx]}')
                else:
                    print(f'  ✓ 没有其他位发生变化')
                
                # 获取该引脚对应的 output 和 oe 位索引
                reg = self._io_regs.get(pin_name, {})
                if 'output' in reg and 'oe' in reg:
                    output_idx = reg['output']
                    oe_idx = reg['oe']
                    print(f'  {pin_name}: output[{output_idx}]={after_bits[output_idx] if output_idx < len(after_bits) else "N/A"}, oe[{oe_idx}]={after_bits[oe_idx] if oe_idx < len(after_bits) else "N/A"}')
            print('=== END DEBUG ===\n')

            # 执行扫描，将设置应用到硬件
            print(f'>>> 即将执行 scan() 应用设置...')
            self._jc.scan()
            print(f'<<< scan() 完成')
            
            # 🔍 关键：检查 scan() 后的 _input_bits 状态
            if dev in self._jc._input_bits:
                input_bits = self._jc._input_bits[dev]
                reg = self._io_regs.get(pin_name, {})
                if 'input' in reg:
                    input_idx = reg['input']
                    input_val = input_bits[input_idx] if input_idx < len(input_bits) else 'N/A'
                    print(f'  [scan 后] {pin_name} 的 input 值: {input_val}')

            # 立即更新网格显示
            self._sample_grid.update_states({pin_name: val})
            
            # 更新引脚表值列
            display_val = 'Z' if mode == 'Z' else str(val)
            self._tree.set(pin_name, 'value', display_val)
            
            # 立即更新波形图（实时显示变化）
            waveform_data = {pin_name: {'output': val}}
            self._waveform.append_samples(waveform_data)

            self._status_var.set(f'EXTEST: {pin_name} -> {mode}')
            
        except Exception as e:
            messagebox.showerror('EXTEST 错误', f'设置 {pin_name} 失败: {e}')

    def _update_device_panel(self):
        """更新设备信息面板"""
        self._device_info_text.configure(state=NORMAL)
        self._device_info_text.delete('1.0', tk.END)

        num_devs = self._jc.get_number_devices()
        for i in range(num_devs):
            devid = self._jc.get_devid(i)
            part_num = (devid >> 12) & 0xFFFF
            version = (devid >> 28) & 0xF
            man_id = devid & 0xFFE

            # 设备类型标识
            if i == self._dev_num:
                try:
                    dev_name = self._jc.bsdl[i].get_name()
                except Exception:
                    dev_name = os.path.basename(self._bsdl_path)
                tag = 'SAMPLE'
                line = f'Dev{i+1} : {dev_name}  (IDCODE: 0x{devid:08X}, Part:0x{part_num:04X}, Ver:{version})  [{tag}]'
            else:
                line = f'Dev{i+1} : BYPASS  (IDCODE: 0x{devid:08X})'

            self._device_info_text.insert(tk.END, line + '\n')

        self._device_info_text.configure(state=DISABLED)

    def _on_change_bsdl(self):
        """手动选择/更改 BSDL 文件"""
        if not self._jc:
            messagebox.showinfo('提示', '请先连接设备')
            return

        bsdl_path = filedialog.askopenfilename(
            title='选择 BSDL 文件',
            initialdir=os.path.dirname(self._bsdl_path),
            filetypes=[('BSDL Files', '*.bsd *.bsdl'), ('All Files', '*.*')]
        )
        if not bsdl_path:
            return

        try:
            # 尝试匹配设备
            bf = BSDLFile(bsdl_path)
            idmask, fileidcode = bf.get_idcode()
            num_devs = self._jc.get_number_devices()
            new_dev = None
            for i in range(num_devs):
                if (fileidcode & idmask) == (self._jc.get_devid(i) & idmask):
                    new_dev = i
                    break

            if new_dev is None:
                messagebox.showwarning('BSDL 匹配', 'BSDL 文件 IDCODE 与链中任何设备不匹配，将强制附加到当前设备。')
                new_dev = self._dev_num

            # 如果在采集中，先停止
            if self._continuous_running:
                self._on_run_toggle()
            # 如果 EXTEST 模式，先退出
            if self._extest_mode:
                self._extest_mode = False
                self._pin_z_set.clear()
                self._jc.set_scan_mode(self._dev_num, 'sample')
                self._mode_var.set('sample')

            self._dev_num = new_dev
            self._bsdl_path = bsdl_path
            self._jc.bsdl_attach(bsdl_path, new_dev, force=True)

            # 重新加载引脚
            self._io_regs = self._jc.bsdl[new_dev].io_regs
            self._pin_names = sorted(self._io_regs.keys())
            self._pin_map = {}
            try:
                bsdldict = self._jc.bsdl[new_dev].bsdl
                mappings = bsdldict.get('device_package_pin_mappings', [])
                if mappings:
                    for pm in mappings[0].get('pin_map', []):
                        if ':' in pm:
                            parts = pm.split(':')
                            self._pin_map[parts[0]] = parts[1]
            except Exception:
                pass

            self._update_device_panel()
            self._load_pin_table()
            self._sample_grid.load_pins(self._pin_names, self._linked_pins)
            self._sample_grid.set_pin_locations(self._pin_map)

            dev_name = self._jc.bsdl[new_dev].get_name()
            self._sample_grid.set_device_info({
                'name': f'Dev{new_dev}: {dev_name}',
                'idcode': self._jc.get_devid(new_dev),
                'pins': len(self._pin_names),
                'mode': 'SAMPLE'
            })

            devid = self._jc.get_devid(new_dev)
            part_num = (devid >> 12) & 0xFFFF
            version = (devid >> 28) & 0xF
            self._device_var.set(
                f'设备: {num_devs} | 目标: Dev{new_dev} | '
                f'IDCODE: 0x{devid:08X} (Part:0x{part_num:04X} Ver:{version})')
            self._status_var.set(f'BSDL 已更新: {dev_name}, {len(self._pin_names)} 个引脚')

        except Exception as e:
            messagebox.showerror('BSDL 错误', str(e))

    def _on_search_changed(self):
        """搜索引脚"""
        text = self._search_var.get().strip().upper()
        for name in self._pin_names:
            if not text or text in name.upper():
                # 显示
                self._tree.reattach(name, '', 'end')
            else:
                # 隐藏（detach）
                try:
                    self._tree.detach(name)
                except tk.TclError:
                    pass

    def _on_tree_right_click(self, event):
        """引脚表右键菜单"""
        item = self._tree.identify_row(event.y)
        if item:
            self._tree.selection_set(item)
            pin_name = item

            menu = tk.Menu(self.root, tearoff=0)
            menu.add_command(label=f'添加 {pin_name} (input) 到波形',
                             command=lambda: self._waveform.add_signal(pin_name, 'input'))
            menu.add_command(label=f'添加 {pin_name} (output) 到波形',
                             command=lambda: self._waveform.add_signal(pin_name, 'output'))
            menu.add_separator()
            menu.add_command(label=f'从波形移除 {pin_name}',
                             command=lambda: self._waveform.remove_signal(pin_name))

            # EXTEST 模式下添加引脚设置选项
            if self._extest_mode:
                menu.add_separator()
                extest_menu = tk.Menu(menu, tearoff=0)
                extest_menu.add_command(
                    label='Set to 0 (输出低)',
                    command=lambda: self._extest_set_pin(pin_name, '0'))
                extest_menu.add_command(
                    label='Set to 1 (输出高)',
                    command=lambda: self._extest_set_pin(pin_name, '1'))
                extest_menu.add_command(
                    label='Set to Z (高阻)',
                    command=lambda: self._extest_set_pin(pin_name, 'Z'))
                menu.add_cascade(label=f'EXTEST 设置 {pin_name}', menu=extest_menu)

            menu.post(event.x_root, event.y_root)
        else:
            menu = tk.Menu(self.root, tearoff=0)
            menu.add_command(label='清除所有波形', command=self._waveform.clear_all)
            menu.add_command(label='清除波形数据', command=self._waveform.clear_data)
            menu.post(event.x_root, event.y_root)

    def _parse_interval(self, text):
        """解析间隔字符串为毫秒"""
        text = text.strip().lower()
        if text.endswith('ms'):
            return int(text[:-2])
        elif text.endswith('s'):
            return int(float(text[:-1]) * 1000)
        return 100

    def on_close(self):
        """关闭清理"""
        self._continuous_running = False
        # 如果在 EXTEST 模式，先退出
        if self._extest_mode:
            try:
                self._extest_mode = False
                self._jc.set_scan_mode(self._dev_num, 'sample')
                self._jc.scan()
            except Exception:
                pass
        if self._jc:
            try:
                self._jc.jlink.close()
            except Exception:
                pass
        self.root.destroy()
