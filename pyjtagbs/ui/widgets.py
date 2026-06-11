"""JTAG 边界扫描 UI 自定义组件 - tkinter/ttkbootstrap 版本"""
import math
import tkinter as tk
from tkinter import ttk


class SampleGrid(tk.Canvas):
    """SAMPLE 网格组件 - 以网格形式显示所有引脚状态"""

    CELL_SIZE = 14
    CELL_GAP = 1
    COLOR_LOW = '#333333'      # 0 - 深色
    COLOR_HIGH = '#2196F3'     # 1 - 蓝色
    COLOR_Z = '#FF9800'        # Z - 橙色（高阻）
    COLOR_UNKNOWN = '#F44336'  # 未知 - 红色

    def __init__(self, parent, **kwargs):
        kwargs.setdefault('bg', '#FAFAFA')
        kwargs.setdefault('highlightthickness', 0)
        super().__init__(parent, **kwargs)

        self._pin_names = []
        self._pin_states = {}
        self._cols = 20
        self._rect_ids = {}  # pin_name -> canvas rect id
        self._tooltip = None

        self.bind('<Motion>', self._on_motion)
        self.bind('<Leave>', self._on_leave)

    def load_pins(self, pin_names):
        """设置引脚名称列表"""
        self._pin_names = list(pin_names)
        self._pin_states = {n: -1 for n in pin_names}
        n = len(pin_names)
        self._cols = max(1, math.ceil(math.sqrt(n)))
        self._draw_grid()

    def update_states(self, pin_states):
        """更新引脚状态"""
        self._pin_states.update(pin_states)
        self._update_colors()

    def _draw_grid(self):
        """绘制网格"""
        self.delete('all')
        cs = self.CELL_SIZE
        gap = self.CELL_GAP
        margin = 8
        cols = self._cols

        # 标题
        self.create_text(margin, margin, text='SAMPLE', anchor='nw',
                         font=('Segoe UI', 10, 'bold'))

        y_offset = margin + 18
        self._rect_ids = {}

        for idx, name in enumerate(self._pin_names):
            col = idx % cols
            row = idx // cols
            x = margin + col * (cs + gap)
            y = y_offset + row * (cs + gap)

            state = self._pin_states.get(name, -1)
            color = self._get_color(state)

            rect_id = self.create_rectangle(x, y, x + cs, y + cs,
                                            fill=color, outline='#BDBDBD', width=1)
            self._rect_ids[name] = (rect_id, x, y)

    def _update_colors(self):
        """更新所有矩形颜色"""
        for name, (rect_id, _, _) in self._rect_ids.items():
            state = self._pin_states.get(name, -1)
            color = self._get_color(state)
            self.itemconfigure(rect_id, fill=color)

    def _get_color(self, state):
        """根据状态返回颜色"""
        if state == 0:
            return self.COLOR_LOW
        elif state == 1:
            return self.COLOR_HIGH
        elif state == 2:  # Z 高阻
            return self.COLOR_Z
        return self.COLOR_UNKNOWN

    def _on_motion(self, event):
        """鼠标悬停显示 tooltip"""
        cs = self.CELL_SIZE
        gap = self.CELL_GAP
        margin = 8
        y_offset = margin + 18
        cols = self._cols

        col = int((event.x - margin) / (cs + gap))
        row = int((event.y - y_offset) / (cs + gap))

        if 0 <= col < cols and row >= 0:
            idx = row * cols + col
            if 0 <= idx < len(self._pin_names):
                name = self._pin_names[idx]
                state = self._pin_states.get(name, -1)
                state_str = {0: '0', 1: '1', 2: 'Z'}.get(state, '?')
                self._show_tooltip(event.x_root, event.y_root,
                                   f'{name} = {state_str}')
                # 高亮当前矩形
                for n, (rid, _, _) in self._rect_ids.items():
                    if n == name:
                        self.itemconfigure(rid, outline='#FF9800', width=2)
                    else:
                        self.itemconfigure(rid, outline='#BDBDBD', width=1)
                return

        self._hide_tooltip()
        for n, (rid, _, _) in self._rect_ids.items():
            self.itemconfigure(rid, outline='#BDBDBD', width=1)

    def _on_leave(self, event):
        """鼠标离开"""
        self._hide_tooltip()
        for n, (rid, _, _) in self._rect_ids.items():
            self.itemconfigure(rid, outline='#BDBDBD', width=1)

    def _show_tooltip(self, x, y, text):
        """显示 tooltip"""
        if self._tooltip:
            self._tooltip.withdraw()
        else:
            self._tooltip = tk.Toplevel(self)
            self._tooltip.overrideredirect(True)
            self._tooltip.configure(bg='#333333')
            self._label = tk.Label(self._tooltip, bg='#333333', fg='white',
                                   font=('Segoe UI', 9), padx=4, pady=2)
            self._label.pack()

        self._label.configure(text=text)
        self._tooltip.geometry(f'+{x + 15}+{y + 10}')
        self._tooltip.deiconify()
        self._tooltip.lift()

    def _hide_tooltip(self):
        """隐藏 tooltip"""
        if self._tooltip:
            self._tooltip.withdraw()


