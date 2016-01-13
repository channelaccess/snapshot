#!/usr/bin/env python
import sys
from PyQt4 import QtGui, QtCore
from PyQt4.QtCore import pyqtSlot, Qt, SIGNAL
import time
import datetime
import argparse
from enum import Enum

from snapshot import *

# close with ctrl+C
import signal
signal.signal(signal.SIGINT, signal.SIG_DFL)


# Define enums
class PvViewStatus(Enum):
    eq = 0
    neq = 1
    err = 2


class PvCompareFilter(Enum):
    show_all = 0
    show_neq = 1
    show_eq = 2


class SnapshotGui(QtGui.QWidget):

    '''
    Main GUI class for Snapshot application. It needs separate working
    thread where core of the application is running
    '''

    def __init__(self, worker, req_file_name, req_file_macros=None,
                 save_dir=None, save_file_dft=None, mode=None, parent=None):
        QtGui.QWidget.__init__(self, parent)
        self.worker = worker
        self.setMinimumSize(1100, 900)

        # common_settings is a dictionary which holds common configuration of
        # the application (such as directory with save files, request file
        # path, etc). It is propagated to other snapshot widgets if needed

        self.common_settings = dict()
        self.common_settings["req_file_name"] = req_file_name
        self.common_settings["req_file_macros"] = req_file_macros
        if not save_dir:
            # Set current dir as save dir
            save_dir = os.path.dirname(os.path.realpath(__file__))

        self.common_settings["save_dir"] = save_dir
        self.common_settings["save_file_dft"] = save_file_dft

        self.common_settings["pvs_to_restore"] = list()

        # Before first interaction with the worker object list all signals
        # on which action must be taken
        self.connect(self.worker, SIGNAL("save_done(PyQt_PyObject,PyQt_PyObject)"),
                     self.save_done)
        self.connect(self.worker, SIGNAL("restore_done(PyQt_PyObject)"),
                     self.restore_done)
        self.connect(self.worker, SIGNAL("new_snapshot_loaded(PyQt_PyObject)"),
                     self.handle_new_snapshot)

        # Before creating GUI, snapshot must be initialized. This must be
        # blocking operation. Cannot proceed without snapshot instance.
        QtCore.QMetaObject.invokeMethod(self.worker, "init_snapshot",
                                        Qt.BlockingQueuedConnection,
                                        QtCore.Q_ARG(
                                            str, self.common_settings["req_file_name"]),
                                        QtCore.Q_ARG(dict, self.common_settings["req_file_macros"]))

        # Create snapshot GUI:
        # Snapshot gui consists of two tabs: "Save" and "Restore" default
        # is selected depending on mode parameter TODO
        main_layout = QtGui.QVBoxLayout(self)
        self.setLayout(main_layout)
        tabs = QtGui.QTabWidget(self)
        tabs.setMinimumSize(900, 450)
        main_layout.addWidget(tabs)

        # Each tab has it's own widget. Need one for save and one for restore.
        self.save_widget = SnapshotSaveWidget(self.worker,
                                              self.common_settings, tabs)
        self.restore_widget = SnapshotRestoreWidget(self.worker,
                                                    self.common_settings, tabs)

        tabs.addTab(self.save_widget, "Save")
        tabs.addTab(self.restore_widget, "Restore")

        # Show GUI and manage window properties
        self.show()
        self.setWindowTitle('Snapshot')

    def make_file_list(self, file_list):
        # Just pass data to Restore widget
        self.restore_widget.make_file_list(file_list)

    def save_done(self, file_path, status):
        # When save is done, save widget is updated by itself
        # Update restore widget (new file in directory)
        # TODO report status, update restore widget with new file
        self.restore_widget.start_file_list_update()
        print("Save done")

    def restore_done(self, status):
        # TODO report status
        print("Restore done")

    def handle_new_snapshot(self, pvs_names_list):
        # This function should do anything that is needed when new worker
        # creates new instance of snapshot

        # Store pv names list to common settings and call all widgets that need to
        # be updated
        self.common_settings["pvs_to_restore"] = pvs_names_list
        self.restore_widget.compare_widget.create_compare_list()
        self.restore_widget.compare_widget.create_compare_list()


