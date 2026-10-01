import ctypes
from ctypes import wintypes
import json
import signal
import sys
import uuid
from datetime import datetime, date
from pathlib import Path

from PySide6.QtCore import (
    QAbstractNativeEventFilter,
    QPoint,
    QDate,
    QTime,
    QSettings,
    QTimer,
    Qt,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
    QStyle,
)

try:
    from winotify import Notification
except ImportError:
    Notification = None


APP_NAME = "Sticky Todo"
DATA = Path.home() / ".sticky_todo.json"
SETTINGS = QSettings("DeathKernel", "StickyTodo")

user32 = ctypes.windll.user32
DESKTOP_CLASSES = {"Progman", "WorkerW"}

HOTKEY_ID = 0x53544943
WM_HOTKEY = 0x0312
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
VK_SPACE = 0x20

CATEGORIES = ["Personal", "Work", "Study", "Coding"]
PRIORITIES = ["Low", "Medium", "High"]


def window_class(hwnd):
    if not hwnd:
        return ""
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def new_task(text, category="Personal", priority="Medium", due_date="", reminder=""):
    return {
        "id": uuid.uuid4().hex,
        "text": text,
        "done": False,
        "category": category,
        "priority": priority,
        "due_date": due_date,
        "reminder": reminder,
        "notified": False,
    }


class GlobalHotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def nativeEventFilter(self, event_type, message):
        if event_type in ("windows_generic_MSG", "windows_dispatcher_MSG",
                          b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            try:
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    self.callback()
                    return True, 0
            except (TypeError, ValueError, OSError):
                pass
        return False, 0


class TodoWindow(QWidget):
    def __init__(self):
        super().__init__()

        self.tasks = self.load()
        self.search_query = ""
        self.category_filter = "All categories"
        self.manual_hide = False
        self.desktop_only = True
        self.drag_pos = None
        self.hotkey_registered = False
        self.mini_button = None

        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(360, 460)

        saved_pos = SETTINGS.value("position")
        if isinstance(saved_pos, QPoint):
            self.move(saved_pos)
        else:
            screen = QApplication.primaryScreen().availableGeometry()
            self.move(
                screen.right() - self.width() - 28,
                screen.top() + 80,
            )

        self.build()
        self.create_minimize_icon()
        self.apply_opacity()
        self.render()

        self.hotkey_filter = GlobalHotkeyFilter(self.quick_add)
        QApplication.instance().installNativeEventFilter(self.hotkey_filter)
        self.hotkey_registered = bool(
            user32.RegisterHotKey(
                None,
                HOTKEY_ID,
                MOD_CONTROL | MOD_SHIFT,
                VK_SPACE,
            )
        )

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
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return []

        if not isinstance(data, list):
            return []

        changed = False
        for task in data:
            if not isinstance(task, dict):
                continue
            if not task.get("id"):
                task["id"] = uuid.uuid4().hex
                changed = True
            task.setdefault("text", "")
            task.setdefault("done", False)
            task.setdefault("category", "Personal")
            task.setdefault("priority", "Medium")
            task.setdefault("due_date", "")
            task.setdefault("reminder", "")
            task.setdefault("notified", False)

        if changed:
            try:
                DATA.write_text(
                    json.dumps(data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except OSError:
                pass

        return [task for task in data if isinstance(task, dict)]

    def save(self):
        try:
            DATA.write_text(
                json.dumps(self.tasks, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass

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
                color: #9b9da5;
                border: 0;
                border-radius: 8px;
                padding: 5px;
            }
            QPushButton:hover {
                background: #25282e;
                color: #ffffff;
            }
            QCheckBox {
                color: #f4f4f5;
                spacing: 9px;
                padding: 7px 3px;
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
            QMenu::item:selected {
                background: #30343b;
            }
            """
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(7, 7, 7, 7)

        self.card = QFrame()
        self.card.setObjectName("card")
        outer.addWidget(self.card)

        root = QVBoxLayout(self.card)
        root.setContentsMargins(13, 11, 13, 12)
        root.setSpacing(5)

        header = QHBoxLayout()
        title = QLabel("✓  Sticky")
        title.setStyleSheet(
            "font-size: 14px; font-weight: 700; color: #ffffff;"
        )
        header.addWidget(title)
        header.addStretch()

        add_button = QPushButton("+")
        add_button.setFixedSize(30, 30)
        add_button.setToolTip("Add task")
        add_button.clicked.connect(self.open_task_dialog)
        header.addWidget(add_button)

        more_button = QPushButton("•••")
        more_button.setFixedSize(34, 30)
        more_button.setToolTip("Options")
        more_button.clicked.connect(
            lambda: self.open_more_menu(more_button)
        )
        header.addWidget(more_button)

        root.addLayout(header)

        self.list_layout = QVBoxLayout()
        self.list_layout.setContentsMargins(0, 3, 0, 0)
        self.list_layout.setSpacing(1)
        root.addLayout(self.list_layout)

    def create_minimize_icon(self):
        self.mini_button = QPushButton("✓", self)
        self.mini_button.setFixedSize(58, 58)
        self.mini_button.setToolTip("Restore Sticky Todo")
        self.mini_button.setStyleSheet(
            """
            QPushButton {
                background: #15171b;
                color: #8b92ff;
                border: 2px solid #30343b;
                border-radius: 29px;
                font-size: 21px;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #202329;
                border-color: #5965ff;
            }
            """
        )
        self.mini_button.clicked.connect(self.restore_from_mini)
        self.mini_button.hide()

    def open_more_menu(self, button):
        menu = QMenu(self)

        search = menu.addAction("Search")
        search.triggered.connect(self.search_tasks)

        category = menu.addAction("Filter category")
        category.triggered.connect(self.filter_tasks)

        clear_filter = menu.addAction("Clear filters")
        clear_filter.triggered.connect(self.clear_filters)

        menu.addSeparator()

        clear_completed = menu.addAction("Clear completed")
        clear_completed.triggered.connect(self.clear_completed)

        opacity = menu.addAction("Opacity")
        opacity.triggered.connect(self.change_opacity_dialog)

        menu.addSeparator()

        minimize = menu.addAction("Minimize to icon")
        minimize.triggered.connect(self.minimize_to_icon)

        hide = menu.addAction("Hide")
        hide.triggered.connect(self.hide_from_desktop)

        menu.addSeparator()

        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(QApplication.instance().quit)

        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def search_tasks(self):
        text, ok = QInputDialog.getText(
            self,
            "Search",
            "Find task:",
            QLineEdit.Normal,
            self.search_query,
        )
        if ok:
            self.search_query = text.strip().lower()
            self.render()

    def filter_tasks(self):
        choices = ["All categories"] + CATEGORIES
        current = self.category_filter
        index = choices.index(current) if current in choices else 0

        choice, ok = QInputDialog.getItem(
            self,
            "Category",
            "Show:",
            choices,
            index,
            False,
        )
        if ok:
            self.category_filter = choice
            self.render()

    def clear_filters(self):
        self.search_query = ""
        self.category_filter = "All categories"
        self.render()

    def change_opacity_dialog(self):
        current = int(SETTINGS.value("opacity", 96))
        value, ok = QInputDialog.getInt(
            self,
            "Opacity",
            "Opacity (%):",
            current,
            55,
            100,
            1,
        )
        if ok:
            SETTINGS.setValue("opacity", value)
            self.setWindowOpacity(value / 100)

    def open_task_dialog(self, task_id=None):
        editing = task_id is not None
        task = self.find_task(task_id) if editing else None
        if editing and task is None:
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Edit task" if editing else "Add task")
        dialog.setMinimumWidth(340)

        form = QFormLayout(dialog)
        form.setContentsMargins(18, 16, 18, 16)
        form.setSpacing(9)

        text = QLineEdit(task.get("text", "") if task else "")
        text.setPlaceholderText("What needs to be done?")
        form.addRow("Task", text)

        category = QComboBox()
        category.addItems(CATEGORIES)
        category.setCurrentText(task.get("category", "Personal") if task else "Personal")
        form.addRow("Category", category)

        priority = QComboBox()
        priority.addItems(PRIORITIES)
        priority.setCurrentText(task.get("priority", "Medium") if task else "Medium")
        form.addRow("Priority", priority)

        due_enabled = QCheckBox("Set due date")
        due_enabled.setChecked(bool(task and task.get("due_date")))
        form.addRow("", due_enabled)

        due = QDateEdit(QDate.currentDate())
        due.setCalendarPopup(True)
        due.setDisplayFormat("dd MMM yyyy")
        if task and task.get("due_date"):
            parsed = QDate.fromString(task["due_date"], "yyyy-MM-dd")
            if parsed.isValid():
                due.setDate(parsed)
        due.setEnabled(due_enabled.isChecked())
        due_enabled.toggled.connect(due.setEnabled)
        form.addRow("Due", due)

        reminder = QTimeEdit()
        reminder.setDisplayFormat("HH:mm")
        reminder.setSpecialValueText("No reminder")
        reminder.setTime(QTime(0, 0))
        if task and task.get("reminder"):
            parsed_time = QTime.fromString(task["reminder"][-5:], "HH:mm")
            if parsed_time.isValid():
                reminder.setTime(parsed_time)
        reminder.setEnabled(due_enabled.isChecked())
        due_enabled.toggled.connect(reminder.setEnabled)
        form.addRow("Reminder", reminder)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        form.addRow(buttons)

        def save_form():
            task_text = text.text().strip()
            if not task_text:
                QMessageBox.warning(
                    dialog,
                    "Missing task",
                    "Enter a task name.",
                )
                text.setFocus()
                return

            due_value = (
                due.date().toString("yyyy-MM-dd")
                if due_enabled.isChecked()
                else ""
            )
            reminder_time = reminder.time().toString("HH:mm")
            reminder_value = (
                f"{due_value} {reminder_time}"
                if due_value and reminder_time != "00:00"
                else ""
            )

            if editing:
                task.update({
                    "text": task_text,
                    "category": category.currentText(),
                    "priority": priority.currentText(),
                    "due_date": due_value,
                    "reminder": reminder_value,
                    "notified": False,
                })
            else:
                self.tasks.append(
                    new_task(
                        task_text,
                        category.currentText(),
                        priority.currentText(),
                        due_value,
                        reminder_value,
                    )
                )

            self.save()
            self.render()
            dialog.accept()

        buttons.button(QDialogButtonBox.Save).clicked.connect(save_form)
        buttons.button(QDialogButtonBox.Cancel).clicked.connect(dialog.reject)

        text.selectAll()
        text.setFocus()
        dialog.exec()

    def find_task(self, task_id):
        for task in self.tasks:
            if task.get("id") == task_id:
                return task
        return None

    def render(self):
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            layout = item.layout()
            if widget:
                widget.deleteLater()
            elif layout:
                while layout.count():
                    child = layout.takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()

        query = self.search_query
        category = self.category_filter

        visible = []
        for task in self.tasks:
            if query and query not in task.get("text", "").lower():
                continue
            if (
                category != "All categories"
                and task.get("category", "Personal") != category
            ):
                continue
            visible.append(task)

        pending = [task for task in visible if not task.get("done")]
        completed = [task for task in visible if task.get("done")]
        today = date.today().isoformat()

        def sort_key(task):
            due = task.get("due_date") or "9999-12-31"
            return (0 if due == today else 1, due, task.get("text", "").lower())

        pending.sort(key=sort_key)
        completed.sort(key=lambda t: t.get("text", "").lower())

        if not visible:
            empty = QLabel("No tasks")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(
                "color:#71717a; padding:24px 8px; font-size:12px;"
            )
            self.list_layout.addWidget(empty)
            return

        for task in pending:
            self.add_task_row(task)

        if completed:
            label = QLabel("Completed")
            label.setStyleSheet(
                "color:#52525b; font-size:10px; margin:8px 4px 3px;"
            )
            self.list_layout.addWidget(label)
            for task in completed:
                self.add_task_row(task)

    def add_task_row(self, task):
        row = QHBoxLayout()
        row.setContentsMargins(2, 1, 2, 1)
        row.setSpacing(2)

        box = QCheckBox(task.get("text", ""))
        box.setChecked(bool(task.get("done")))
        task_id = task.get("id")
        box.stateChanged.connect(
            lambda state, tid=task_id: self.toggle(tid, state)
        )

        if task.get("done"):
            box.setStyleSheet(
                "color:#666a73; text-decoration:line-through;"
            )

        row.addWidget(box, 1)

        menu_button = QPushButton("•••")
        menu_button.setFixedSize(30, 30)
        menu_button.setToolTip("Task options")
        menu_button.clicked.connect(
            lambda _, tid=task_id, button=menu_button:
            self.task_menu(tid, button)
        )
        row.addWidget(menu_button)

        self.list_layout.addLayout(row)

    def task_menu(self, task_id, button):
        task = self.find_task(task_id)
        if task is None:
            return

        menu = QMenu(self)

        edit = menu.addAction("Edit")
        edit.triggered.connect(
            lambda: self.open_task_dialog(task_id)
        )

        toggle = menu.addAction(
            "Mark incomplete" if task.get("done") else "Mark complete"
        )
        toggle.triggered.connect(
            lambda: self.toggle(task_id, not task.get("done"))
        )

        delete = menu.addAction("Delete")
        delete.triggered.connect(
            lambda: self.remove(task_id)
        )

        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def quick_add(self):
        text, ok = QInputDialog.getText(
            None,
            "Sticky • Quick Add",
            "Task:",
            QLineEdit.Normal,
            "",
        )
        if not ok or not text.strip():
            return

        self.tasks.append(new_task(text.strip()))
        self.save()
        self.render()

    def toggle(self, task_id, state):
        task = self.find_task(task_id)
        if task is None:
            return
        task["done"] = bool(state)
        self.save()
        self.render()

    def remove(self, task_id):
        task = self.find_task(task_id)
        if task is None:
            return

        answer = QMessageBox.question(
            self,
            "Delete task",
            f'Delete "{task.get("text", "this task")}"?',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.tasks = [
                item for item in self.tasks
                if item.get("id") != task_id
            ]
            self.save()
            self.render()

    def clear_completed(self):
        if not any(task.get("done") for task in self.tasks):
            return

        answer = QMessageBox.question(
            self,
            "Clear completed",
            "Remove all completed tasks?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.tasks = [
                task for task in self.tasks if not task.get("done")
            ]
            self.save()
            self.render()

    def check_reminders(self):
        if not Notification:
            return

        now = datetime.now()
        changed = False

        for task in self.tasks:
            if (
                task.get("done")
                or task.get("notified")
                or not task.get("reminder")
            ):
                continue

            try:
                reminder_at = datetime.strptime(
                    task["reminder"],
                    "%Y-%m-%d %H:%M",
                )
            except (TypeError, ValueError):
                continue

            if reminder_at <= now:
                try:
                    Notification(
                        app_id=APP_NAME,
                        title="Sticky Todo reminder",
                        msg=task.get("text", "Task due"),
                    ).show()
                except Exception:
                    continue

                task["notified"] = True
                changed = True

        if changed:
            self.save()

    def minimize_to_icon(self):
        if self.mini_button.isVisible():
            return

        self.card.hide()
        self.mini_button.show()
        self.resize(72, 72)

    def restore_from_mini(self):
        self.mini_button.hide()
        self.card.show()
        self.resize(360, 460)
        self.raise_()
        self.activateWindow()

    def update_desktop_visibility(self):
        if not self.desktop_only:
            return

        hwnd = user32.GetForegroundWindow()
        own_hwnd = int(self.winId())

        if hwnd == own_hwnd:
            return

        on_desktop = window_class(hwnd) in DESKTOP_CLASSES

        if on_desktop and not self.manual_hide:
            if not self.isVisible():
                self.show()
        elif not on_desktop and self.isVisible():
            self.hide()

    def apply_opacity(self):
        value = int(SETTINGS.value("opacity", 96))
        value = max(55, min(100, value))
        self.setWindowOpacity(value / 100)

    def hide_from_desktop(self):
        self.manual_hide = True
        self.hide()

    def show_from_tray(self):
        self.manual_hide = False
        self.show()
        self.raise_()
        self.activateWindow()

    def moveEvent(self, event):
        if not self.mini_button or not self.mini_button.isVisible():
            SETTINGS.setValue("position", self.pos())
        super().moveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_pos = (
                event.globalPosition().toPoint()
                - self.frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self.drag_pos is not None
            and event.buttons() & Qt.LeftButton
        ):
            self.move(
                event.globalPosition().toPoint() - self.drag_pos
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.drag_pos = None
        super().mouseReleaseEvent(event)

    def cleanup(self):
        if self.hotkey_registered:
            user32.UnregisterHotKey(None, HOTKEY_ID)
            self.hotkey_registered = False
        try:
            QApplication.instance().removeNativeEventFilter(
                self.hotkey_filter
            )
        except Exception:
            pass


def request_quit():
    app.quit()


app = QApplication(sys.argv)
app.setApplicationName(APP_NAME)
app.setQuitOnLastWindowClosed(False)

signal.signal(signal.SIGINT, lambda signum, frame: request_quit())

window = TodoWindow()
app.aboutToQuit.connect(window.cleanup)
window.show()

tray = QSystemTrayIcon(window)
tray.setToolTip(APP_NAME)
tray.setIcon(
    app.style().standardIcon(
        QStyle.StandardPixmap.SP_ComputerIcon
    )
)

menu = QMenu()
show_action = menu.addAction("Show Sticky")
show_action.triggered.connect(window.show_from_tray)

hide_action = menu.addAction("Hide Sticky")
hide_action.triggered.connect(window.hide_from_desktop)

menu.addSeparator()

quit_action = menu.addAction("Quit")
quit_action.triggered.connect(app.quit)

tray.setContextMenu(menu)
tray.activated.connect(
    lambda reason: window.show_from_tray()
    if reason == QSystemTrayIcon.DoubleClick
    else None
)
tray.show()

sys.exit(app.exec())
