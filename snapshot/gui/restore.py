#!/usr/bin/env python
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

import copy
import datetime
import os
import time
import enum

from PyQt5 import QtCore
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QCursor, QGuiApplication
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QPushButton, QHBoxLayout, \
    QFormLayout, QMessageBox, QTreeWidget, QTreeWidgetItem, QMenu, QLineEdit

from ..ca_core import PvStatus, ActionStatus, SnapshotPv
from ..core import background_workers, BackgroundThread, since_start
from ..parser import get_save_files, list_save_files, parse_from_save_file, \
    save_file_suffix
from .utils import SnapshotKeywordSelectorWidget, SnapshotEditMetadataDialog, \
    DetailedMsgBox, show_snapshot_parse_errors


@enum.unique
class FileSelectorColumns(enum.IntEnum):
    filename = 0
    comment = enum.auto()
    labels = enum.auto()
    params = enum.auto()


class FileListScanner(QtCore.QObject, BackgroundThread):
    """Periodically scans the snapshot files to see if any were modified. Emits
    a signal if so."""

    files_changed = QtCore.pyqtSignal()
    _internal_sig = QtCore.pyqtSignal()

    update_rate = 5.  # seconds

    def __init__(self, parent=None):
        super().__init__(parent=parent)

        self._save_dir = None
        self._req_file_name = None
        self._existing_files = None
        self._only_paths = None

        self._internal_sig.connect(self.files_changed,
                                   QtCore.Qt.QueuedConnection)

    def change_paths(self, save_dir, req_file_path):
        with self._lock:
            self._save_dir = save_dir
            self._req_file_name = os.path.basename(req_file_path)

    def change_file_list(self, files):
        # The restore widget stores files as a dict {name: metadata}.
        # We just need full path and modification time.
        with self._lock:
            self._existing_files = {f['file_path']: f['modif_time']
                                    for f in files.values()}
            self._only_paths = set(self._existing_files.keys())

    def _run(self):
        self._periodic_loop(self.update_rate, self._task)

    def _task(self):
        if not all((self._save_dir, self._req_file_name,
                    self._existing_files, self._only_paths)):
            return

        since_start("Started looking for changes in snapshot files")

        file_paths, modif_times = list_save_files(self._save_dir,
                                                  self._req_file_name)
        change_detected = False

        try:
            for path, mtime in zip(file_paths, modif_times):
                if path not in self._existing_files:
                    change_detected = True
                    return
                if mtime != self._existing_files[path]:
                    change_detected = True
                    return

            if any(self._only_paths - set(file_paths)):
                change_detected = True
                return
        finally:
            since_start("Finished looking for changes in snapshot files")
            if change_detected:
                # This is emitted with lock held, do not use a blocking
                # connection or it will deadlock.
                self._internal_sig.emit()