class SnapshotSaveWidget(QtGui.QWidget):

    '''
    Save widget is a widget that enables user to save current state of PVs
    listed in request file. Widget includes:
    Save widget consists of:
     - input-fields:
        * file extension (default YYMMDD_hhmm)
        * comment
        * keywords
     - read-back showing whole file name
     - Save button

    Data about current app state (such as request file) must be provided as
    part of the structure "common_settings".

    '''

    def __init__(self, worker, common_settings, parent=None, **kw):
        QtGui.QWidget.__init__(self, parent, **kw)

        self.common_settings = common_settings
        self.worker = worker

        # Default saved file name: If req file name is PREFIX.req, then saved
        # file name is: PREFIX_YYMMDD_hhmm (holds time info)
        self.name_base = os.path.split(
            common_settings["req_file_name"])[1].split(".")[0] + "_"
        self.name_extension = datetime.datetime.fromtimestamp(
            time.time()).strftime('%Y%m%d_%H%M')

        self.file_path = os.path.join(self.common_settings["save_dir"],
                                      self.name_base + self.name_extension)

        # Before creating elements that can use worker to signals from working
        # thread that are response of this widget actions. If this widget must
        # be updated by other widget actions, catch appropriate signals outside
        # and call methods from outside.
        #
        # * "save_done" is response of the worker when save is finished. Returns
        #   (file_path, status).
        self.connect(self.worker, SIGNAL("save_done(PyQt_PyObject,PyQt_PyObject)"),
                     self.save_done)

        # Create layout and add GUI elements (input fields, buttons, ...)
        layout = QtGui.QVBoxLayout(self)
        layout.setMargin(10)
        layout.setSpacing(10)
        self.setLayout(layout)
        min_label_width = 120

        # Make a field to select file extension (has a read-back)
        extension_layout = QtGui.QHBoxLayout()
        extension_layout.setSpacing(10)
        extension_label = QtGui.QLabel("Name extension:", self)
        extension_label.setAlignment(Qt.AlignCenter | Qt.AlignRight)
        extension_label.setMinimumWidth(min_label_width)
        self.extension_input = QtGui.QLineEdit(self)
        # Monitor any changes (by user, or by other methods)
        self.extension_input.textChanged.connect(self.update_name)
        file_name_label = QtGui.QLabel("File name: ", self)
        self.file_name_rb = QtGui.QLabel(self)
        self.file_name_rb.setMinimumWidth(300)
        self.extension_input.setText(self.name_extension)
        extension_layout.addWidget(extension_label)
        extension_layout.addWidget(self.extension_input)
        extension_layout.addWidget(file_name_label)
        extension_layout.addWidget(self.file_name_rb)

        # Make a field to enable user adding a comment
        comment_layout = QtGui.QHBoxLayout()
        comment_layout.setSpacing(10)
        comment_label = QtGui.QLabel("Comment:", self)
        comment_label.setAlignment(Qt.AlignCenter | Qt.AlignRight)
        comment_label.setMinimumWidth(min_label_width)
        self.comment_input = QtGui.QLineEdit(self)
        comment_layout.addWidget(comment_label)
        comment_layout.addWidget(self.comment_input)

        # Make field for keywords
        keyword_layout = QtGui.QHBoxLayout()
        keyword_layout.setSpacing(10)
        keyword_label = QtGui.QLabel("Keywords:", self)
        keyword_label.setAlignment(Qt.AlignCenter | Qt.AlignRight)
        keyword_label.setMinimumWidth(min_label_width)
        self.keyword_input = QtGui.QLineEdit(self)
        keyword_layout.addWidget(keyword_label)
        keyword_layout.addWidget(self.keyword_input)

        # Make Save button, status indicator and save report
        save_layout = QtGui.QHBoxLayout()
        save_layout.setSpacing(10)
        self.save_button = QtGui.QPushButton("Save", self)
        self.save_button.clicked.connect(self.start_save)
        self.save_sts = QtGui.QLabel(self)
    
        self.save_sts.setMaximumWidth(200)
        self.save_sts.setMaximumHeight(30)
        self.save_sts.setMargin(5)
        self.save_sts.setStyleSheet("background-color : white")
        save_layout.addWidget(self.save_button)
        save_layout.addWidget(self.save_sts)

        # Add to main layout
        layout.addItem(extension_layout)
        layout.addItem(comment_layout)
        layout.addItem(keyword_layout)
        layout.addItem(save_layout)

        # Widget properties
        self.setMaximumHeight(180)

    def start_save(self):
        # Disable button for the time of saving. Will be unlocked when save is
        # finished.
        self.save_button.setEnabled(False)
        self.save_sts.setText("Saving ...")
        self.save_sts.setStyleSheet("background-color : orange")
        QtCore.QMetaObject.invokeMethod(self.worker, "save_pvs",
                                        Qt.QueuedConnection,
                                        QtCore.Q_ARG(str, self.file_path),
                                        QtCore.Q_ARG(
                                            str, self.keyword_input.text()),
                                        QtCore.Q_ARG(str, self.comment_input.text()))

    def save_done(self, file_path, status):
        # Enable saving
        self.save_button.setEnabled(True)
        self.save_sts.setText("Saving done")
        self.save_sts.setStyleSheet("background-color : #64C864")
        # bad status
        #self.save_sts.setStyleSheet("background-color : #F06464")

    def update_name(self):
        self.name_extension = self.extension_input.text()
        self.file_path = os.path.join(self.common_settings["save_dir"],
                                      self.name_base + self.name_extension)
        self.file_name_rb.setText(self.name_base + self.name_extension)


