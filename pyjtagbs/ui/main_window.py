"""JTAG 边界扫描主窗口 - tkinter/ttkbootstrap 版本

# ============================================================
# 修改记录
# 修改人: claude code
# 修改日期: 2025-06-12
# 修改内容:
#   1. [Bug Fix] _on_mode_changed() - 进入 EXTEST 模式时，改为调用
#      jc.capture_current_state() 先同步当前硬件电平到 _output_bits，
#      移除了之前手动遍历引脚设置 OE 的错误逻辑（oe_disable 极性判断反向）。
#      移除了大量调试 print 语句。
#   2. [Bug Fix] _on_run_toggle() - 同上，进入 EXTEST 时改用
#      capture_current_state() 替代手动遍历设置高阻的旧逻辑。
#   3. [Bug Fix] _do_sample_scan() - EXTEST 模式下之前完全跳过 scan()，
#      导致用户 Set to 0/1/Z 的操作只写入了 _output_bits 内存，
#      但 BSR 从未被实际推送到硬件。修复为始终执行 scan()；
#      由于 pylinkbs.py 中已做 IR 缓存，不会重复写 IR，TAP 不会跑飞。
#   4. [Bug Fix] _extest_set_pin() - 移除所有调试 print；修复自动切换
#      EXTEST 模式的逻辑（改为触发 _mode_var.set 走统一切换流程）；
#      scan() 调用保留，依赖 pylinkbs.py 的 IR 缓存保证安全性。
#
# 修改日期: 2025-06-12（第二次）
# 修改内容:
#   5. [Bug Fix] 线程竞争导致 Set to 0/1/Z 无反应：
#      __init__ 中新增 self._jtag_lock (threading.Lock) 和
#      self._extest_pin_states (dict)。
#      _extest_set_pin() 和 _do_sample_scan() 共用同一把锁，
#      确保主线程写 _output_bits 和后台扫描线程推送 BSR 不会交叉执行。
#   6. [Bug Fix] 连续扫描 UI 刷新覆盖手动设置的显示值：
#      _update_ui_after_scan() 在 EXTEST 模式下，用 _extest_pin_states
#      覆盖扫描读回值，保证用户手动设置的引脚显示值不被刷新覆盖。
#   7. [Bug Fix] _extest_set_pin() 在连续扫描运行时不再自己调 scan()，
#      由扫描线程统一在获得锁后推送，避免双重 scan() 竞争。
#      仅在无连续扫描时才在持锁内自己 scan()。
#   8. 所有 EXTEST 退出路径（_on_mode_changed/_on_run_toggle/
#      _on_change_bsdl）均加入 _extest_pin_states.clear()。
# ============================================================
"""
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
        self._extest_pin_states = {}   # 用户手动设置的引脚显示值 {name: val}
        self._jtag_lock = threading.Lock()  # 保护 JTAG 操作的互斥锁

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
            print(f'[DEBUG _on_mode_changed] 连续扫描运行中，跳过模式切换')
            return

        mode = self._mode_var.get().strip().lower()
        print(f'\n[DEBUG _on_mode_changed] >>> 模式切换: {mode}')
        try:
            if mode == 'extest':
                print(f'  - 进入 EXTEST 模式...')
                # 进入 EXTEST 前，先捕获当前引脚状态并同步到 _output_bits
                # 这样可防止进入 EXTEST 瞬间因 _output_bits 全 0 导致所有引脚跳变
                print(f'  - 调用 capture_current_state()...')
                self._jc.capture_current_state(self._dev_num)
                print(f'  - capture_current_state() 完成')

                self._extest_mode = True
                self._pin_z_set.clear()
                print(f'  - 设置 scan_mode 为 extest...')
                self._jc.set_scan_mode(self._dev_num, 'extest')

                # 执行首次 EXTEST scan()，将同步好的状态推送到硬件
                print(f'  - 执行首次 EXTEST scan()...')
                self._jc.scan()
                print(f'  - 首次 scan() 完成')

                self._last_scan_mode = 'extest'
                self._status_var.set('已进入 EXTEST 模式 - 所有引脚保持当前状态（高阻）')
                self._update_grid_mode_label()
                print(f'  - [OK] 已进入 EXTEST 模式')
            else:
                print(f'  - 退出 EXTEST 模式，回到 SAMPLE...')
                # 退出 EXTEST 模式，回到 SAMPLE
                if self._extest_mode:
                    self._extest_mode = False
                    self._pin_z_set.clear()
                    self._extest_pin_states.clear()
                print(f'  - 设置 scan_mode 为 sample...')
                self._jc.set_scan_mode(self._dev_num, 'sample')
                print(f'  - 执行 SAMPLE scan()...')
                self._jc.scan()
                print(f'  - SAMPLE scan() 完成')
                self._last_scan_mode = 'sample'
                self._status_var.set('已进入 SAMPLE 模式')
                self._update_grid_mode_label()
                print(f'  - [OK] 已进入 SAMPLE 模式')
            print(f'[DEBUG _on_mode_changed] <<< 模式切换完成\n')
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
                self._extest_pin_states.clear()
                self._jc.set_scan_mode(self._dev_num, 'sample')
                self._jc.scan()
                self._mode_var.set('sample')
                self._last_scan_mode = 'sample'  # 重置模式记录
            self._update_grid_mode_label()
        else:
            # Run
            mode = self._mode_var.get().strip().lower()
            if mode == 'extest':
                # 进入 EXTEST 前，先捕获当前引脚状态并同步到 _output_bits
                self._jc.capture_current_state(self._dev_num)

                self._extest_mode = True
                self._pin_z_set.clear()
                self._jc.set_scan_mode(self._dev_num, 'extest')
                self._jc.scan()
                self._last_scan_mode = 'extest'
            else:
                self._extest_mode = False
                self._jc.set_scan_mode(self._dev_num, 'sample')
                self._jc.scan()
                self._last_scan_mode = 'sample'

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
        print(f'\n[DEBUG _continuous_scan_loop] >>> 连续扫描线程启动, interval={interval_ms}ms')
        loop_count = 0
        while self._continuous_running:
            loop_count += 1
            # print(f'[DEBUG _continuous_scan_loop] 第 {loop_count} 次循环')
            try:
                self._do_sample_scan()
            except Exception as e:
                print(f'[DEBUG _continuous_scan_loop] ⚠️ _do_sample_scan() 异常: {e}')
                import traceback
                traceback.print_exc()
            time.sleep(interval_ms / 1000.0)
        print(f'[DEBUG _continuous_scan_loop] <<< 连续扫描线程退出, 共执行 {loop_count} 次循环\n')

    def _do_sample_scan(self):
        """执行一次扫描（线程安全，通过 after 更新 UI）"""
        if not self._jc or self._dev_num is None:
            return
        try:
            t0 = time.perf_counter()
            scan_mode = 'extest' if self._extest_mode else 'sample'

            print(f'[DEBUG _do_sample_scan] >>> 开始扫描, mode={scan_mode}, extest_mode={self._extest_mode}')

            # 用锁保护：防止与主线程 _extest_set_pin 同时操作 JTAG 硬件
            with self._jtag_lock:
                print(f'[DEBUG _do_sample_scan] [LOCKED] 获取锁')
                if not hasattr(self, '_last_scan_mode') or self._last_scan_mode != scan_mode:
                    print(f'[DEBUG _do_sample_scan] 切换模式: {self._last_scan_mode} -> {scan_mode}')
                    self._jc.set_scan_mode(self._dev_num, scan_mode)
                    self._last_scan_mode = scan_mode
                else:
                    print(f'[DEBUG _do_sample_scan] 模式未变化: {scan_mode}')

                print(f'[DEBUG _do_sample_scan] 执行 scan()...')
                self._jc.scan()
                print(f'[DEBUG _do_sample_scan] scan() 完成')
            
            print(f'[DEBUG _do_sample_scan] [UNLOCKED] 释放锁')

            states = {}
            pin_type = 'output' if self._extest_mode else 'input'

            # 🔍 调试：打印第一个和最后一个引脚的状态
            debug_pins = ['PS_MIO0', 'PS_MIO51']
            print(f'[DEBUG _do_sample_scan] 读取引脚状态 (pin_type={pin_type}):')
            
            for name in self._pin_names:
                if name in self._pin_z_set:
                    states[name] = 2
                else:
                    try:
                        val = self._jc.get_pin_state(self._dev_num, name, pin_type)
                        states[name] = val
                        # 打印调试引脚的状态
                        if name in debug_pins:
                            reg = self._io_regs.get(name, {})
                            if 'output' in reg and 'oe' in reg:
                                output_idx = reg['output']
                                oe_idx = reg['oe']
                                output_val = self._jc._output_bits[self._dev_num][output_idx] if self._dev_num in self._jc._output_bits and output_idx < len(self._jc._output_bits[self._dev_num]) else 'N/A'
                                oe_val = self._jc._output_bits[self._dev_num][oe_idx] if self._dev_num in self._jc._output_bits and oe_idx < len(self._jc._output_bits[self._dev_num]) else 'N/A'
                                print(f'  {name}: get_pin_state={val}, _output_bits[output]={output_val}, _output_bits[oe]={oe_val}')
                    except Exception:
                        states[name] = -1

            scan_ms = (time.perf_counter() - t0) * 1000
            self._scan_count += 1

            self.root.after(0, self._update_ui_after_scan, states, scan_ms)

        except Exception as e:
            import traceback
            traceback.print_exc()

    def _update_ui_after_scan(self, states, scan_ms):
        """在主线程更新 UI"""
        # EXTEST 模式下，用户手动 Set to 0/1/Z 的引脚，显示值以 _extest_pin_states 为准，
        # 不被连续扫描读回的 _output_bits 值覆盖（_output_bits 读回值理论上应一致，
        # 但可因 linkage/input-only 引脚等情况产生干扰）
        if self._extest_mode and self._extest_pin_states:
            states = dict(states)
            states.update(self._extest_pin_states)

        for name, val in states.items():
            display = 'Z' if val == 2 else str(val)
            self._tree.set(name, 'value', display)
        self._sample_grid.update_states(states)

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

        print(f'\n[DEBUG _extest_set_pin] >>> 开始设置 {pin_name} -> {mode}')
        print(f'  - _extest_mode: {self._extest_mode}')
        print(f'  - _continuous_running: {self._continuous_running}')
        
        # 如果不在 EXTEST 模式，自动切换（会先捕获当前状态）
        if not self._extest_mode:
            print(f'  - 不在 EXTEST 模式，触发自动切换...')
            self._mode_var.set('extest')
            print(f'  - 已触发模式切换，返回等待用户再次操作')
            return  # _on_mode_changed 会处理切换；用户再次右键选择即可

        dev = self._dev_num
        try:
            print(f'  - 设备号: {dev}')
            print(f'  - 获取锁...')
            # 用锁保护：防止连续扫描线程与此处同时操作 _output_bits / JTAG 硬件
            with self._jtag_lock:
                print(f'  - [LOCKED] 已获取锁')
                
                # 🔍 打印设置前的 _output_bits 状态
                if dev in self._jc._output_bits:
                    bits = self._jc._output_bits[dev]
                    reg = self._io_regs.get(pin_name, {})
                    if 'output' in reg and 'oe' in reg:
                        output_idx = reg['output']
                        oe_idx = reg['oe']
                        oe_disable = reg.get('oe_disable', 1)
                        print(f'  - [BEFORE] {pin_name}: output[{output_idx}]={bits[output_idx] if output_idx < len(bits) else "N/A"}, oe[{oe_idx}]={bits[oe_idx] if oe_idx < len(bits) else "N/A"}')
                        print(f'  - [CONFIG] {pin_name}: oe_disable={oe_disable} (0=active-high, 1=active-low)')
                
                if mode == 'Z':
                    print(f'  - [ACTION] 设置 {pin_name} OE=False (高阻)')
                    self._jc.set_pin_state(dev, pin_name, False, 'oe')
                    self._pin_z_set.add(pin_name)
                    val = 2
                elif mode == '1':
                    print(f'  - [ACTION] 设置 {pin_name} OE=True, output=True')
                    self._jc.set_pin_state(dev, pin_name, True, 'oe')
                    self._jc.set_pin_state(dev, pin_name, True, 'output')
                    self._pin_z_set.discard(pin_name)
                    val = 1
                elif mode == '0':
                    print(f'  - [ACTION] 设置 {pin_name} OE=True, output=False')
                    self._jc.set_pin_state(dev, pin_name, True, 'oe')
                    self._jc.set_pin_state(dev, pin_name, False, 'output')
                    self._pin_z_set.discard(pin_name)
                    val = 0
                else:
                    print(f'  - [ERROR] 无效模式: {mode}')
                    return
                
                # 🔍 打印设置后的 _output_bits 状态
                if dev in self._jc._output_bits:
                    bits = self._jc._output_bits[dev]
                    reg = self._io_regs.get(pin_name, {})
                    if 'output' in reg and 'oe' in reg:
                        output_idx = reg['output']
                        oe_idx = reg['oe']
                        print(f'  - [AFTER] {pin_name}: output[{output_idx}]={bits[output_idx] if output_idx < len(bits) else "N/A"}, oe[{oe_idx}]={bits[oe_idx] if oe_idx < len(bits) else "N/A"}')

                # 记录用户手动设置的显示值，防止被连续扫描的 UI 刷新覆盖
                self._extest_pin_states[pin_name] = val
                print(f'  - 已记录 _extest_pin_states[{pin_name}] = {val}')

                # 如果没有连续扫描线程在跑，需要自己触发一次 scan() 推送到硬件
                # 如果有连续扫描线程，它会在下一个周期自动推送（已持锁，线程会等锁释放后推送）
                if not self._continuous_running:
                    print(f'  - [SCAN] 无连续扫描，立即执行 scan() 推送到硬件')
                    
                    # 🔍 关键修复：强制重新加载 EXTEST 指令
                    print(f'  - [SCAN] 强制重置 IR 缓存，重新加载 EXTEST 指令')
                    self._jc._last_ir_opcode = None
                    self._jc.scan()
                    print(f'  - [SCAN] scan() 完成')
                    
                    # 🔍 立即读取验证
                    try:
                        verify_output = self._jc.get_pin_state(dev, pin_name, 'output')
                        verify_oe = self._jc.get_pin_state(dev, pin_name, 'oe')
                        print(f'  - [VERIFY] 立即读取: output={verify_output}, oe={verify_oe}')
                    except Exception as e:
                        print(f'  - [VERIFY] 读取失败: {e}')
                else:
                    print(f'  - [SCAN] 有连续扫描运行，等待下一周期自动推送')
                    # 🔍 关键修复：即使有连续扫描，也强制重新加载 EXTEST 指令
                    print(f'  - [SCAN] 强制重置 IR 缓存，确保下一周期使用正确的指令')
                    self._jc._last_ir_opcode = None
            
            print(f'  - [UNLOCKED] 已释放锁')

            # 更新 UI（在锁外执行，避免长时间持锁）
            print(f'  - [UI] 更新网格显示: {pin_name} -> {val}')
            self._sample_grid.update_states({pin_name: val})
            display_val = 'Z' if mode == 'Z' else str(val)
            print(f'  - [UI] 更新引脚表: {pin_name} -> {display_val}')
            self._tree.set(pin_name, 'value', display_val)
            print(f'  - [UI] 更新波形图')
            self._waveform.append_samples({pin_name: {'output': val}})
            print(f'  - [STATUS] 设置状态栏文本')
            self._status_var.set(f'EXTEST: {pin_name} -> {mode}')
            print(f'[DEBUG _extest_set_pin] <<< 设置完成\n')

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
                self._extest_pin_states.clear()
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
        print(f'\n[DEBUG _on_tree_right_click] 右键点击: y={event.y}, item={item}')
        if item:
            self._tree.selection_set(item)
            pin_name = item
            print(f'[DEBUG _on_tree_right_click] 选中引脚: {pin_name}')
            print(f'[DEBUG _on_tree_right_click] _extest_mode: {self._extest_mode}')

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
                print(f'[DEBUG _on_tree_right_click] 添加 EXTEST 菜单项')
                menu.add_separator()
                        
                # 🔍 测试：直接调用，不使用 partial 或 lambda
                def test_callback_0():
                    print(f'[DEBUG TEST CALLBACK] 直接调用 _extest_set_pin({pin_name}, "0")')
                    try:
                        self._extest_set_pin(pin_name, '0')
                        print(f'[DEBUG TEST CALLBACK] 调用成功')
                    except Exception as e:
                        print(f'[DEBUG TEST CALLBACK] 调用失败: {e}')
                        import traceback
                        traceback.print_exc()
                        
                def test_callback_1():
                    print(f'[DEBUG TEST CALLBACK] 直接调用 _extest_set_pin({pin_name}, "1")')
                    try:
                        self._extest_set_pin(pin_name, '1')
                        print(f'[DEBUG TEST CALLBACK] 调用成功')
                    except Exception as e:
                        print(f'[DEBUG TEST CALLBACK] 调用失败: {e}')
                        import traceback
                        traceback.print_exc()
                        
                def test_callback_z():
                    print(f'[DEBUG TEST CALLBACK] 直接调用 _extest_set_pin({pin_name}, "Z")')
                    try:
                        self._extest_set_pin(pin_name, 'Z')
                        print(f'[DEBUG TEST CALLBACK] 调用成功')
                    except Exception as e:
                        print(f'[DEBUG TEST CALLBACK] 调用失败: {e}')
                        import traceback
                        traceback.print_exc()
                        
                menu.add_command(label=f'TEST: Set {pin_name} to 0 (输出低)', command=test_callback_0)
                menu.add_command(label=f'TEST: Set {pin_name} to 1 (输出高)', command=test_callback_1)
                menu.add_command(label=f'TEST: Set {pin_name} to Z (高阻)', command=test_callback_z)
                        
                print(f'[DEBUG _on_tree_right_click] EXTEST 菜单项已添加')

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
