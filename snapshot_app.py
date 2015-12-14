import threading
import sys
from PyQt4 import QtGui, QtCore
from PyQt4.QtCore import pyqtSlot, Qt, SIGNAL
import time
import datetime
import threading
import argparse

from snapshot import *

# close with ctrl+C
import signal
signal.signal(signal.SIGINT, signal.SIG_DFL)


class SnapshotFileSelector(QtGui.QWidget):

    ''' Widget to select file with dialog box '''

    def __init__(self, parent=None, label_text="File", button_text="Browse",
                 init_path=None, **kw):
        QtGui.QWidget.__init__(self, parent, **kw)
        self.setMinimumSize(550, 50)
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


class SnapshotSaveWidget(QtGui.QWidget):

    def __init__(self, worker, common_settings, parent=None, **kw):
        QtGui.QWidget.__init__(self, parent, **kw)
        # Set file name and pass when save is pressed, pass file path to worker
        # to execute save
        # Add meta data TODO
        self.common_settings = common_settings
        self.name_base = os.path.split(common_settings["req_file_name"])[1].split(".")[0] + "_"
        # set default name extension
        self.name_extension = datetime.datetime.fromtimestamp(time.time()).strftime('%Y%m%d_%H%M')
        

        self.file_path = os.path.join(self.common_settings["save_dir"],
                                      self.name_base + self.name_extension)

        self.worker = worker
        self.setMaximumHeight(180)

        # Create main layout
        layout = QtGui.QVBoxLayout(self)
        layout.setMargin(10)
        layout.setSpacing(10)
        self.setLayout(layout)

        min_label_width = 120

        # Make a field to select file extension with readback
        extension_layout = QtGui.QHBoxLayout(self)
        extension_layout.setSpacing(10)
        extension_label = QtGui.QLabel("Name extension:", self)
        extension_label.setAlignment(Qt.AlignCenter | Qt.AlignRight)
        extension_label.setMinimumWidth(min_label_width)
        self.extension_input = QtGui.QLineEdit(self)
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
        comment_layout = QtGui.QHBoxLayout(self)
        comment_layout.setSpacing(10)
        comment_label = QtGui.QLabel("Comment:", self)
        comment_label.setAlignment(Qt.AlignCenter | Qt.AlignRight)
        comment_label.setMinimumWidth(min_label_width)
        self.comment_input = QtGui.QLineEdit(self)
        comment_layout.addWidget(comment_label)
        comment_layout.addWidget(self.comment_input)

        # Make field for keywords
        keyword_layout = QtGui.QHBoxLayout(self)
        keyword_layout.setSpacing(10)
        keyword_label = QtGui.QLabel("Keywords:", self)
        keyword_label.setAlignment(Qt.AlignCenter | Qt.AlignRight)
        keyword_label.setMinimumWidth(min_label_width)
        self.keyword_input = QtGui.QLineEdit(self)
        keyword_layout.addWidget(keyword_label)
        keyword_layout.addWidget(self.keyword_input)

        save_button = QtGui.QPushButton("Save", self)
        save_button.clicked.connect(self.start_save)

        layout.addItem(extension_layout)
        layout.addItem(comment_layout)
        layout.addItem(keyword_layout)
        layout.addWidget(save_button)

    def start_save(self):
        QtCore.QMetaObject.invokeMethod(self.worker, "save_pvs",
                                        Qt.QueuedConnection,
                                        QtCore.Q_ARG(str, self.file_path),
                                        QtCore.Q_ARG(str, self.keyword_input.text()),
                                        QtCore.Q_ARG(str, self.comment_input.text()))

    def update_name(self):
        self.file_path = os.path.join(self.common_settings["save_dir"],
                                      self.name_base + self.name_extension)
        self.file_name_rb.setText(self.name_base + self.name_extension)