class SnapshotRestoreWidget(QtGui.QWidget):

    '''
    restore widget is a widget that enables user to restore saved state of PVs
    listed in request file from one of the saved files.
    Save widget consists of:
     - file selector (tree of all files)
     - restore button
     TODO add meta data searcher/filter

    It also owns a compare widget.

    Data about current app state (such as request file) must be provided as
    part of the structure "common_settings".

    '''

    def __init__(self, worker, common_settings, parent=None, **kw):
        QtGui.QWidget.__init__(self, parent, **kw)

        self.worker = worker
        self.common_settings = common_settings
        # dict of available files to avoid multiple openings of one file when
        # not needed. It is shared with worker thread (currently only for
        # reading)
        self.file_list = dict()

        # Before creating elements that can use worker, connect to signals from
        # working thread that are response of this widget actions. If this
        # widget must be updated by other widget actions, catch appropriate
        # signals outside and call methods from outside.
        #
        # * "restore_done" is response of the worker when restore is finished.
        #   Returns (file_path, status)
        # * "save_files_loaded" response of the worker when file parsing done.
        #   Returns (file_list)
        self.connect(self.worker, SIGNAL("restore_done(PyQt_PyObject)"),
                     self.restore_done)
        self.connect(self.worker, SIGNAL("save_files_loaded(PyQt_PyObject)"),
                     self.update_file_list_selector)

        # Create main layout
        layout = QtGui.QVBoxLayout(self)
        layout.setMargin(10)
        layout.setSpacing(10)
        self.setLayout(layout)

        # Create list with: file names, keywords, comments
        self.file_selector = QtGui.QTreeWidget(self)
        self.file_selector.setColumnCount(3)
        self.file_selector.setHeaderLabels(["File", "Keywords", "Comment"])
        self.file_selector.header().resizeSection(0, 300)
        self.file_selector.header().resizeSection(1, 300)
        self.file_selector.setAlternatingRowColors(True)
        self.file_selector.itemSelectionChanged.connect(self.choose_file)

        # Make restore button, status indicator and restore report
        restore_layout = QtGui.QHBoxLayout()
        restore_layout.setSpacing(10)
        self.restore_button = QtGui.QPushButton("Restore", self)
        self.restore_button.clicked.connect(self.start_restore)
        self.restore_sts = QtGui.QLabel(self)
        self.restore_sts.setMaximumWidth(200)
        self.restore_sts.setMaximumHeight(30)
        self.restore_sts.setMargin(5)
        self.restore_sts.setStyleSheet("background-color : white")
        restore_layout.addWidget(self.restore_button)
        restore_layout.addWidget(self.restore_sts)

        # Add all widgets to main layout
        layout.addWidget(self.file_selector)
        layout.addItem(restore_layout)

        # Create file list for first time (this is done  by worker)
        self.start_file_list_update()

        # Compare widget
        self.compare_widget = SnapshotCompareWidget(self.worker,
                                                    self.common_settings, self)

        layout.addWidget(self.compare_widget)

    def start_restore(self):
        # First disable restore button (will be enabled when finished)
        # Then Use one of the preloaded saved files to restore
        self.restore_button.setEnabled(False)
        QtCore.QMetaObject.invokeMethod(self.worker,
                                        "restore_pvs",
                                        Qt.QueuedConnection)

    def restore_done(self, status):
        # Enable button when restore is finished (worker notifies)
        self.restore_button.setEnabled(True)
        self.restore_sts.setText("Restore done")
        self.restore_sts.setStyleSheet("background-color : #64C864")
        # bad status
        #self.restore_sts.setStyleSheet("background-color : #F06464")

    def start_file_list_update(self):
        # Rescans directory and adds new/modified files and removes none
        # existing ones from the list.

        # set prefix of the files (do outside invokeMethod because looks less
        # ugly :D)
        file_prefix = os.path.split(
            self.common_settings["req_file_name"])[1].split(".")[0] + "_"

        QtCore.QMetaObject.invokeMethod(self.worker, "get_save_files",
                                        Qt.QueuedConnection,
                                        QtCore.Q_ARG(
                                            str, self.common_settings["save_dir"]),
                                        QtCore.Q_ARG(str, file_prefix),
                                        QtCore.Q_ARG(dict, self.file_list))

    def choose_file(self):
        pvs = self.file_list[
            self.file_selector.selectedItems()[0].text(0)]["pvs_list"]
        QtCore.QMetaObject.invokeMethod(self.worker,
                                        "load_pvs_to_snapshot",
                                        Qt.QueuedConnection,
                                        QtCore.Q_ARG(dict, pvs))

    def update_file_list_selector(self, file_list):
        for key in file_list:
            keywords = file_list[key]["meta_data"].get("keywords", "")
            comment = file_list[key]["meta_data"].get("comment", "")

            # check if already on list (was just modified) and modify file
            # selctor
            if key not in self.file_list:
                self.file_selector.addTopLevelItem(
                    QtGui.QTreeWidgetItem([key, keywords, comment]))
            else:
                # If everything ok only one file should exist in list
                to_modify = self.file_selector.findItems(
                    key, Qt.MatchCaseSensitive, 0)[0]
                to_modify.setText(1, keywords)
                to_modify.setText(2, comment)

            self.file_list[key] = file_list[key]

        # Sort by file name (alphabetical order)
        self.file_selector.sortItems(0, Qt.AscendingOrder)


