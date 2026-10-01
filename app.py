import ctypes
from ctypes import wintypes
import json
import signal
import sys
from datetime import datetime, date
from pathlib import Path

from PySide6.QtCore import QAbstractNativeEventFilter, QPoint, Qt, QDate, QTime, QSettings, QTimer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDateEdit, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QMenu, QPushButton, QSlider, QSystemTrayIcon,
    QTimeEdit, QVBoxLayout, QWidget, QStyle, QInputDialog,
    QDialog, QDialogButtonBox, QFormLayout, QSpinBox,
)

try:
    from winotify import Notification
except ImportError:
    Notification = None

DATA = Path.home() / ".sticky_todo.json"
SETTINGS = QSettings("DeathKernel", "StickyTodo")

user32 = ctypes.windll.user32
DESKTOP_CLASSES = {"Progman", "WorkerW"}
HOTKEY_ID = 0x53544943  # STIC
WM_HOTKEY = 0x0312
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
VK_SPACE = 0x20


def window_class(hwnd):
    if not hwnd:
        return ""
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


class GlobalHotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def nativeEventFilter(self, eventType, message):
        if eventType in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self.callback()
                return True, 0
        return False, 0


class TodoWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.hotkey_filter = GlobalHotkeyFilter(self.quick_add)
        QApplication.instance().installNativeEventFilter(self.hotkey_filter)
        user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_SHIFT, VK_SPACE)

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
        self.mini_button = QPushButton("✓", self)
        self.mini_button.setFixedSize(56, 56)
        self.mini_button.setToolTip("Restore Sticky Todo")
        self.mini_button.setStyleSheet(
            """
            QPushButton {
                background: #17191f;
                color: #7c83ff;
                border: 2px solid #343842;
                border-radius: 28px;
                font-size: 22px;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #22252d;
                border-color: #5965ff;
            }
            """
        )
        self.mini_button.clicked.connect(self.restore_from_mini)
        self.mini_button.hide()
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
                task.setdefault("category", "Personal")
            return data
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []

    def save(self):
        DATA.write_text(
            json.dumps(self.tasks, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def quick_add(self):
        # Keep the main widget desktop-only. Quick Add uses a tiny transient
        # dialog so the global shortcut also works while another app is open.
        text, ok = QInputDialog.getText(
            None,
            "Sticky • Quick Add",
            "Task:",
            QLineEdit.Normal,
            "",
        )
        if ok and text.strip():
            self.tasks.append({
                "text": text.strip(),
                "done": False,
                "priority": "Medium",
                "due_date": date.today().isoformat(),
                "reminder": "",
                "notified": False,
                "category": "Personal",
            })
            self.save()
            self.render()

    def minimize_to_icon(self):
        """Collapse Sticky into a small floating desktop icon."""
        if self.mini_button.isVisible():
            return
        self.card.hide()
        self.mini_button.show()
        self.resize(64, 64)
        self.setMinimumSize(0, 0)
        self.adjustSize()

    def restore_from_mini(self):
        """Restore the full Sticky Todo window from its desktop icon."""
        self.mini_button.hide()
        self.card.show()
        self.resize(430, 570)
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

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
        self.setStyleSheet(
            """
            QWidget {
                color: #f4f4f5;
                font-family: "Segoe UI";
            }
            #card {
                background: #15171b;
                border: 1px solid #292d33;
                border-radius: 16px;
            }
            QPushButton {
                background: transparent;
                color: #a1a1aa;
                border: 0;
                border-radius: 8px;
                padding: 6px;
            }
            QPushButton:hover {
                background: #24272d;
                color: #ffffff;
            }
            QCheckBox {
                color: #f4f4f5;
                spacing: 10px;
                padding: 8px 4px;
                font-size: 13px;
            }
            QLineEdit, QComboBox, QDateEdit, QTimeEdit {
                background: #202329;
                color: #f4f4f5;
                border: 1px solid #30343b;
                border-radius: 8px;
                padding: 7px;
            }
            QDialog {
                background: #15171b;
            }
            """
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(7, 7, 7, 7)
        self.card = QFrame()
        self.card.setObjectName("card")
        outer.addWidget(self.card)

        root = QVBoxLayout(self.card)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("✓  Sticky")
        title.setStyleSheet("font-size: 14px; font-weight: 700; color: #ffffff;")
        header.addWidget(title)
        header.addStretch()

        add = QPushButton("+")
        add.setFixedSize(30, 30)
        add.setToolTip("Add task")
        add.clicked.connect(self.open_task_dialog)
        header.addWidget(add)

        more = QPushButton("•••")
        more.setFixedSize(34, 30)
        more.setToolTip("More")
        more.clicked.connect(self.open_more_menu)
        header.addWidget(more)
        root.addLayout(header)

        self.list_layout = QVBoxLayout()
        self.list_layout.setContentsMargins(0, 2, 0, 0)
        self.list_layout.setSpacing(1)
        root.addLayout(self.list_layout)

    def open_more_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(
            """
            QMenu {
                background: #202329;
                color: #f4f4f5;
                border: 1px solid #343840;
                padding: 5px;
            }
            QMenu::item {
                padding: 7px 22px 7px 10px;
                border-radius: 6px;
            }
            QMenu::item:selected { background: #30343b; }
            """
        )

        search_action = menu.addAction("Search tasks")
        search_action.triggered.connect(self.search_tasks)

        filter_action = menu.addAction("Filter category")
        filter_action.triggered.connect(self.filter_tasks)

        menu.addSeparator()

        clear = menu.addAction("Clear completed")
        clear.triggered.connect(self.clear_completed)

        opacity = menu.addAction("Opacity")
        opacity.triggered.connect(self.change_opacity_dialog)

        menu.addSeparator()
        menu.addAction("Minimize to icon", self.minimize_to_icon)
        menu.exec(self.sender().mapToGlobal(self.sender().rect().bottomLeft()))

    def search_tasks(self):
        text, ok = QInputDialog.getText(
            self, "Search", "Find task:", QLineEdit.Normal, ""
        )
        if ok:
            self.search_query = text.strip().lower()
            self.render()

    def filter_tasks(self):
        choices = ["All categories", "Personal", "Work", "Study", "Coding"]
        current = getattr(self, "category_filter", "All categories")
        choice, ok = QInputDialog.getItem(
            self, "Category", "Show:", choices, choices.index(current), False
        )
        if ok:
            self.category_filter = choice
            self.render()

    def change_opacity_dialog(self):
        value, ok = QInputDialog.getInt(
            self, "Opacity", "Opacity (%):",
            int(SETTINGS.value("opacity", 96)), 55, 100, 1
        )
        if ok:
            SETTINGS.setValue("opacity", value)
            self.setWindowOpacity(value / 100)

    def open_task_dialog(self, idx=None):
        editing = idx is not None
        task = self.tasks[idx] if editing else {}

        dialog = QDialog(self)
        dialog.setWindowTitle("Edit task" if editing else "Add task")
        dialog.setMinimumWidth(320)

        form = QFormLayout(dialog)
        text = QLineEdit(task.get("text", ""))
        text.setPlaceholderText("Task")
        form.addRow("Task", text)

        category = QComboBox()
        category.addItems(["Personal", "Work", "Study", "Coding"])
        category.setCurrentText(task.get("category", "Personal"))
        form.addRow("Category", category)

        priority = QComboBox()
        priority.addItems(["Low", "Medium", "High"])
        priority.setCurrentText(task.get("priority", "Medium"))
        form.addRow("Priority", priority)

        due = QDateEdit()
        due.setCalendarPopup(True)
        due.setDisplayFormat("dd MMM yyyy")
        due.setDate(
            QDate.fromString(task.get("due_date", ""), "yyyy-MM-dd")
            if task.get("due_date")
            else QDate.currentDate()
        )
        form.addRow("Due", due)

        reminder = QTimeEdit()
        reminder.setDisplayFormat("HH:mm")
        reminder.setSpecialValueText("No reminder")
        reminder.setTime(QTime(0, 0))
        raw_reminder = task.get("reminder", "")
        if raw_reminder:
            try:
                reminder.setTime(QTime.fromString(raw_reminder[-5:], "HH:mm"))
            except Exception:
                pass
        form.addRow("Reminder", reminder)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        text.selectAll()
        text.setFocus()

        if dialog.exec() != QDialog.Accepted or not text.text().strip():
            return

        due_value = due.date().toString("yyyy-MM-dd")
        reminder_time = reminder.time().toString("HH:mm")
        reminder_value = "" if reminder_time == "00:00" else f"{due_value} {reminder_time}"

        if editing:
            self.tasks[idx].update({
                "text": text.text().strip(),
                "category": category.currentText(),
                "priority": priority.currentText(),
                "due_date": due_value,
                "reminder": reminder_value,
                "notified": False,
            })
        else:
            self.tasks.append({
                "text": text.text().strip(),
                "done": False,
                "category": category.currentText(),
                "priority": priority.currentText(),
                "due_date": due_value,
                "reminder": reminder_value,
                "notified": False,
            })

        self.save()
        self.render()

    def render(self):
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            elif item.layout():
                child = item.layout()
                while child.count():
                    sub = child.takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()

        query = getattr(self, "search_query", "")
        category_filter = getattr(self, "category_filter", "All categories")
        visible = []
        for i, task in enumerate(self.tasks):
            if query and query not in task.get("text", "").lower():
                continue
            if category_filter != "All categories" and task.get("category", "Personal") != category_filter:
                continue
            visible.append((i, task))

        pending = [(i, t) for i, t in visible if not t.get("done")]
        completed = [(i, t) for i, t in visible if t.get("done")]
        today = date.today().isoformat()

        def sort_key(item):
            _, task = item
            due = task.get("due_date") or "9999-12-31"
            return (0 if due == today else 1, due)

        pending.sort(key=sort_key)
        completed.sort(key=lambda item: item[1].get("due_date") or "9999-12-31")

        if not visible:
            empty = QLabel("No tasks")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet("color:#71717a; padding:18px;")
            self.list_layout.addWidget(empty)
            return

        for idx, task in pending:
            self.add_task_row(idx, task)

        if completed:
            label = QLabel("Completed")
            label.setStyleSheet("color:#52525b; font-size:10px; margin:8px 4px 3px;")
            self.list_layout.addWidget(label)
            for idx, task in completed:
                self.add_task_row(idx, task)

    def add_task_row(self, idx, task):
        row = QHBoxLayout()
        row.setContentsMargins(2, 1, 2, 1)

        box = QCheckBox(task.get("text", ""))
        box.setChecked(bool(task.get("done")))
        box.stateChanged.connect(lambda state, index=idx: self.toggle(index, state))
        if task.get("done"):
            box.setStyleSheet("color:#666a73; text-decoration:line-through;")

        row.addWidget(box, 1)

        menu_button = QPushButton("•••")
        menu_button.setFixedSize(30, 30)
        menu_button.setToolTip("Task options")
        menu_button.clicked.connect(
            lambda _, index=idx, button=menu_button: self.task_menu(index, button)
        )
        row.addWidget(menu_button)
        self.list_layout.addLayout(row)

    def task_menu(self, idx, button):
        if not (0 <= idx < len(self.tasks)):
            return
        menu = QMenu(self)
        edit = menu.addAction("Edit")
        edit.triggered.connect(lambda: self.open_task_dialog(idx))
        delete = menu.addAction("Delete")
        delete.triggered.connect(lambda: self.remove(idx))
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

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
tray.setIcon(app.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))

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
