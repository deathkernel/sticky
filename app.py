import ctypes
import json
import signal
import sys
from datetime import datetime, date
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QDate, QTime, QSettings, QTimer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDateEdit, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMenu, QPushButton, QSlider, QSystemTrayIcon,
    QTimeEdit, QVBoxLayout, QWidget,
)

try:
    from winotify import Notification
except ImportError:
    Notification = None

DATA = Path.home() / ".sticky_todo.json"
SETTINGS = QSettings("DeathKernel", "StickyTodo")

user32 = ctypes.windll.user32
DESKTOP_CLASSES = {"Progman", "WorkerW"}


def window_class(hwnd):
    if not hwnd:
        return ""
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


class TodoWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.tasks = self.load()
        self.drag_pos = None
        self.desktop_only = True
        self.manual_hide = False

        self.setWindowTitle("Sticky Todo")
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(430, 570)

        saved_pos = SETTINGS.value("position")
        if isinstance(saved_pos, QPoint):
            self.move(saved_pos)
        else:
            screen = QApplication.primaryScreen().availableGeometry()
            self.move(screen.right() - self.width() - 28, screen.top() + 80)

        self.build()
        self.apply_opacity()
        self.render()

        self.desktop_timer = QTimer(self)
        self.desktop_timer.setInterval(250)
        self.desktop_timer.timeout.connect(self.update_desktop_visibility)
        self.desktop_timer.start()

        self.reminder_timer = QTimer(self)
        self.reminder_timer.setInterval(15000)
        self.reminder_timer.timeout.connect(self.check_reminders)
        self.reminder_timer.start()

    def load(self):
        try:
            data = json.loads(DATA.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return []
            for task in data:
                task.setdefault("priority", "Medium")
                task.setdefault("due_date", "")
                task.setdefault("reminder", "")
                task.setdefault("notified", False)
            return data
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []

    def save(self):
        DATA.write_text(
            json.dumps(self.tasks, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def update_desktop_visibility(self):
        if not self.desktop_only:
            return

        hwnd = user32.GetForegroundWindow()
        own_hwnd = int(self.winId())
        active_class = window_class(hwnd)

        if hwnd == own_hwnd:
            return

        on_desktop = active_class in DESKTOP_CLASSES

        if on_desktop and not self.manual_hide:
            if not self.isVisible():
                self.show()
        elif not on_desktop and self.isVisible():
            self.hide()

    def build(self):
        self.card = QFrame()
        self.card.setObjectName("card")
        self.card.setStyleSheet(
            """
            #card {
                background: #17191f;
                border: 1px solid #343842;
                border-radius: 18px;
            }
            QLabel { color: #f5f7fb; }
            QLineEdit, QComboBox, QDateEdit, QTimeEdit {
                background: #22252d;
                color: #fff;
                border: 1px solid #363a45;
                border-radius: 9px;
                padding: 8px;
            }
            QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QTimeEdit:focus {
                border: 1px solid #5965ff;
            }
            QComboBox QAbstractItemView {
                background: #22252d;
                color: #fff;
                selection-background-color: #383d48;
            }
            QPushButton {
                background: #292d36;
                color: #fff;
                border: 0;
                border-radius: 9px;
                padding: 7px 10px;
            }
            QPushButton:hover { background: #383d48; }
            QCheckBox {
                color: #e7e9ee;
                padding: 7px 3px;
            }
            QSlider::groove:horizontal {
                height: 4px;
                background: #343842;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 12px;
                margin: -4px 0;
                border-radius: 6px;
                background: #7c83ff;
            }
            """
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.addWidget(self.card)

        root = QVBoxLayout(self.card)
        root.setContentsMargins(18, 16, 18, 18)
        root.setSpacing(9)

        top = QHBoxLayout()
        title = QLabel("✓  STICKY TODO")
        title.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.count = QLabel()
        self.count.setStyleSheet("color:#9ca3af;")
        minimize = QPushButton("—")
        minimize.setFixedWidth(30)
        minimize.clicked.connect(self.hide_from_desktop)
        close = QPushButton("×")
        close.setFixedWidth(30)
        close.clicked.connect(self.hide_from_desktop)
        top.addWidget(title)
        top.addStretch()
        top.addWidget(self.count)
        top.addWidget(minimize)
        top.addWidget(close)
        root.addLayout(top)

        add = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Add a task…")
        self.input.returnPressed.connect(self.add_task)
        btn = QPushButton("+")
        btn.setFixedWidth(40)
        btn.clicked.connect(self.add_task)
        add.addWidget(self.input)
        add.addWidget(btn)
        root.addLayout(add)

        options = QHBoxLayout()
        self.due_date = QDateEdit(QDate.currentDate())
        self.due_date.setCalendarPopup(True)
        self.due_date.setDisplayFormat("dd MMM")
        self.due_date.setToolTip("Due date")

        self.priority = QComboBox()
        self.priority.addItems(["Low", "Medium", "High"])
        self.priority.setCurrentText("Medium")
        self.priority.setToolTip("Priority")

        self.reminder = QTimeEdit()
        self.reminder.setDisplayFormat("HH:mm")
        self.reminder.setTime(QTime(0, 0))
        self.reminder.setToolTip("Reminder time (today)")
        self.reminder.setSpecialValueText("No reminder")

        options.addWidget(self.due_date, 2)
        options.addWidget(self.priority, 1)
        options.addWidget(self.reminder, 1)
        root.addLayout(options)

        hint = QLabel("Date • Priority • reminder time")
        hint.setStyleSheet("color:#777d89; font-size:10px;")
        root.addWidget(hint)

        self.list_layout = QVBoxLayout()
        self.list_layout.setSpacing(2)
        root.addLayout(self.list_layout)

        actions = QHBoxLayout()
        clear = QPushButton("Clear completed")
        clear.clicked.connect(self.clear_completed)
        actions.addWidget(clear)
        actions.addStretch()
        root.addLayout(actions)

        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel("Opacity"))
        self.opacity_value = QLabel()
        self.opacity_value.setStyleSheet("color:#9ca3af;")
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(55, 100)
        self.opacity.setValue(int(SETTINGS.value("opacity", 96)))
        self.opacity.valueChanged.connect(self.opacity_changed)
        opacity_row.addWidget(self.opacity)
        opacity_row.addWidget(self.opacity_value)
        root.addLayout(opacity_row)

    def render(self):
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        pending = [(i, t) for i, t in enumerate(self.tasks) if not t.get("done")]
        completed = [(i, t) for i, t in enumerate(self.tasks) if t.get("done")]

        today = date.today().isoformat()

        def sort_key(item):
            _, task = item
            due = task.get("due_date") or "9999-12-31"
            priority_order = {"High": 0, "Medium": 1, "Low": 2}
            return (
                0 if due == today else 1,
                due,
                priority_order.get(task.get("priority"), 1),
            )

        pending.sort(key=sort_key)
        completed.sort(key=lambda item: item[1].get("due_date") or "9999-12-31")

        done = len(completed)
        self.count.setText(f"{done}/{len(self.tasks)} done")

        if not self.tasks:
            empty = QLabel("No tasks yet. Add something above.")
            empty.setStyleSheet("color:#777d89; padding:12px 3px;")
            self.list_layout.addWidget(empty)

        shown_today_header = False
        for idx, task in pending:
            if task.get("due_date") == today and not shown_today_header:
                label = QLabel("TODAY")
                label.setStyleSheet(
                    "color:#7c83ff; font-size:11px; font-weight:700; margin-top:5px;"
                )
                self.list_layout.addWidget(label)
                shown_today_header = True
            self.add_task_row(idx, task)

        if completed:
            label = QLabel("COMPLETED")
            label.setStyleSheet(
                "color:#777d89; font-size:11px; font-weight:700; margin-top:8px;"
            )
            self.list_layout.addWidget(label)
            for idx, task in completed:
                self.add_task_row(idx, task)

    def add_task_row(self, idx, task):
        row = QHBoxLayout()
        box = QCheckBox(task.get("text", ""))
        box.setChecked(bool(task.get("done")))
        box.stateChanged.connect(
            lambda state, index=idx: self.toggle(index, state)
        )

        priority = task.get("priority", "Medium")
        due = task.get("due_date", "")
        reminder = task.get("reminder", "")

        details = []
        if due:
            if due == date.today().isoformat():
                details.append("Today")
            else:
                try:
                    details.append(datetime.strptime(due, "%Y-%m-%d").strftime("%d %b"))
                except ValueError:
                    details.append(due)
        details.append(priority)
        if reminder:
            details.append(f"⏰ {reminder}")

        meta = QLabel("  •  ".join(details))
        meta.setStyleSheet(
            "color:#9ca3af; font-size:10px;" +
            (" text-decoration:line-through;" if task.get("done") else "")
        )

        if task.get("done"):
            box.setStyleSheet("color:#777d89; text-decoration:line-through;")

        delete = QPushButton("×")
        delete.setFixedWidth(28)
        delete.clicked.connect(lambda _, index=idx: self.remove(index))

        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        text_col.addWidget(box)
        if details:
            text_col.addWidget(meta)

        row.addLayout(text_col)
        row.addStretch()
        row.addWidget(delete)
        self.list_layout.addLayout(row)

    def add_task(self):
        text = self.input.text().strip()
        if not text:
            return

        due = self.due_date.date().toString("yyyy-MM-dd")
        priority = self.priority.currentText()
        reminder_time = self.reminder.time().toString("HH:mm")
        reminder = "" if reminder_time == "00:00" else f"{due} {reminder_time}"

        self.tasks.append({
            "text": text,
            "done": False,
            "priority": priority,
            "due_date": due,
            "reminder": reminder,
            "notified": False,
        })
        self.input.clear()
        self.save()
        self.render()
        self.input.setFocus()

    def toggle(self, idx, state):
        if 0 <= idx < len(self.tasks):
            self.tasks[idx]["done"] = bool(state)
            self.save()
            self.render()

    def remove(self, idx):
        if 0 <= idx < len(self.tasks):
            self.tasks.pop(idx)
            self.save()
            self.render()

    def clear_completed(self):
        self.tasks = [task for task in self.tasks if not task.get("done")]
        self.save()
        self.render()

    def check_reminders(self):
        if not Notification:
            return

        now = datetime.now()
        changed = False

        for task in self.tasks:
            if task.get("done") or task.get("notified") or not task.get("reminder"):
                continue

            try:
                reminder_at = datetime.strptime(task["reminder"], "%Y-%m-%d %H:%M")
            except (TypeError, ValueError):
                continue

            if reminder_at <= now:
                try:
                    toast = Notification(
                        app_id="Sticky Todo",
                        title="Sticky Todo reminder",
                        msg=task.get("text", "Task due"),
                    )
                    toast.show()
                except Exception:
                    continue

                task["notified"] = True
                changed = True

        if changed:
            self.save()
            self.render()

    def opacity_changed(self, value):
        SETTINGS.setValue("opacity", value)
        self.apply_opacity()

    def apply_opacity(self):
        value = int(SETTINGS.value("opacity", 96))
        self.setWindowOpacity(value / 100)
        if hasattr(self, "opacity_value"):
            self.opacity_value.setText(f"{value}%")

    def moveEvent(self, event):
        SETTINGS.setValue("position", self.pos())
        super().moveEvent(event)

    def hide_from_desktop(self):
        self.manual_hide = True
        self.hide()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_pos = None
        super().mouseReleaseEvent(event)


def request_quit():
    """Gracefully stop the Qt event loop so Ctrl+C exits once."""
    app.quit()


app = QApplication(sys.argv)
app.setQuitOnLastWindowClosed(False)

# Qt's event loop can keep running after Python raises KeyboardInterrupt from
# a timer callback. Handle Ctrl+C at the process level and ask Qt to exit.
signal.signal(signal.SIGINT, lambda signum, frame: request_quit())

window = TodoWindow()
window.show()

tray = QSystemTrayIcon(window)
tray.setToolTip("Sticky Todo")

# Give the tray icon an explicit icon to avoid the Qt warning and make the
# tray entry visible on Windows even though Sticky currently has no .ico file.
tray.setIcon(app.style().standardIcon(app.style().SP_ComputerIcon))

menu = QMenu()
show_action = menu.addAction("Show Sticky")
show_action.triggered.connect(
    lambda: (setattr(window, "manual_hide", False), window.show())
)
hide_action = menu.addAction("Hide Sticky")
hide_action.triggered.connect(window.hide_from_desktop)
menu.addSeparator()
quit_action = menu.addAction("Quit")
quit_action.triggered.connect(app.quit)

tray.setContextMenu(menu)
tray.activated.connect(
    lambda reason: window.show()
    if reason == QSystemTrayIcon.DoubleClick
    else None
)
tray.show()

sys.exit(app.exec())