class SnapshotRestoreWidget(QtGui.QWidget):

    def __init__(self, worker, common_settings, parent=None, **kw):
        QtGui.QWidget.__init__(self, parent, **kw)
        # Select file and start restoring when restore button is pressed,
        # pass file path to worker
        # Add meta data TODO
        self.worker = worker
        self.common_settings = common_settings
        self.file_list = dict()

        # Create main layout
        layout = QtGui.QVBoxLayout(self)
        layout.setMargin(10)
        layout.setSpacing(10)
        self.setLayout(layout)

        # Create list of files, keywords, comments
        self.file_selector = QtGui.QTreeWidget(self)
        self.file_selector.setColumnCount(3)
        self.file_selector.setHeaderLabels(["File", "Keywords", "Comment"])
        self.file_selector.header().resizeSection(0, 300)
        self.file_selector.header().resizeSection(1, 300)
        self.file_selector.itemSelectionChanged.connect(self.choose_file)

        #self.file_input = SnapshotFileSelector(self)
        restore_button = QtGui.QPushButton("Restore", self)
        restore_button.clicked.connect(self.start_restore)

        #layout.addWidget(self.file_input)
        layout.addWidget(self.file_selector)
        layout.addWidget(restore_button)

    def choose_file(self):
        pvs = self.file_list[self.file_selector.selectedItems()[0].text(0)]["pvs_list"]
        self.common_settings["pvs_to_restore"] = pvs

    def make_file_list(self, file_list):
        self.file_list = file_list
        for key in file_list:

            keywords = file_list[key]["meta_data"].get("keywords", "")
            comment = file_list[key]["meta_data"].get("comment", "")
            save_file = QtGui.QTreeWidgetItem([key, keywords, comment])

            self.file_selector.addTopLevelItem(save_file)
        
        # Sort by file name (alphabetical order)
        self.file_selector.sortItems(0, Qt.AscendingOrder)

    def start_restore(self):
        # Use one of the preloaded caved files
        QtCore.QMetaObject.invokeMethod(self.worker,
                                        "restore_pvs_from_obj",
                                        Qt.QueuedConnection,
                                        QtCore.Q_ARG(dict,
                                                     self.common_settings["pvs_to_restore"]))

class TestWidget(QtGui.QWidget):

    def __init__(self, parent=None):
        QtGui.QWidget.__init__(self, parent)
        self.thread = Worker()
        self.label = QtGui.QLabel("bla", self)
        self.connect(self.thread, SIGNAL("setPVs(PyQt_PyObject)"), self.setPVs)
        self.thread.start()

    def setPVs(self, pvs):
        self.pvs = pvs
        for key in self.pvs:
            print(key)


class SnapshotGui(QtGui.QWidget):

    '''
    Main GUI class for Snapshot application. It needs separate working
    thread where core application is running
    '''

    def __init__(self, worker, req_file_name, req_file_macros=None,
                 save_dir=None, save_file_dft=None, mode=None, parent=None):
        QtGui.QWidget.__init__(self, parent)
        self.worker = worker
        self.parsed_save_files = dict()
        self.setMinimumSize(900, 500)

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

        # Listen signals and call apropritae functions. All signals from worker
        # should be cached here
        self.connect(self.worker, SIGNAL("save_files_loaded(PyQt_PyObject)"),
                     self.make_file_list)
        self.connect(self.worker, SIGNAL("save_done(PyQt_PyObject,PyQt_PyObject)"),
                     self.save_done)

        # Snapshot gui consists of two tabs: "Save" and "Restore" default
        # is selected depending on mode parameter TODO
        main_layout = QtGui.QVBoxLayout(self)
        self.setLayout(main_layout)

        # Tab widget. Each tab has it's own widget. Need one for save 
        # and one for Restore
        tabs = QtGui.QTabWidget(self)
        tabs.setMinimumSize(900, 450)

        self.save_widget = SnapshotSaveWidget(self.worker, self.common_settings,
                                         tabs)
        self.restore_widget = SnapshotRestoreWidget(self.worker,
                                               self.common_settings, tabs)

        tabs.addTab(self.save_widget, "Save")
        tabs.addTab(self.restore_widget, "Restore")

        self.start_gui()
        self.show() # TODO check why it waits for end of get_save_files method
        self.setWindowTitle('Snapshot')
        self.get_save_files()

    def start_gui(self):
        if not self.common_settings["req_file_name"]:
            # For now obligatory to pas req_file_name
            # TODO request dialog to select request file
            pass
        else:
            # initialize snapshot and show the gui in proper mode 
            # TODO (select tab)
            self.worker.init_snapshot(self.common_settings["req_file_name"],
                                      self.common_settings["req_file_macros"])
               
    def get_save_files(self):
        prefix = os.path.split(self.common_settings["req_file_name"])[1].split(".")[0] + "_"
        QtCore.QMetaObject.invokeMethod(self.worker, "get_save_files",
                                        Qt.QueuedConnection,
                                        QtCore.Q_ARG(str, self.common_settings["save_dir"]),
                                        QtCore.Q_ARG(str, prefix))

    def make_file_list(self, file_list):
        # Just pass data to Restore widget
        self.restore_widget.make_file_list(file_list)

    def save_done(self, file_path, status):
        # TODO 
        print("Save done")


