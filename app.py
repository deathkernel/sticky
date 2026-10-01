import json
import sys
from pathlib import Path
from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QCheckBox, QFrame, QSystemTrayIcon, QMenu

DATA = Path.home() / ".sticky_todo.json"

class TodoWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.tasks = self.load()
        self.drag_pos = None
        self.setWindowTitle("Sticky Todo")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(360, 430)
        self.build()
        self.render()

    def load(self):
        try:
            return json.loads(DATA.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def save(self):
        DATA.write_text(json.dumps(self.tasks, indent=2, ensure_ascii=False), encoding="utf-8")

    def build(self):
        self.card = QFrame()
        self.card.setObjectName("card")
        self.card.setStyleSheet("""
            #card { background:#17191f; border:1px solid #343842; border-radius:16px; }
            QLabel { color:#f5f7fb; }
            QLineEdit { background:#22252d; color:#fff; border:1px solid #363a45; border-radius:9px; padding:9px; }
            QPushButton { background:#292d36; color:#fff; border:0; border-radius:9px; padding:8px 11px; }
            QPushButton:hover { background:#383d48; }
            QCheckBox { color:#e7e9ee; padding:7px; }
        """)
        outer = QVBoxLayout(self); outer.setContentsMargins(8,8,8,8); outer.addWidget(self.card)
        root = QVBoxLayout(self.card); root.setContentsMargins(18,16,18,18); root.setSpacing(10)

        top = QHBoxLayout()
        title = QLabel("✓  STICKY TODO"); title.setStyleSheet("font-size:16px;font-weight:700;")
        self.count = QLabel()
        close = QPushButton("×"); close.setFixedWidth(30); close.clicked.connect(self.hide)
        top.addWidget(title); top.addStretch(); top.addWidget(self.count); top.addWidget(close)
        root.addLayout(top)

        add = QHBoxLayout()
        self.input = QLineEdit(); self.input.setPlaceholderText("Add a task…"); self.input.returnPressed.connect(self.add_task)
        btn = QPushButton("+"); btn.setFixedWidth(38); btn.clicked.connect(self.add_task)
        add.addWidget(self.input); add.addWidget(btn); root.addLayout(add)

        self.list_layout = QVBoxLayout(); self.list_layout.setSpacing(2); root.addLayout(self.list_layout)
        root.addStretch()

    def render(self):
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        done = sum(t["done"] for t in self.tasks)
        self.count.setText(f"{done}/{len(self.tasks)}")
        for i, task in enumerate(self.tasks):
            row = QHBoxLayout()
            box = QCheckBox(task["text"]); box.setChecked(task["done"])
            box.stateChanged.connect(lambda state, idx=i: self.toggle(idx, state))
            delete = QPushButton("×"); delete.setFixedWidth(28)
            delete.clicked.connect(lambda _, idx=i: self.remove(idx))
            row.addWidget(box); row.addStretch(); row.addWidget(delete)
            self.list_layout.addLayout(row)

    def add_task(self):
        text = self.input.text().strip()
        if not text: return
        self.tasks.append({"text": text, "done": False})
        self.input.clear(); self.save(); self.render()

    def toggle(self, idx, state):
        if idx < len(self.tasks):
            self.tasks[idx]["done"] = bool(state); self.save(); self.render()

    def remove(self, idx):
        if idx < len(self.tasks):
            self.tasks.pop(idx); self.save(); self.render()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_pos = None

app = QApplication(sys.argv)
app.setQuitOnLastWindowClosed(False)
window = TodoWindow()
window.show()
tray = QSystemTrayIcon(window)
tray.setToolTip("Sticky Todo")
menu = QMenu()
show = menu.addAction("Show")
show.triggered.connect(window.show)
quit_action = menu.addAction("Quit")
quit_action.triggered.connect(app.quit)
tray.setContextMenu(menu)
tray.show()
sys.exit(app.exec())