class SnapshotCompareWidget(QtGui.QWidget):

    """

    Widget for live comparing pv values. All infos about PVs that needs to be
    monitored are already in the "snapshot" object controlled by worker. They
    were loaded with

    """

    def __init__(self, worker, common_settings, parent=None, **kw):
        QtGui.QWidget.__init__(self, parent, **kw)

        self.worker = worker
        self.common_settings = common_settings

        # Before creating elements that can use worker to signals from working
        # thread that are response of this widget actions. If this widget must
        # be updated by other widget actions, catch appropriate signals outside
        # and call methods from outside.
        self.connect(self.worker, SIGNAL("pv_changed(PyQt_PyObject)"),
                     self.update_pv)

        # Create main layout
        layout = QtGui.QVBoxLayout(self)
        layout.setMargin(10)
        layout.setSpacing(10)
        self.setLayout(layout)

        # Create filter selectors
        # - text input to filter by name
        # - drop down to filter by compare status
        # - check box to select if showing pvs with incomplete data
        filter_layout = QtGui.QHBoxLayout()
        pv_filter_layout = QtGui.QHBoxLayout()
        pv_filter_layout.setSpacing(10)
        pv_filter_label = QtGui.QLabel("Filter:", self)
        pv_filter_label.setAlignment(Qt.AlignCenter | Qt.AlignRight)
        self.pv_filter_inp = QtGui.QLineEdit(self)
        self.pv_filter_inp.textChanged.connect(self.filter_list)

        pv_filter_layout.addWidget(pv_filter_label)
        pv_filter_layout.addWidget(self.pv_filter_inp)

        self.compare_filter_inp = QtGui.QComboBox(self)
        self.compare_filter_inp.addItems(
            ["Show all", "Not equal only", "Equal only"])
        self.compare_filter_inp.currentIndexChanged.connect(self.filter_list)
        self.compare_filter_inp.setMaximumWidth(200)
        self.completnes_filter_inp = QtGui.QCheckBox(
            "Show PVs with incomplete data.", self)
        self.completnes_filter_inp.setChecked(True)
        self.completnes_filter_inp.stateChanged.connect(self.filter_list)
        self.completnes_filter_inp.setMaximumWidth(500)

        filter_layout.addItem(pv_filter_layout)
        filter_layout.addWidget(self.compare_filter_inp)
        filter_layout.addWidget(self.completnes_filter_inp)
        filter_layout.setAlignment(Qt.AlignLeft)
        filter_layout.setSpacing(10)

        # Create list where each line presents one PV with data:
        # - pv name
        # - current pv value
        # - saved pv value
        # - status string
        self.pv_view = QtGui.QTreeWidget(self)
        self.pv_view.setColumnCount(4)
        self.pv_view.setHeaderLabels(
            ["PV", "Current value", "Saved value", "Status"])
        self.pv_view.header().resizeSection(0, 400)
        self.pv_view.header().resizeSection(1, 200)
        self.pv_view.header().resizeSection(2, 100)
        self.pv_view.setAlternatingRowColors(True)

        # Add all widgets to main layout
        # TODO add filter selector widget
        layout.addItem(filter_layout)
        layout.addWidget(self.pv_view)

        # fill the compare view and start comparing
        self.create_compare_list()
        self.start_compare()

        # Disable possibility to select item in the compare list
        self.pv_view.setSelectionMode(QtGui.QAbstractItemView.NoSelection)
        self.pv_view.setFocusPolicy(Qt.NoFocus)

    def create_compare_list(self):
        '''
        Create tree item for each PV. List of pv names was returned after
        parsing the request file. Attributes except PV name are empty at
        init. Will be updated when monitor happens, snapshot object will
        raise a callback which is then catched in worker and passed with
        signal. TODO which function handles.
        '''

        # First remove all existing entries
        while self.pv_view.topLevelItemCount() > 0:
            self.pv_view.takeTopLevelItem(0)

        for pv_name in self.common_settings["pvs_to_restore"]:
            saved_val = ""
            status = ""
            curr_val = ""
            pv_line = CompareTreeWidgetItem(pv_name, self.pv_view)
            self.pv_view.addTopLevelItem(pv_line)
        # Sort by name (alphabetical order)
        self.pv_view.sortItems(0, Qt.AscendingOrder)

    def filter_list(self):
        # Just pass the filter conditions to all items in the list. # Use
        # values directly from GUI elements (filter selectors).
        for i in range(self.pv_view.topLevelItemCount()):
            curr_item = self.pv_view.topLevelItem(i)
            curr_item.apply_filter(self.compare_filter_inp.currentIndex(),
                                   self.completnes_filter_inp.isChecked(),
                                   self.pv_filter_inp.text())

    def start_compare(self):
        # Just invoke worker, to set snapshot sending a callbacks
        QtCore.QMetaObject.invokeMethod(self.worker, "start_continous_compare",
                                        Qt.QueuedConnection)

    def update_pv(self, data):
        # If everything ok, only one line should match
        line_to_update = self.pv_view.findItems(
            data["pv_name"], Qt.MatchCaseSensitive, 0)[0]

        line_to_update.update_state(**data)