class SnapshotWorker(QtCore.QObject):
    # This worker object running in separate thread

    def __init__(self, parent=None):

        QtCore.QObject.__init__(self, parent)
        # Instance of snapshot will be created with  init_snapshot(), which
        self.snapshot = None

    def init_snapshot(self, req_file_path, req_macros=None):
        # creates new instance of snapshot and loads the request file and
        # emit signal new_snapshot to update GUI

        self.snapshot = Snapshot(req_file_path, req_macros)
        self.emit(SIGNAL("new_snapshot(PyQt_PyObject)"), self.snapshot.pvs)

    @pyqtSlot(str, str, str)
    def save_pvs(self, save_file_path, keywords=None, comment=None):
        status = self.snapshot.save_pvs(save_file_path, keywords=keywords,
                                        comment=comment)
        self.emit(SIGNAL("save_done(PyQt_PyObject, PyQt_PyObject)"), save_file_path, status)

    @pyqtSlot(str, str)
    def get_save_files(self, save_dir, name_prefix):
        parsed_save_files = dict()

        for file_name in os.listdir(save_dir):
            file_path = os.path.join(save_dir, file_name)
            if os.path.isfile(file_path) and file_name.startswith(name_prefix):
                pvs_list, meta_data = self.snapshot.parse_from_save_file(file_path)

                # save data (no need to open file again later))
                parsed_save_files[file_name] = dict()
                parsed_save_files[file_name]["pvs_list"] = pvs_list
                parsed_save_files[file_name]["meta_data"] = meta_data
        
        self.emit(SIGNAL("save_files_loaded(PyQt_PyObject)"), parsed_save_files)

    @pyqtSlot(dict)
    def restore_pvs_from_obj(self, saved_pvs):
        # All files are already parsed. Just need to load selected one
        # and do parse
        self.snapshot.load_saved_pvs_from_obj(saved_pvs)
        self.snapshot.restore_pvs()
        # TODO return status

    def start_continous_compare(self):
        self.snapshot.start_continous_compare(self.process_callbacks)

    def stop_continous_compare(self):
        self.snapshot.stop_continous_compare()

    def process_callbacks(self, **kw):
        pass
        # TODO here raise signals data is packed in kw

    def check_status(self, status):
        report = ""
        for key in status:
            if not status[key]:
                pass  # TODO status checking


def main():
    """ Main logic """

    args_pars = argparse.ArgumentParser()
    args_pars.add_argument('req_file', help='Request file')
    args_pars.add_argument('-macros',
                          help="Macros for request file e.g.: \"SYS=TEST,DEV=D1\"")
    args_pars.add_argument('-dir',
                          help="Directory for saved files")
    args = args_pars.parse_args()

    #Parse macros string if exists
    macros = dict()
    if args.macros:
        macros_list = args.macros.split(',')
        for macro in macros_list:
            split_macro = macro.split('=')
            macros[split_macro[0]] = split_macro[1]

    # Create application which consists of two threads. "gui" runs in main
    # GUI thread. Time consuming functions are executed in worker thread.
    app = QtGui.QApplication(sys.argv)
    worker = SnapshotWorker(app)
    worker_thread = threading.Thread(target=worker)

    gui = SnapshotGui(worker, args.req_file, macros, args.dir)  

    sys.exit(app.exec_())

# Start program here
if __name__ == '__main__':
    main()

