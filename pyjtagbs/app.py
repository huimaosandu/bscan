"""BScanner - JTAG 边界扫描 GUI 应用入口"""
import sys
import os

# 确保项目路径在 sys.path 中
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.normpath(os.path.join(script_dir, '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import ttkbootstrap as ttkb
from ui.main_window import MainWindow


def main():
    root = ttkb.Window(
        title='BScanner - JTAG 边界扫描工具',
        themename='cosmo',  # 现代风格主题
        size=(1200, 800),
        minsize=(900, 600),
    )

    app = MainWindow(root)
    root.protocol('WM_DELETE_WINDOW', app.on_close)
    root.mainloop()


if __name__ == '__main__':
    main()