class CompareTreeWidgetItem(QtGui.QTreeWidgetItem):

    '''
    Extended to hold last info about connection status and value. Also
    implements methods to set visibility according to filter
    '''

    def __init__(self, pv_name, parent=None):
        # Item with [pv_name, current_value, saved_value, status]
        QtGui.QTreeWidgetItem.__init__(self, parent, [pv_name, "", "", ""])
        self.pv_name = pv_name

        # Have data stored in native types, for easier filtering etc.
        self.connect_sts = None
        self.saved_value = None
        self.value = None
        self.compare = None
        self.has_error = None

        # Variables to hold current filter. Whenever filter is applied they are
        # updated. When filter is applied from items own metods (like
        # update_state), this stored values are used.
        self.compare_filter = 0
        self.completeness_filter = True
        self.name_filter = None

    def update_state(self, pv_value, pv_saved, pv_compare, pv_cnct_sts, saved_sts, **kw):
        self.connect_sts = pv_cnct_sts
        # indicates if list of saved PVs loaded to snapshot
        self.saved_sts = saved_sts
        self.saved_value = pv_saved
        self.value = pv_value
        self.compare = pv_compare
        self.has_error = False

        if not self.connect_sts:
            self.setText(1, "")  # no connection means no value
            self.setText(3, "PV not connected!")
            self.has_error = True
        else:
            if isinstance(self.value, (numpy.ndarray)):
                self.setText(1, json.dumps(self.value.tolist()))
            elif self.value is not None:
                # if string do not dump it will add "" to a string
                if isinstance(self.value, (str)):
                    self.setText(1, self.value)
                else:
                    # dump other values
                    self.setText(1, json.dumps(self.value))
            else:
                self.setText(1, "")

        if not self.saved_sts:
            self.setText(2, "")  # not loaded list of saved PVs means no value
            self.setText(3, "Set of saved PVs not selected!")
            self.has_error = True
        else:
            if isinstance(self.saved_value, (numpy.ndarray)):
                self.setText(2, json.dumps(self.saved_value.tolist()))
            elif self.saved_value is not None:
                # if string do not dump it will add "" to a string
                if isinstance(self.saved_value, (str)):
                    self.setText(2, self.saved_value)
                else:
                    # dump other values
                    self.setText(2, json.dumps(self.saved_value))
            else:
                self.setText(2, "Not Saved")
                self.setText(3, "PV value not saved in this set.")
                self.has_error = True

            if self.has_error or (self.compare is None):
                self.set_color(PvViewStatus.err)
            else:
                if self.compare:
                    self.setText(3, "Equal")
                    self.set_color(PvViewStatus.eq)
                else:
                    self.setText(3, "Not equal")
                    self.set_color(PvViewStatus.neq)

        # Filter with saved filter data, to check conditions with new values.
        self.apply_filter(self.compare_filter, self.completeness_filter,
                          self.name_filter)

    def set_color(self, status):
        brush = QtGui.QBrush()

        if status == PvViewStatus.eq:
            brush.setColor(QtGui.QColor(0, 190, 0))
        elif status == PvViewStatus.neq:
            brush.setColor(QtGui.QColor(204, 0, 0))

        # TODO porting to python 2 xrange
        for i in range(0, self.columnCount()):
            # ideally would set a background color, but it look like a bug (no
            # background is applied with method setBackground()
            self.setForeground(i, brush)

    def apply_filter(self, compare_filter=PvCompareFilter.show_all,
                     completeness_filter=True, name_filter=None):
        ''' Controls visibility of item, depending on filter conditions. '''

        # Save filters to use the when processed by value change
        self.compare_filter = compare_filter
        self.completeness_filter = completeness_filter
        self.name_filter = name_filter

        # if name filter empty --> no filter applied (show all)
        if name_filter:
            name_match = name_filter in self.pv_name
        else:
            name_match = True

        compare_match = ((PvCompareFilter(compare_filter) == PvCompareFilter.show_eq) and
                         (self.compare)) or ((PvCompareFilter(compare_filter) == PvCompareFilter.show_neq) and
                                             (not self.compare)) or (PvCompareFilter(compare_filter) == PvCompareFilter.show_all)

        # Do show values which has incomplete data?
        completeness_match = completeness_filter or \
            (not completeness_filter and not self.has_error)

        self.setHidden(
            not(name_match and compare_match and completeness_match))