class WaveformCanvas(tk.Canvas):
    """波形查看器 - 显示选中引脚的时序波形"""

    TRACK_HEIGHT = 30
    TIME_SCALE_PX = 60
    MARGIN_LEFT = 120
    MARGIN_TOP = 30

    def __init__(self, parent, **kwargs):
        kwargs.setdefault('bg', '#FFFDE7')  # 浅黄色背景
        kwargs.setdefault('highlightthickness', 0)
        super().__init__(parent, **kwargs)

        self._signals = []  # [(name, sig_type, [values]), ...]
        self._max_samples = 0

    def add_signal(self, name, sig_type='input'):
        """添加信号通道"""
        for s in self._signals:
            if s[0] == name and s[1] == sig_type:
                return
        self._signals.append([name, sig_type, []])
        self._redraw()

    def remove_signal(self, name, sig_type=None):
        """移除信号"""
        if sig_type:
            self._signals = [s for s in self._signals
                             if not (s[0] == name and s[1] == sig_type)]
        else:
            self._signals = [s for s in self._signals if s[0] != name]
        self._redraw()

    def append_samples(self, pin_samples):
        """追加采样数据"""
        for sig in self._signals:
            name, sig_type, values = sig
            if name in pin_samples:
                sample = pin_samples[name]
                if isinstance(sample, dict):
                    val = sample.get(sig_type, -1)
                else:
                    val = sample
                values.append(val)
            else:
                values.append(-1)

        self._max_samples = max((len(s[2]) for s in self._signals), default=0)
        self._redraw()

    def clear_data(self):
        """清除所有采样数据"""
        for sig in self._signals:
            sig[2] = []
        self._max_samples = 0
        self._redraw()

    def clear_all(self):
        """清除所有信号"""
        self._signals = []
        self._max_samples = 0
        self._redraw()

    def _redraw(self):
        """重绘波形"""
        self.delete('all')
        w = self.winfo_width() or 800
        th = self.TRACK_HEIGHT
        ts = self.TIME_SCALE_PX
        ml = self.MARGIN_LEFT
        mt = self.MARGIN_TOP

        # 标题
        self.create_text(10, 15, text='Waveform', anchor='nw',
                         font=('Segoe UI', 10, 'bold'))

        if not self._signals:
            self.create_text(ml, mt + 20, text='（右键引脚表添加信号）',
                             anchor='nw', fill='#999999', font=('Segoe UI', 9))
            return

        # 时间刻度
        max_visible = max(1, (w - ml) // ts)
        start_idx = max(0, self._max_samples - max_visible)

        for t in range(start_idx, self._max_samples + 1):
            x = ml + (t - start_idx) * ts
            if x > w:
                break
            self.create_line(x, mt - 5, x, mt + len(self._signals) * th,
                             fill='#E0E0E0')
            self.create_text(x + 2, mt - 10, text=str(t + 1),
                             anchor='nw', fill='#999999', font=('Segoe UI', 8))

        # 绘制每个信号
        for ch, sig in enumerate(self._signals):
            name, sig_type, values = sig
            y_base = mt + ch * th
            y_mid = y_base + th // 2
            y_high = y_base + 4
            y_low = y_base + th - 4

            # 标签
            label = f'{name} ({sig_type})'
            self.create_text(4, y_mid, text=label, anchor='w',
                             fill='#333333', font=('Segoe UI', 8))

            # 分隔线
            self.create_line(0, y_base + th, w, y_base + th, fill='#E0E0E0')

            if not values:
                continue

            # 波形颜色
            color = '#2196F3' if sig_type == 'input' else '#F44336'
            z_color = '#FF9800'  # Z状态用橙色

            prev_x = None
            prev_y = None
            prev_val = None
            for t_idx in range(start_idx, min(len(values), start_idx + max_visible + 1)):
                x = ml + (t_idx - start_idx) * ts
                val = values[t_idx]

                if val == 1:
                    y = y_high
                elif val == 0:
                    y = y_low
                elif val == 2:  # Z 高阻 - 半幅位置
                    y = (y_high + y_mid) // 2
                else:
                    y = y_mid

                # Z状态用虚线，其他用实线
                is_z = (val == 2)
                line_color = z_color if is_z else color
                dash = (4, 4) if is_z else None

                if prev_x is not None:
                    mid_x = prev_x + ts // 2
                    prev_is_z = (prev_val == 2)
                    prev_line_color = z_color if prev_is_z else color
                    prev_dash = (4, 4) if prev_is_z else None

                    self.create_line(prev_x, prev_y, mid_x, prev_y,
                                     fill=prev_line_color, width=2, dash=prev_dash)
                    if prev_y != y:
                        self.create_line(mid_x, prev_y, mid_x, y,
                                         fill=line_color, width=2)
                    self.create_line(mid_x, y, x, y,
                                     fill=line_color, width=2, dash=dash)
                prev_x = x
                prev_y = y
                prev_val = val