class SnapshotRestoreWidget(QWidget):
    """
    Restore widget is a widget that enables user to restore saved state of PVs
    listed in request file from one of the saved files.
    Save widget consists of:
        - file selector (tree of all files)
        - restore button
        - search/filter

    Data about current app state (such as request file) must be provided as
    part of the structure "common_settings".
    """
    files_selected = QtCore.pyqtSignal(dict)
    files_updated = QtCore.pyqtSignal()
    restored_callback = QtCore.pyqtSignal(dict, bool)

    def __init__(self, snapshot, common_settings, parent=None, **kw):
        QWidget.__init__(self, parent, **kw)

        self.snapshot = snapshot
        self.common_settings = common_settings
        self.filtered_pvs = list()
        # dict of available files to avoid multiple openings of one file when
        # not needed.
        self.file_list = dict()

        # Create main layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self.setLayout(layout)

        # Create list with: file names, comment, labels, machine params
        self.file_selector = SnapshotRestoreFileSelector(snapshot,
                                                         common_settings, self)

        self.file_selector.files_selected.connect(self.handle_selected_files)

        # Make restore buttons
        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.clicked.connect(self.start_refresh)
        self.refresh_button.setToolTip("Refresh .snap files.")
        self.refresh_button.setEnabled(True)

        self.restore_button = QPushButton("Restore Filtered", self)
        self.restore_button.clicked.connect(self.start_restore_filtered)
        self.restore_button.setToolTip("Restores only currently filtered PVs from the selected .snap file.")
        self.restore_button.setEnabled(False)

        self.restore_all_button = QPushButton("Restore All", self)
        self.restore_all_button.clicked.connect(self.start_restore_all)
        self.restore_all_button.setToolTip("Restores all PVs from the selected .snap file.")
        self.restore_all_button.setEnabled(False)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self.refresh_button)
        btn_layout.addWidget(self.restore_all_button)
        btn_layout.addWidget(self.restore_button)

        # Link to status widgets
        self.sts_log = self.common_settings["sts_log"]
        self.sts_info = self.common_settings["sts_info"]

        # Add all widgets to main layout
        layout.addWidget(self.file_selector)
        layout.addLayout(btn_layout)

        self.restored_callback.connect(self.restore_done)

        self.scanner = FileListScanner(parent=self)
        self.scanner.files_changed.connect(self.indicate_refresh_needed)

        self.file_selector.files_updated.connect(self.scanner.change_file_list)
        self.file_selector.files_updated.connect(self.files_updated)

        # Tie starting and stopping the worker thread to starting and
        # stopping of the application.
        app = QtCore.QCoreApplication.instance()
        app.aboutToQuit.connect(self.scanner.stop)
        QtCore.QTimer.singleShot(2 * self.scanner.update_rate * 1000,
                                 self.scanner.start)

    def handle_new_snapshot_instance(self, snapshot, already_parsed_files):
        self.file_selector.handle_new_snapshot_instance(snapshot)
        self.snapshot = snapshot
        self.scanner.change_paths(self.common_settings["save_dir"],
                                  self.common_settings["req_file_path"])
        self.rebuild_file_list(already_parsed_files)

    def indicate_refresh_needed(self):
        self.refresh_button.setStyleSheet('background-color: red;')

    def start_refresh(self):
        self.refresh_button.setStyleSheet('')
        self.rebuild_file_list()

    def start_restore_all(self):
        self.do_restore()

    def start_restore_filtered(self):
        filtered_n = len(self.filtered_pvs)

        if filtered_n == len(self.snapshot.pvs):  # all pvs selected
            self.do_restore()  # This way functions below skip unnecessary checks
        elif filtered_n:
            self.do_restore(self.filtered_pvs)
            # Do not start a restore if nothing to restore

    def do_restore(self, pvs_list=None):
        num_pvs = len(pvs_list) if pvs_list else "ALL"
        response = QMessageBox.question(self, "Confirm restore",
                                        "Do you wish to restore "
                                        f"{num_pvs} PVs?")
        if response != QMessageBox.Yes:
            return

        # Restore can be done only if specific file is selected
        if len(self.file_selector.selected_files) == 1:
            file_data = self.file_selector.file_list.get(self.file_selector.selected_files[0])

            # Prepare pvs with values to restore
            if file_data:
                # Ignore parsing errors: the user has already seen them when
                # when opening the snapshot.
                pvs_in_file, _, _ = \
                    parse_from_save_file(file_data['file_path'])
                pvs_to_restore = copy.copy(pvs_in_file)  # is actually a dict
                macros = self.snapshot.macros

                if pvs_list is not None:
                    for pvname in pvs_in_file.keys():
                        if SnapshotPv.macros_substitution(pvname, macros) not in pvs_list:
                            pvs_to_restore.pop(pvname, None)  # remove unfiltered pvs

                force = self.common_settings["force"]

                # Try to restore with default force mode.
                # First disable restore button (will be enabled when finished)
                # Then Use one of the preloaded saved files to restore
                self.restore_all_button.setEnabled(False)
                self.restore_button.setEnabled(False)

                # Force updating the GUI and disabling the button before future actions
                QtCore.QCoreApplication.processEvents()
                self.sts_log.log_msgs("Restore started.", time.time())
                self.sts_info.set_status("Restoring ...", 0, "orange")

                status, pvs_status = self.snapshot.restore_pvs(pvs_to_restore, callback=self.restore_done_callback,
                                                               force=force)

                if status == ActionStatus.no_conn:
                    # Ask user if he wants to force restoring
                    msg = "Some PVs are not connected (see details). Do you want to restore anyway?\n"
                    msg_window = DetailedMsgBox(msg, "\n".join(list(pvs_status.keys())), 'Warning', self)
                    reply = msg_window.exec_()

                    if reply != QMessageBox.No:
                        # Force restore
                        status, pvs_status = self.snapshot.restore_pvs(pvs_to_restore,
                                                                       callback=self.restore_done_callback, force=True)

                        # If here restore started successfully. Waiting for callbacks.

                    else:
                        # User rejected restoring with unconnected PVs. Not an error state.
                        self.sts_log.log_msgs("Restore rejected by user.", time.time())
                        self.sts_info.clear_status()
                        self.restore_all_button.setEnabled(True)
                        self.restore_button.setEnabled(True)

                elif status == ActionStatus.no_data:
                    self.sts_log.log_msgs("ERROR: Nothing to restore.", time.time())
                    self.sts_info.set_status("Restore rejected", 3000, "#F06464")
                    self.restore_all_button.setEnabled(True)
                    self.restore_button.setEnabled(True)

                elif status == ActionStatus.busy:
                    self.sts_log.log_msgs("ERROR: Restore rejected. Previous restore not finished.", time.time())
                    self.restore_all_button.setEnabled(True)
                    self.restore_button.setEnabled(True)

                    # else: ActionStatus.ok  --> waiting for callbacks

            else:
                # Problem reading data from file
                warn = "Cannot start a restore. Problem reading data from selected file."
                QMessageBox.warning(self, "Warning", warn,
                                          QMessageBox.Ok,
                                          QMessageBox.NoButton)
                self.restore_all_button.setEnabled(True)
                self.restore_button.setEnabled(True)

        else:
            # Don't start a restore if file not selected
            warn = "Cannot start a restore. File with saved values is not selected."
            QMessageBox.warning(self, "Warning", warn,
                                      QMessageBox.Ok,
                                      QMessageBox.NoButton)
            self.restore_all_button.setEnabled(True)
            self.restore_button.setEnabled(True)

    def restore_done_callback(self, status, forced, **kw):
        # Raise callback to handle GUI specifics in GUI thread
        self.restored_callback.emit(status, forced)

    def restore_done(self, status, forced):
        # When snapshot finishes restore, GUI must be updated with
        # status of the restore action.
        error = False
        msgs = list()
        msg_times = list()
        status_txt = ""
        status_background = ""
        for pvname, sts in status.items():
            if sts == PvStatus.access_err:
                error = not forced  # if here and not in force mode, then this is error state
                msgs.append("WARNING: {}: Not restored (no connection or no write access).".format(pvname))
                msg_times.append(time.time())
                status_txt = "Restore error"
                status_background = "#F06464"

            elif sts == PvStatus.type_err:
                error = True
                msgs.append("WARNING: {}: Not restored (type problem).".format(pvname))
                msg_times.append(time.time())
                status_txt = "Restore error"
                status_background = "#F06464"

        self.sts_log.log_msgs(msgs, msg_times)

        if not error:
            self.sts_log.log_msgs("Restore finished.", time.time())
            status_txt = "Restore done"
            status_background = "#64C864"

        # Enable button when restore is finished
        self.restore_all_button.setEnabled(True)
        self.restore_button.setEnabled(True)

        if status_txt:
            self.sts_info.set_status(status_txt, 3000, status_background)

    def handle_selected_files(self, selected_files):
        """
        Handle sub widgets and emits signal when files are selected.

        :param selected_files: list of selected file names
        :return:
        """
        if len(selected_files) == 1:
            self.restore_all_button.setEnabled(True)
            self.restore_button.setEnabled(True)
        else:
            self.restore_all_button.setEnabled(False)
            self.restore_button.setEnabled(False)

        selected_data = dict()
        for file_name in selected_files:
            file_data = self.file_selector.file_list.get(file_name, None)
            if file_data:
                selected_data[file_name] = file_data

        # First update other GUI components (compare widget) and then pass pvs to compare to the snapshot core
        self.files_selected.emit(selected_data)

    def rebuild_file_list(self, already_parsed_files=None):
        self.file_selector.rebuild_file_list(already_parsed_files)