class SnapshotFileSelector(QtGui.QWidget):

    ''' Widget to select file with dialog box '''

    def __init__(self, parent=None, label_text="File", button_text="Browse",
                 init_path=None, **kw):
        QtGui.QWidget.__init__(self, parent, **kw)
        self.file_path = init_path

        # Create main layout
        layout = QtGui.QHBoxLayout(self)
        layout.setMargin(0)
        layout.setSpacing(0)
        self.setLayout(layout)

        # Create file dialog box. When file is selected set file path to be
        # shown in input field (can be then edited manually)
        self.req_file_dialog = QtGui.QFileDialog(self)
        self.req_file_dialog.fileSelected.connect(self.set_file_input_text)

        # This widget has 3 parts:
        #   label
        #   input field (when value of input is changed, it is stored locally)
        #   icon button to open file dialog
        label = QtGui.QLabel(label_text, self)
        file_path_button = QtGui.QToolButton(self)
        icon = QtGui.QIcon.fromTheme("folder")
        file_path_button.setIcon(icon)
        file_path_button.clicked.connect(self.req_file_dialog.show)
        file_path_button.setFixedSize(27, 27)
        self.file_path_input = QtGui.QLineEdit(self)
        self.file_path_input.textChanged.connect(self.change_file_path)

        layout.addWidget(label)
        layout.addWidget(self.file_path_input)
        layout.addWidget(file_path_button)

    def set_file_input_text(self):
        self.file_path_input.setText(self.req_file_dialog.selectedFiles()[0])

    def change_file_path(self):
        self.file_path = self.file_path_input.text()


