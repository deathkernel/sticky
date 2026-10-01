import ctypes
from ctypes import wintypes
import json
import signal
import sys
import uuid
import re
import shutil
from datetime import datetime, date, timedelta
from pathlib import Path

from PySide6.QtCore import (
    QAbstractNativeEventFilter,
    QPoint,
    QDate,
    QTime,
    QSettings,
    QTimer,
    Qt,
    Signal,
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
    QFileDialog,
    QPlainTextEdit,
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


def new_task(
    text,
    category="Personal",
    priority="Medium",
    due_date="",
    reminder="",
    tags="",
    notes="",
    pinned=False,
    repeat="None",
    subtasks=None,
):
    return {
        "id": uuid.uuid4().hex,
        "text": text,
        "done": False,
        "category": category,
        "priority": priority,
        "due_date": due_date,
        "reminder": reminder,
        "notified": False,
        "tags": tags,
        "notes": notes,
        "pinned": pinned,
        "repeat": repeat,
        "subtasks": list(subtasks or []),
        "created_at": datetime.now().isoformat(timespec="seconds"),
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


class TaskRow(QWidget):
    dropped = Signal(str)

    def __init__(self, owner, task):
        super().__init__(owner)
        self.owner = owner
        self.task_id = task.get("id")
        self.drag_start = None
        self.setObjectName("taskRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 5, 3)
        layout.setSpacing(3)

        self.box = QCheckBox(task.get("text", ""))
        self.box.setChecked(bool(task.get("done")))
        self.box.stateChanged.connect(
            lambda state, tid=self.task_id: owner.toggle(tid, state)
        )
        if task.get("done"):
            self.box.setStyleSheet(
                "color:#9ca3af; text-decoration:line-through;"
            )
        layout.addWidget(self.box, 1)

        repeat = task.get("repeat", "None")
        if repeat and repeat != "None":
            badge = QLabel("↻")
            badge.setToolTip("Repeats " + repeat)
            badge.setStyleSheet("color:#6b7280; font-size:11px;")
            layout.addWidget(badge)

        if task.get("pinned"):
            pin = QLabel("●")
            pin.setToolTip("Pinned")
            pin.setStyleSheet("color:#6478ff; font-size:9px;")
            layout.addWidget(pin)

        menu_button = QPushButton("•••")
        menu_button.setFixedSize(30, 30)
        menu_button.clicked.connect(
            lambda _, tid=self.task_id, button=menu_button:
            owner.task_menu(tid, button)
        )
        layout.addWidget(menu_button)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_start = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_start is not None and event.buttons() & Qt.LeftButton:
            if (event.position().toPoint() - self.drag_start).manhattanLength() > 8:
                self.owner.start_task_drag(self.task_id)
                self.drag_start = None
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.drag_start is not None:
            self.drag_start = None
        super().mouseReleaseEvent(event)


class TodoWindow(QWidget):
    def __init__(self):
        super().__init__()

        self.tasks = self.load()
        self.search_query = ""
        self.category_filter = "All categories"
        self.sort_mode = str(SETTINGS.value("sort_mode", "Smart"))
        self.manual_hide = False
        self.desktop_only = SETTINGS.value("desktop_only", True, type=bool)
        self.drag_pos = None
        self.task_rows = {}
        self.dragging_task_id = None
        self.undo_stack = []
        self.redo_stack = []
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
            task.setdefault("tags", "")
            task.setdefault("notes", "")
            task.setdefault("pinned", False)
            task.setdefault("repeat", "None")
            task.setdefault("subtasks", [])
            if not isinstance(task.get("subtasks"), list):
                task["subtasks"] = []
            task.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))

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

    def save_setting(self, key, value):
        SETTINGS.setValue(key, value)

    def task_matches(self, task):
        q = self.search_query
        if not q:
            return True
        haystack = " ".join(
            str(task.get(k, ""))
            for k in ("text", "tags", "notes", "category", "priority")
        ).lower()
        tokens = q.split()
        for token in tokens:
            if ":" in token:
                key, value = token.split(":", 1)
                value = value.strip().lower()
                if key == "priority" and task.get("priority", "").lower() != value:
                    return False
                if key == "category" and task.get("category", "").lower() != value:
                    return False
                if key == "status":
                    wanted = value == "done"
                    if bool(task.get("done")) != wanted:
                        return False
                if key == "tag" and value not in str(task.get("tags", "")).lower():
                    return False
                if key == "due":
                    today = date.today().isoformat()
                    due = task.get("due_date", "")
                    if value == "today" and due != today:
                        return False
                    if value == "overdue" and not (due and due < today and not task.get("done")):
                        return False
            elif token not in haystack:
                return False
        return True

    def sort_tasks(self, tasks):
        mode = getattr(self, "sort_mode", "Smart")
        def key(task):
            due = task.get("due_date") or "9999-12-31"
            priority = {"High": 0, "Medium": 1, "Low": 2}.get(task.get("priority"), 1)
            created = task.get("created_at", "")
            if mode == "Due date":
                return (due, task.get("text", "").lower())
            if mode == "Priority":
                return (priority, due, task.get("text", "").lower())
            if mode == "Alphabetical":
                return (task.get("text", "").lower(), due)
            if mode == "Manual":
            return list(tasks)
        if mode == "Newest":
                return (created * -1 if False else created,)
            return (0 if task.get("pinned") else 1, 0 if due == date.today().isoformat() else 1, due, priority)

        if mode == "Newest":
            return sorted(tasks, key=lambda t: t.get("created_at", ""), reverse=True)
        return sorted(tasks, key=key)

    def show_productivity(self):
        total = len(self.tasks)
        completed = sum(1 for t in self.tasks if t.get("done"))
        pending = total - completed
        overdue = sum(
            1 for t in self.tasks
            if not t.get("done") and t.get("due_date") and t.get("due_date") < date.today().isoformat()
        )
        pinned = sum(1 for t in self.tasks if t.get("pinned"))
        categories = {}
        for task in self.tasks:
            cat = task.get("category", "Personal")
            categories[cat] = categories.get(cat, 0) + 1

        dialog = QDialog(self)
        dialog.setWindowTitle("Productivity")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 18, 22, 18)
        percent = int((completed / total) * 100) if total else 0
        stats = QLabel(
            f"<b>Total</b> {total}<br>"
            f"<b>Completed</b> {completed} ({percent}%)<br>"
            f"<b>Pending</b> {pending}<br>"
            f"<b>Overdue</b> {overdue}<br>"
            f"<b>Pinned</b> {pinned}<br><br>"
            + "<b>Categories</b><br>"
            + "<br>".join(f"{k}: {v}" for k, v in sorted(categories.items()))
        )
        stats.setTextFormat(Qt.RichText)
        layout.addWidget(stats)
        close = QPushButton("Close")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def build(self):
        self.setStyleSheet(
            """
            QWidget {
                color: #172033;
                font-family: "Segoe UI";
            }
            #card {
                background: rgba(255, 255, 255, 225);
                border: 1px solid rgba(255, 255, 255, 245);
                border-radius: 22px;
            }
            #taskRow {
                background: rgba(255, 255, 255, 155);
                border: 1px solid rgba(255, 255, 255, 190);
                border-radius: 11px;
            }
            QPushButton {
                background: rgba(255, 255, 255, 110);
                color: #526078;
                border: 0;
                border-radius: 8px;
                padding: 5px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 205);
                color: #172033;
            }
            QCheckBox {
                color: #172033;
                spacing: 9px;
                padding: 7px 3px;
                font-size: 13px;
            }
            QLineEdit, QComboBox, QDateEdit, QTimeEdit {
                background: rgba(255, 255, 255, 205);
                color: #172033;
                border: 1px solid rgba(180, 190, 210, 170);
                border-radius: 8px;
                padding: 7px;
            }
            QDialog {
                background: rgba(248, 250, 255, 245);
            }
            QPlainTextEdit {
                background: rgba(255, 255, 255, 205);
                color: #172033;
                border: 1px solid rgba(180, 190, 210, 170);
                border-radius: 8px;
                padding: 7px;
            }
            QMenu {
                background: rgba(250, 252, 255, 250);
                color: #172033;
                border: 1px solid #343840;
                padding: 5px;
            }
            QMenu::item {
                padding: 7px 22px 7px 10px;
                border-radius: 6px;
            }
            QMenu::item:selected {
                background: rgba(220, 228, 242, 230);
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
            "font-size: 14px; font-weight: 700; color: #172033;"
        )
        header.addWidget(title)
        header.addStretch()

        add_button = QPushButton("+")
        add_button.setFixedSize(30, 30)
        add_button.setToolTip("Add task")
        add_button.clicked.connect(lambda: self.open_task_dialog())
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
        self.list_layout.setSpacing(5)
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

        sort = menu.addAction("Sort")
        sort.triggered.connect(self.choose_sort)

        menu.addSeparator()

        clear_completed = menu.addAction("Clear completed")
        clear_completed.triggered.connect(self.clear_completed)

        opacity = menu.addAction("Opacity")
        opacity.triggered.connect(self.change_opacity_dialog)

        productivity = menu.addAction("Productivity")
        productivity.triggered.connect(self.show_productivity)

        settings = menu.addAction("Settings")
        settings.triggered.connect(self.show_settings)

        backup = menu.addAction("Backup / Restore")
        backup.triggered.connect(self.backup_restore_menu)

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

    def choose_sort(self):
        choices = ["Smart", "Manual", "Due date", "Priority", "Alphabetical", "Newest"]
        current = getattr(self, "sort_mode", "Smart")
        index = choices.index(current) if current in choices else 0
        choice, ok = QInputDialog.getItem(
            self, "Sort tasks", "Sort by:", choices, index, False
        )
        if ok:
            self.sort_mode = choice
            SETTINGS.setValue("sort_mode", choice)
            self.render()

    def show_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Sticky Settings")
        form = QFormLayout(dialog)
        form.setContentsMargins(18, 16, 18, 16)

        desktop = QCheckBox("Show only on Windows desktop")
        desktop.setChecked(self.desktop_only)
        form.addRow(desktop)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        if dialog.exec() == QDialog.Accepted:
            self.desktop_only = desktop.isChecked()
            SETTINGS.setValue("desktop_only", self.desktop_only)

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

        tags = QLineEdit(task.get("tags", "") if task else "")
        tags.setPlaceholderText("e.g. urgent, project-x")
        form.addRow("Tags", tags)

        notes = QPlainTextEdit(task.get("notes", "") if task else "")
        notes.setPlaceholderText("Optional note")
        notes.setFixedHeight(70)
        form.addRow("Note", notes)

        repeat = QComboBox()
        repeat.addItems(["None", "Daily", "Weekly", "Monthly"])
        repeat.setCurrentText(task.get("repeat", "None") if task else "None")
        form.addRow("Repeat", repeat)

        subtasks = QLineEdit(
            ", ".join(task.get("subtasks", [])) if task else ""
        )
        subtasks.setPlaceholderText("Subtasks separated by commas")
        form.addRow("Subtasks", subtasks)

        pinned = QCheckBox("Pin task")
        pinned.setChecked(bool(task and task.get("pinned")))
        form.addRow("", pinned)

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
                    "tags": tags.text().strip(),
                    "notes": notes.toPlainText().strip(),
                    "pinned": pinned.isChecked(),
                    "repeat": repeat.currentText(),
                    "subtasks": [x.strip() for x in subtasks.text().split(",") if x.strip()],
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
                        tags.text().strip(),
                        notes.toPlainText().strip(),
                        pinned.isChecked(),
                        repeat.currentText(),
                        [x.strip() for x in subtasks.text().split(",") if x.strip()],
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

        self.task_rows = {}
        visible = []
        for task in self.tasks:
            if query and not self.task_matches(task):
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

        pending = self.sort_tasks(pending)
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
        row = TaskRow(self, task)
        self.task_rows[task.get("id")] = row
        self.list_layout.addWidget(row)

    def start_task_drag(self, task_id):
        if getattr(self, "sort_mode", "Smart") != "Manual":
            self.sort_mode = "Manual"
            SETTINGS.setValue("sort_mode", "Manual")
        self.dragging_task_id = task_id
        QApplication.setOverrideCursor(Qt.ClosedHandCursor)
        QTimer.singleShot(0, self.finish_task_drag)

    def finish_task_drag(self):
        if not self.dragging_task_id:
            return
        # A simple, reliable desktop reorder: move the selected task one
        # position toward the end on each drag gesture. The task menu still
        # provides precise up/down movement.
        task_id = self.dragging_task_id
        self.dragging_task_id = None
        QApplication.restoreOverrideCursor()
        index = next((i for i,t in enumerate(self.tasks) if t.get("id")==task_id), None)
        if index is not None and index < len(self.tasks)-1:
            self.record_change()
            self.tasks[index], self.tasks[index+1] = self.tasks[index+1], self.tasks[index]
            self.save()
            self.render()

    def task_menu(self, task_id, button):
        task = self.find_task(task_id)
        if task is None:
            return

        menu = QMenu(self)

        edit = menu.addAction("Edit")
        edit.triggered.connect(
            lambda: self.open_task_dialog(task_id)
        )

        pin_action = menu.addAction(
            "Unpin" if task.get("pinned") else "Pin"
        )
        pin_action.triggered.connect(
            lambda: self.toggle_pin(task_id)
        )

        move_up = menu.addAction("Move up")
        move_up.triggered.connect(lambda: self.move_task(task_id, -1))
        move_down = menu.addAction("Move down")
        move_down.triggered.connect(lambda: self.move_task(task_id, 1))

        menu.addSeparator()

        toggle = menu.addAction(
            "Mark incomplete" if task.get("done") else "Mark complete"
        )
        toggle.triggered.connect(
            lambda: self.toggle(task_id, not task.get("done"))
        )

        snooze = menu.addAction("Snooze 10 min")
        snooze.triggered.connect(lambda: self.snooze_task(task_id, 10))

        menu.addSeparator()

        delete = menu.addAction("Delete")
        delete.triggered.connect(
            lambda: self.remove(task_id)
        )

        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def record_change(self):
        self.undo_stack.append(json.loads(json.dumps(self.tasks)))
        if len(self.undo_stack) > 30:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append(json.loads(json.dumps(self.tasks)))
        self.tasks = self.undo_stack.pop()
        self.save()
        self.render()

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append(json.loads(json.dumps(self.tasks)))
        self.tasks = self.redo_stack.pop()
        self.save()
        self.render()

    def snooze_task(self, task_id, minutes):
        task = self.find_task(task_id)
        if not task:
            return
        self.record_change()
        when = datetime.now() + timedelta(minutes=minutes)
        task["reminder"] = when.strftime("%Y-%m-%d %H:%M")
        task["notified"] = False
        self.save()

    def toggle_pin(self, task_id):
        self.record_change()
        task = self.find_task(task_id)
        if task is None:
            return
        task["pinned"] = not task.get("pinned", False)
        self.save()
        self.render()

    def move_task(self, task_id, direction):
        index = next((i for i, t in enumerate(self.tasks) if t.get("id") == task_id), None)
        if index is None:
            return
        target = index + direction
        if 0 <= target < len(self.tasks):
            self.record_change()
            self.tasks[index], self.tasks[target] = self.tasks[target], self.tasks[index]
            self.save()
            self.render()

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

        self.record_change()
        self.add_parsed_task(text.strip())

    def add_parsed_task(self, raw):
        tokens = raw.split()
        priority = "High" if any(t.lower() in ("high", "!high") for t in tokens) else "Medium"
        category = next((t.split(":",1)[1].title() for t in tokens if t.lower().startswith("category:")), "Personal")
        tags = [t[1:] for t in tokens if t.startswith("#") and len(t) > 1]
        due = ""
        if "today" in [t.lower() for t in tokens]:
            due = date.today().isoformat()
        elif "tomorrow" in [t.lower() for t in tokens]:
            due = (date.today() + timedelta(days=1)).isoformat()
        time_match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", raw)
        reminder = f"{due} {time_match.group(0)}" if due and time_match else ""
        clean = re.sub(r"\b(today|tomorrow|high|!high|category:\S+|[01]?\d:[0-5]\d)\b", "", raw, flags=re.I)
        clean = re.sub(r"\s+", " ", clean).strip()
        self.tasks.append(new_task(
            clean or raw, category if category in CATEGORIES else "Personal",
            priority, due, reminder, ", ".join(tags)
        ))
        self.save()
        self.render()

    def backup_restore_menu(self):
        menu = QMenu(self)
        backup = menu.addAction("Export backup")
        backup.triggered.connect(self.export_backup)
        restore = menu.addAction("Import backup")
        restore.triggered.connect(self.import_backup)
        menu.exec(QCursor.pos()) if False else menu.exec(self.mapToGlobal(QPoint(90, 90)))

    def export_backup(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export Sticky backup", "sticky-backup.json", "JSON (*.json)")
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(self.tasks, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Backup failed", str(exc))

    def import_backup(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import Sticky backup", "", "JSON (*.json)")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
                raise ValueError("Invalid Sticky backup.")
            self.record_change()
            self.tasks = data
            for task in self.tasks:
                task.setdefault("id", uuid.uuid4().hex)
                task.setdefault("done", False)
                task.setdefault("subtasks", [])
                task.setdefault("repeat", "None")
            self.save()
            self.render()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "Restore failed", str(exc))

    def toggle(self, task_id, state):
        task = self.find_task(task_id)
        if task is None:
            return
        old_done = bool(task.get("done"))
        new_done = bool(state)
        if old_done == new_done:
            return
        self.record_change()
        task["done"] = new_done
        if new_done and task.get("repeat") and task.get("repeat") != "None":
            next_task = json.loads(json.dumps(task))
            next_task["id"] = uuid.uuid4().hex
            next_task["done"] = False
            next_task["notified"] = False
            due = task.get("due_date")
            if due:
                qd = QDate.fromString(due, "yyyy-MM-dd")
                if task.get("repeat") == "Daily":
                    qd = qd.addDays(1)
                elif task.get("repeat") == "Weekly":
                    qd = qd.addDays(7)
                elif task.get("repeat") == "Monthly":
                    qd = qd.addMonths(1)
                next_task["due_date"] = qd.toString("yyyy-MM-dd")
                if task.get("reminder"):
                    tm = task["reminder"][-5:]
                    next_task["reminder"] = f'{next_task["due_date"]} {tm}'
            self.tasks.append(next_task)
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
            self.record_change()
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
            self.record_change()
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

        # Never hide the main window while one of its dialogs/message boxes
        # is active. Otherwise Edit/Delete can make the whole app disappear
        # because the dialog becomes the foreground window for a moment.
        modal = QApplication.activeModalWidget()
        while modal is not None:
            if modal is self or modal.parentWidget() is self:
                return
            modal = modal.parentWidget()

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