class SnapshotRestoreFileSelector(QWidget):
    """
    Widget for visual representation (and selection) of existing saved_value
    files.
    """

    files_selected = QtCore.pyqtSignal(list)
    files_updated = QtCore.pyqtSignal(dict)

    def __init__(self, snapshot, common_settings, parent=None, **kw):
        QWidget.__init__(self, parent, **kw)

        self.snapshot = snapshot
        self.selected_files = list()
        self.common_settings = common_settings

        self.file_list = dict()
        self.pvs = dict()

        # Filter handling
        self.file_filter = dict()
        self.file_filter["keys"] = list()
        self.file_filter["comment"] = ""

        self.filter_input = SnapshotFileFilterWidget(
            self.common_settings, self)

        self.filter_input.file_filter_updated.connect(self.filter_file_list_selector)

        # Create list with: file names, comment, labels, machine params.
        # This is done with a single-level QTreeWidget instead of QTableWidget
        # because it is line-oriented whereas a table is cell-oriented.
        self.file_selector = QTreeWidget(self)
        self.file_selector.setRootIsDecorated(False)
        self.file_selector.setUniformRowHeights(True)
        self.file_selector.setIndentation(0)
        self.file_selector.setColumnCount(FileSelectorColumns.params)
        self.column_labels = ["File name", "Comment", "Labels"]
        self.file_selector.setHeaderLabels(self.column_labels)
        self.file_selector.setAllColumnsShowFocus(True)
        self.file_selector.setSortingEnabled(True)
        # Sort by file name (alphabetical order)
        self.file_selector.sortItems(FileSelectorColumns.filename,
                                     Qt.DescendingOrder)

        self.file_selector.itemSelectionChanged.connect(self.select_files)
        self.file_selector.setContextMenuPolicy(Qt.CustomContextMenu)
        self.file_selector.customContextMenuRequested.connect(self.open_menu)

        # Set column sizes
        self.file_selector.resizeColumnToContents(FileSelectorColumns.filename)
        self.file_selector.setColumnWidth(FileSelectorColumns.comment, 350)

        # Applies following behavior for multi select:
        #   click            selects only current file
        #   Ctrl + click     adds current file to selected files
        #   Shift + click    adds all files between last selected and current
        #                    to selected
        self.file_selector.setSelectionMode(QTreeWidget.ExtendedSelection)

        self.filter_file_list_selector()

        # Add to main layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.filter_input)
        layout.addWidget(self.file_selector)

    def handle_new_snapshot_instance(self, snapshot):
        self.clear_file_selector()
        self.filter_input.clear()
        self.snapshot = snapshot

    def rebuild_file_list(self, already_parsed_files=None):
        background_workers.suspend()
        self.clear_file_selector()
        self.file_selector.setSortingEnabled(False)
        if already_parsed_files:
            save_files, err_to_report = already_parsed_files
        else:
            save_dir = self.common_settings["save_dir"]
            req_file_path = self.common_settings["req_file_path"]
            save_files, err_to_report = get_save_files(save_dir, req_file_path)

        self._update_file_list_selector(save_files)
        self.filter_file_list_selector()

        # Report any errors with snapshot files to the user
        if err_to_report:
            show_snapshot_parse_errors(self, err_to_report)

        self.file_selector.setSortingEnabled(True)
        self.files_updated.emit(save_files)
        background_workers.resume()

    def _update_file_list_selector(self, file_list):
        new_labels = set()
        new_params = set()
        for new_file, new_data in file_list.items():
            meta_data = new_data["meta_data"]
            labels = meta_data.get("labels", [])
            params = meta_data.get("machine_params", {})

            assert(new_file not in self.file_list)
            new_labels.update(labels)
            new_params.update(params.keys())

        new_labels = list(new_labels)
        new_params = list(new_params)
        all_params = self.common_settings['machine_params'] + \
            [p for p in new_params
             if p not in self.common_settings['machine_params']]

        for new_file, new_data in file_list.items():
            meta_data = new_data["meta_data"]
            labels = meta_data.get("labels", [])
            params = meta_data.get("machine_params", {})
            comment = meta_data.get("comment", "")

            row = [new_file, comment, " ".join(labels)]
            assert(len(row) == FileSelectorColumns.params)
            param_vals = [None] * len(all_params)
            for p, v in params.items():
                idx = all_params.index(p)
                param_vals[idx] = str(v)
            selector_item = QTreeWidgetItem(row + param_vals)
            self.file_selector.addTopLevelItem(selector_item)
            self.file_list[new_file] = new_data
            self.file_list[new_file]["file_selector"] = selector_item

        self.common_settings["existing_labels"] = new_labels
        self.common_settings["existing_params"] = new_params
        self.filter_input.update_params()

        self.file_selector.setHeaderLabels(self.column_labels + all_params)
        for col in range(self.file_selector.columnCount()):
            self.file_selector.resizeColumnToContents(col)

    def filter_file_list_selector(self):
        file_filter = self.filter_input.file_filter

        for file_name in self.file_list:
            file_line = self.file_list[file_name]["file_selector"]
            file_to_filter = self.file_list.get(file_name)

            if not file_filter:
                file_line.setHidden(False)
            else:
                keys_filter = file_filter.get("keys")
                comment_filter = file_filter.get("comment")
                name_filter = file_filter.get("name")

                if keys_filter:
                    keys_status = False
                    for key in file_to_filter["meta_data"]["labels"]:
                        # Break when first found
                        if key and (key in keys_filter):
                            keys_status = True
                            break
                else:
                    keys_status = True

                if comment_filter:
                    comment_status = comment_filter in file_to_filter["meta_data"]["comment"]
                else:
                    comment_status = True

                if name_filter:
                    name_status = name_filter in file_name
                else:
                    name_status = True

                # Set visibility if any of the filters conditions met
                file_line.setHidden(
                    not (name_status and keys_status and comment_status))

    def open_menu(self, point):
        item_idx = self.file_selector.indexAt(point)
        if not item_idx.isValid():
            return

        text = item_idx.data()
        field = self.file_selector.model().headerData(item_idx.column(),
                                                      Qt.Horizontal)
        clipboard = QGuiApplication.clipboard()

        menu = QMenu(self)
        if item_idx.column() < FileSelectorColumns.params:
            menu.addAction(f"Copy {field.lower()}",
                           lambda: clipboard.setText(text))
        else:
            menu.addAction(f"Copy {field} name",
                           lambda: clipboard.setText(field))
            menu.addAction(f"Copy {field} value",
                           lambda: clipboard.setText(text))

        menu.addAction("Delete selected files", self.delete_files)
        menu.addAction("Edit file meta-data", self.update_file_metadata)

        menu.exec(QCursor.pos())
        menu.deleteLater()

    def select_files(self):
        # Pre-process selected items, to a list of files
        self.selected_files = list()
        if self.file_selector.selectedItems():
            for item in self.file_selector.selectedItems():
                self.selected_files.append(
                    item.text(FileSelectorColumns.filename))

        self.files_selected.emit(self.selected_files)

    def delete_files(self):
        if self.selected_files:
            msg = "Do you want to delete selected files?"
            reply = QMessageBox.question(self, 'Message', msg, QMessageBox.Yes, QMessageBox.No)
            if reply == QMessageBox.Yes:
                background_workers.suspend()
                symlink_file = self.common_settings["save_file_prefix"] \
                    + 'latest' + save_file_suffix
                symlink_path = os.path.join(self.common_settings["save_dir"],
                                            symlink_file)
                symlink_target = os.path.realpath(symlink_path)

                files = self.selected_files[:]
                paths = [os.path.join(self.common_settings["save_dir"],
                                      selected_file)
                         for selected_file in self.selected_files]

                if any((path == symlink_target for path in paths)) \
                   and symlink_file not in files:
                    files.append(symlink_file)
                    paths.append(symlink_path)

                for selected_file, file_path in zip(files, paths):
                    try:
                        os.remove(file_path)
                        self.file_list.pop(selected_file)
                        self.pvs = dict()
                        items = self.file_selector.findItems(
                            selected_file, Qt.MatchCaseSensitive,
                            FileSelectorColumns.filename)
                        self.file_selector.takeTopLevelItem(
                            self.file_selector.indexOfTopLevelItem(items[0]))

                    except OSError as e:
                        warn = "Problem deleting file:\n" + str(e)
                        QMessageBox.warning(self, "Warning", warn,
                                                  QMessageBox.Ok,
                                                  QMessageBox.NoButton)
                self.files_updated.emit(self.file_list)
                background_workers.resume()

    def update_file_metadata(self):
        if self.selected_files:
            if len(self.selected_files) == 1:
                settings_window = SnapshotEditMetadataDialog(
                    self.file_list.get(self.selected_files[0])["meta_data"],
                    self.common_settings, self)
                settings_window.resize(800, 200)
                # if OK was pressed, update actual file and reflect changes in the list
                if settings_window.exec_():
                    background_workers.suspend()
                    file_data = self.file_list.get(self.selected_files[0])
                    try:
                        self.snapshot.replace_metadata(file_data['file_path'],
                                                       file_data['meta_data'])
                    except OSError as e:
                        warn = "Problem modifying file:\n" + str(e)
                        QMessageBox.warning(self, "Warning", warn,
                                            QMessageBox.Ok,
                                            QMessageBox.NoButton)

                    self.rebuild_file_list()
                    background_workers.resume()
            else:
                QMessageBox.information(self, "Information", "Please select one file only",
                                              QMessageBox.Ok,
                                              QMessageBox.NoButton)

    def clear_file_selector(self):
        self.file_selector.clear()  # Clears and "deselects" itmes on file selector
        self.select_files()  # Process new,empty list of selected files
        self.pvs = dict()
        self.file_list = dict()