class SnapshotConfigureDialog(QtGui.QDialog):
    # NOT USED YET#

    ''' Dialog window to select and apply file'''

    def __init__(self, parent=None, **kw):
        QtGui.QDialog.__init__(self, parent, **kw)
        self.file_path = None
        layout = QtGui.QVBoxLayout(self)
        layout.setMargin(10)
        layout.setSpacing(10)
        self.setLayout(layout)

        # This Dialog consists of file selector and buttons to apply
        # or cancel the file selection
        self.file_selector = SnapshotFileSelector(self)
        self.setMinimumSize(600, 50)

        layout.addWidget(self.file_selector)

        button_box = QtGui.QDialogButtonBox(
            QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel)
        layout.addWidget(button_box)

        button_box.accepted.connect(self.config_accepted)
        button_box.rejected.connect(self.config_rejected)

    def config_accepted(self):
        # Save to file path to local variable and emit signal
        self.file_path = self.file_selector.file_path
        self.accepted.emit()
        self.close()

    def config_rejected(self):
        self.rejected.emmit()
        self.close()


class SnapshotWorker(QtCore.QObject):

    '''
    This is a worker object running in separate thread. It holds core of the
    application, snapshot object, so it does all time consuming tasks such as
    saving and restoring, loading files etc.

    Its slots (methods with @pyqtSlot) can be invoked with
    QtCore.QMetaObject.invokeMethod().

    To notify GUI this worker emits following signals
    * new_snapshot(snapshot) --> new snapshot was instantiated
    * save_done(save_file_path, status) --> save was finished
    * restore_done(save_file_path, status) --> restore was finished
    '''

    def __init__(self, parent=None):

        QtCore.QObject.__init__(self, parent)
        # Instance of snapshot will be created with  init_snapshot() so just
        # create a placeholder
        self.snapshot = None

    @pyqtSlot(str, dict)
    def init_snapshot(self, req_file_path, req_macros=None):
        # creates new instance of snapshot loads the request file and emits
        # the signal new_snapshot to update the GUI
        self.snapshot = Snapshot(req_file_path, req_macros)
        pvs_names = self.snapshot.get_pvs_names()
        self.emit(SIGNAL("new_snapshot_loaded(PyQt_PyObject)"), pvs_names)

    @pyqtSlot(str, str, str)
    def save_pvs(self, save_file_path, keywords=None, comment=None):
        # Start saving process and notify when finished
        status = self.snapshot.save_pvs(save_file_path, keywords=keywords,
                                        comment=comment)
        self.emit(
            SIGNAL("save_done(PyQt_PyObject, PyQt_PyObject)"), save_file_path,
            status)

    @pyqtSlot()
    def restore_pvs(self):
        # Just calling snapshot restore.
        status = self.snapshot.restore_pvs()

        self.emit(SIGNAL("restore_done(PyQt_PyObject)"), status)

    @pyqtSlot(dict)
    def load_pvs_to_snapshot(self, saved_pvs):
        # Sets list of pvs to snapshot object

        self.snapshot.prepare_pvs_to_restore_from_list(saved_pvs)
        self.emit(SIGNAL("load_for_restore_done()"))

    @pyqtSlot(str, str, dict)
    def get_save_files(self, save_dir, name_prefix, current_files):
        parsed_save_files = dict()
        # Check if any file added or modified (time of modification)
        for file_name in os.listdir(save_dir):
            file_path = os.path.join(save_dir, file_name)
            if os.path.isfile(file_path) and file_name.startswith(name_prefix):
                if (file_name not in current_files) or \
                   (current_files[file_name]["modif_time"] != os.path.getmtime(file_path)):

                    pvs_list, meta_data = self.snapshot.parse_from_save_file(
                        file_path)

                    # save data (no need to open file again later))
                    parsed_save_files[file_name] = dict()
                    parsed_save_files[file_name]["pvs_list"] = pvs_list
                    parsed_save_files[file_name]["meta_data"] = meta_data
                    parsed_save_files[file_name][
                        "modif_time"] = os.path.getmtime(file_path)

        self.emit(
            SIGNAL("save_files_loaded(PyQt_PyObject)"), parsed_save_files)

    @pyqtSlot()
    def start_continous_compare(self):
        self.snapshot.start_continous_compare(self.process_callbacks)
        print("Compare started")

    @pyqtSlot()
    def stop_continous_compare(self):
        self.snapshot.stop_continous_compare()

    def process_callbacks(self, **kw):
        self.emit(SIGNAL("pv_changed(PyQt_PyObject)"), kw)


def main():
    """ Main logic """

    args_pars = argparse.ArgumentParser()
    args_pars.add_argument('req_file', help='Request file')
    args_pars.add_argument('-macros',
                           help="Macros for request file e.g.: \"SYS=TEST,DEV=D1\"")
    args_pars.add_argument('-dir',
                           help="Directory for saved files")
    args = args_pars.parse_args()

    # Parse macros string if exists
    macros = dict()
    if args.macros:
        macros_list = args.macros.split(',')
        for macro in macros_list:
            split_macro = macro.split('=')
            macros[split_macro[0]] = split_macro[1]

    # Create application which consists of two threads. "gui" runs in main
    # GUI thread. Time consuming functions are executed in worker thread.
    app = QtGui.QApplication(sys.argv)
    worker = SnapshotWorker()  # this is working object manipulating snapshot
    worker_thread = QtCore.QThread()
    worker.moveToThread(worker_thread)
    worker_thread.start()

    gui = SnapshotGui(worker, args.req_file, macros, args.dir)

    sys.exit(app.exec_())

# Start program here
if __name__ == '__main__':
    main()