class SnapshotFileFilterWidget(QWidget):
    """
        Is a widget with 3 filter options:
            - by time (removed)
            - by comment
            - by labels
            - by name

        Emits signal: filter_changed when any of the filter changed.
    """
    file_filter_updated = QtCore.pyqtSignal()

    def __init__(self, common_settings, parent=None, **kw):
        QWidget.__init__(self, parent, **kw)

        self.common_settings = common_settings
        # Create main layout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        left_layout = QFormLayout()
        right_layout = QFormLayout()
        layout.addLayout(left_layout)
        layout.addLayout(right_layout)

        # Create filter selectors (with readbacks)
        # - text input to filter by name
        # - text input to filter comment
        # - labels selector

        # Init filters
        self.file_filter = dict()
        self.file_filter["keys"] = list()
        self.file_filter["comment"] = ""
        self.file_filter["name"] = ""

        # Labels filter
        self.keys_input = SnapshotKeywordSelectorWidget(
            self.common_settings, parent=self)  # No need to force defaults
        self.keys_input.setPlaceholderText("label_1 label_2 ...")
        self.keys_input.keywords_changed.connect(self.update_filter)
        right_layout.addRow("Labels:", self.keys_input)

        # Params filter
        # TODO this is only a mockup
        self.param_input = QLineEdit()
        self.param_input.setText("Param input mockup")
        right_layout.addRow("Params:", self.param_input)

        # File name filter
        self.name_input = QLineEdit(self)
        self.name_input.setPlaceholderText("Filter by name")
        self.name_input.textChanged.connect(self.update_filter)
        left_layout.addRow("Name:", self.name_input)

        # Comment filter
        self.comment_input = QLineEdit(self)
        self.comment_input.setPlaceholderText("Filter by comment")
        self.comment_input.textChanged.connect(self.update_filter)
        left_layout.addRow("Comment:", self.comment_input)

    def update_filter(self):
        if self.keys_input.get_keywords():
            self.file_filter["keys"] = self.keys_input.get_keywords()
        else:
            self.file_filter["keys"] = list()
        self.file_filter["comment"] = self.comment_input.text().strip('')
        self.file_filter["name"] = self.name_input.text().strip('')

        self.file_filter_updated.emit()

    def update_params(self):
        self.keys_input.update_suggested_keywords()

    def clear(self):
        self.keys_input.clear_keywords()
        self.name_input.setText('')
        self.comment_input.setText('')
        self.update_filter()
