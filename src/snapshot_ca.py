#!/usr/bin/env python
#
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

from epics import *
ca.AUTO_CLEANUP = True  # For pyepics versions older than 3.2.4, this was set to True only for
                      # python 2 but not for python 3, which resulted in errors when closing
                      # the application. If true, ca.finalize_libca() is called when app is
                      # closed
import numpy
import json
import time
import os
from enum import Enum


class PvStatus(Enum):
    access_err = 0
    ok = 1
    no_value = 2
    equal = 3


class ActionStatus(Enum):
    busy = 0
    ok = 1
    no_data = 2
    no_cnct = 3
    timeout = 4


def macros_substitution(string, macros):
    for key in macros:
        macro = "$(" + key + ")"
        string = string.replace(macro, macros[key])
    return string


# Subclass PV to be to later add info if needed
class SnapshotPv(PV):
    """
    Extended PV class with non-blocking methods to save and restore pvs.
    """

    def __init__(self, pvname, macros=None, connection_callback=None, **kw):
        # Store the origin
        self.pvname_raw = pvname
        self.macros = macros

        if macros:
            pvname = macros_substitution(pvname, macros)

        self.cnct_callback = connection_callback
        self.saved_value = None
        self.value_to_restore = None  # This holds value from last loaded save file
        self.compare_callback_id = None
        self.last_compare = None
        self.is_array = False

        PV.__init__(self, pvname,
                    connection_callback=self.internal_cnct_callback,
                    auto_monitor=True, connection_timeout=None, **kw)
        self.cnct_lost = not self.connected

    def save_pv(self):
        """
        None blocking save. Takes latest value (monitored). If no connection
        or access simply skips the saving.
        """
        pv_status = PvStatus.ok  # Will be changed if error occurs.
        if self.connected:
            # Must be after connection test. If checking access when not
            # connected pyepics tries to reconnect which takes some time.
            if self.read_access:
                self.saved_value = self.get(use_monitor=True)
                if self.is_array:
                    if numpy.size(self.saved_value) == 0:
                        # Empty array is equal to "None" scalar value
                        self.saved_value = None
                    elif numpy.size(self.saved_value) == 1:
                        # make scalars as arrays
                        self.saved_value = numpy.asarray([self.saved_value])
                if self.value is None:
                    pv_status = PvStatus.no_value
                else:
                    pv_status = PvStatus.ok
            else:
                pv_status = PvStatus.access_err
        else:
            self.saved_value = None
            pv_status = PvStatus.access_err
        return(pv_status)

    def restore_pv(self, callback=None):
        """
        Executes pv.put of value_to_restore. Success of put is returned
        in callback.
        """
        if self.connected:
            # Must be after connection test. If checking access when not
            # connected pyepics tries to reconnect which takes some time.
            if self.write_access:
                if self.value_to_restore is None:
                    self.verify_restore_response(PvStatus.no_value, callback)
                else:
                    equal = self.compare(self.value)

                    if not equal:
                        if isinstance(self.value_to_restore, str):
                            # pyepics needs value as bytes not as string
                            put_value = str.encode(self.value_to_restore)
                        else:
                            put_value = self.value_to_restore

                        self.put(put_value, wait=False,
                                 callback=self.verify_restore_response,
                                 callback_data={"status": PvStatus.ok,
                                                "callback": callback})
                    else:
                        # No need to be restored.
                        self.verify_restore_response(PvStatus.equal, callback)
            else:
                self.verify_restore_response(PvStatus.access_err, callback)
        else:
            self.verify_restore_response(PvStatus.access_err, callback)

    def verify_restore_response(self, status, callback=None, **kw):
        """
        This method is called for each restore with appropriate status. It
        calls user specified callback.
        """
        if callback:
            callback(pv_name=self.pvname, status=status)

    def set_restore_parameters(self, pv_params):
        """
        Accepts parameters that specify restore. Currently just value in future
        possibility to add dead-band for analogue values, etc
        """
        if pv_params is not None:
            self.value_to_restore = pv_params['pv_value']
        else:
            # Clear params for PVs that is not defined to avoid restoring
            # values from old configurations
            self.value_to_restore = None

    def clear_restore_parameters(self):
        """ Sets all restore parameters to None values. """
        self.value_to_restore = None

    def compare(self, value):
        if self.is_array:
            compare = numpy.array_equal(value, self.value_to_restore)
        else:
            compare = (value == self.value_to_restore)

        return compare

    def internal_cnct_callback(self, conn, **kw):
        """
        Snapshot specific handling of connection status on connection callback.
        """

        # PV layer of pyepics handles arrays strange. In case of having a
        # waveform with NORD field "1" it will not interpret it as array.
        # Instead of native "pv.count" (NORD) it should use "pv.nelm",
        # but this also acts wrong. It simply does: if count == 1, then
        # nelm = 1.) The true NELM info can be found with
        # ca.element_count(self.chid).
        self.is_array = (ca.element_count(self.chid) > 1)

        # Because snapshot must be updated also when connection is lost,
        # and one callback per pv is used in snapshot, lost of connection
        # must execute callbacks.
        # These callbacks need info about connection status but self.connected
        # is updated after connection callbacks are called. To have this info
        # before store it in self.cnct_lost
        self.cnct_lost = not conn

        # If user specifies his own connection callback, call it here.
        if self.cnct_callback:
            self.cnct_callback(conn=conn, **kw)

        # If connection is lost call all "normal" callbacks, to update
        # the status.
        if not conn:
            self.run_callbacks()


class Snapshot:
    def __init__(self, req_file_path, macros=None, **kw):
        # Hold a dictionary for each PV (key=pv_name) with reference to
        # SnapshotPv object.
        self.pvs = dict()

        # Other important states
        self.compare_state = False
        self.restore_values_loaded = False
        self.restore_started = False
        self.restore_blocking_done = False
        self.all_connected = False
        self.compare_callback = None
        self.restore_callback = None
        self.current_restore_forced = False
        self.macros = macros

        # holds path to the req_file_path as this is sort of identifier
        self.req_file_path = req_file_path

        # Uses default parsing method. If other format is needed, subclass
        # and re implement parse_req_file method. It must return list of
        # PV names.
        self.add_pvs(self.parse_req_file(req_file_path))

    def add_pvs(self, pv_list_raw):
        # pyepics will handle PVs to have only one connection per PV.
        # If pv not yet on list add it

        for pv_name_raw in pv_list_raw:
            pv_ref = SnapshotPv(pv_name_raw, self.macros,
                            connection_callback=self.update_all_connected_status)

            if not self.pvs.get(pv_ref.pvname):
                self.pvs[pv_ref.pvname] = pv_ref

    def remove_pvs(self, pv_list):
        # disconnect pvs to avoid unneeded connections
        # and remove from list of pvs
        for pv_name in pv_list:
            if self.pvs.get(pv_name, None):
                pv_ref = self.pvs.pop(pv_name)
                pv_ref.disconnect()

    def change_macros(self, macros=None, **kw):
        macros = macros or {}
        if self.macros != macros:
            self.macros = macros
            pvs_to_change = dict()
            pvs_to_remove = list()
            for pv_name, pv_ref in self.pvs.items():
                if "$" in pv_ref.pvname_raw:
                    # store pvs value to restore (and indirectly pv raw name)
                    pvs_to_change[pv_ref.pvname_raw] = dict()
                    pvs_to_change[pv_ref.pvname_raw]["pv_value"] = pv_ref.value_to_restore
                    pvs_to_remove.append(pv_name)

            self.remove_pvs(pvs_to_remove)
            self.add_pvs(pvs_to_change.keys())

            if self.restore_values_loaded:
                self.prepare_pvs_to_restore_from_list(pvs_to_change)

            self.update_all_connected_status()


    def save_pvs(self, req_file_name, save_file_path, force=False, symlink_path=None, **kw):
        # get value of all PVs and save them to file
        # All other parameters (packed in kw) are appended to file as meta data
        pvs_status = dict()
        if not force and not self.all_connected:
            return(ActionStatus.no_cnct, pvs_status)

        # Update metadata
        kw["save_time"] = time.time()
        if req_file_name:
            kw["req_file_name"] = req_file_name

        for key in self.pvs:
            pvs_status[key] = self.pvs[key].save_pv()
        self.parse_to_save_file(save_file_path, self.macros, symlink_path, **kw)
        return(ActionStatus.ok, pvs_status)

    def prepare_pvs_to_restore_from_file(self, save_file_path):
        # Parsers the file and loads value to corresponding objects
        # Can be later used for compare and restore

        saved_pvs, meta_data, err = self.parse_from_save_file(save_file_path)
        self.prepare_pvs_to_restore_from_list(saved_pvs, meta_data.get('macros', dict()))

    def prepare_pvs_to_restore_from_list(self, saved_pvs_raw, custom_macros = dict()):
        saved_pvs = dict()
        if self.macros:
            macros = self.macros
        else:
            macros = custom_macros

        if macros:
            # Make macro substitution on saved_pvs
            for pv_name_raw, pv_data in saved_pvs_raw.items():
                saved_pvs[macros_substitution(pv_name_raw, macros)] = pv_data
        else:
            saved_pvs = saved_pvs_raw

        # Disable compare for the time of loading new restore value
        if self.compare_state:
            callback = self.compare_callback
            self.stop_continuous_compare()
            self.compare_state = True  # keep old info to restart at the end

        # Loads pvs that were previously parsed from saved file
        for pv_name, pv_ref in self.pvs.items():
            pv_ref.set_restore_parameters(saved_pvs.get(pv_name, None))

        self.restore_values_loaded = True

        # run compare again and do initial compare
        if self.compare_state:
            self.start_continuous_compare(callback)

    def clear_pvs_to_restore(self):
        # Disable compare for the time of loading new restore value
        if self.compare_state:
            callback = self.compare_callback
            self.stop_continuous_compare()
            self.compare_state = True  # keep old info to restart at the end

        # Loads pvs that were previously parsed from saved file
        for pv_name, pv_ref in self.pvs.items():
            pv_ref.clear_restore_parameters()

        self.restore_values_loaded = False

        # run compare again and do initial compare
        if self.compare_state:
            self.start_continuous_compare(callback)

    def restore_pvs(self, save_file_path=None, force=False, callback=None, selected=None):
        """

        :param save_file_path: Path to snap file. If None, then preloaded values are used-
        :param force: Force restore if not all needed PVs are connected
        :param callback: Callback fnc
        :param selected: List of selected PVs to be restored.
        :return:
        """
        if selected is None:
            selected = list()

        # If file with saved values specified then read file. If no file
        # then just use last stored values
        if self.restore_started:
            # Cannot do a restore, previous not finished
            return(ActionStatus.busy)

        self.restore_started = True
        self.current_restore_forced = force
        if save_file_path:
            self.prepare_pvs_to_restore_from_file(save_file_path)

        if not self.restore_values_loaded:
            # Nothing to restore
            self.restore_started = False
            return(ActionStatus.no_data)


        # Standard restore (restore all)
        # If force=True, then do restore even if not all PVs are connected.
        # If only few PVs are selected, check if needed PVs are connected
        # Default is to abort restore if one is missing

        if not force and (not self.check_pvs_connected_status(selected) and selected or
                                  not self.all_connected and not selected):
            self.restore_started = False
            return(ActionStatus.no_cnct)


        # Do a restore
        self.restored_pvs_list = list()
        self.restore_callback = callback
        for pv_name, pv_ref in self.pvs.items():
            if not selected or pv_name in selected:
                pv_ref.restore_pv(callback=self.check_restore_complete)
            else:
                # pv is not in subset in the "selected only" mode
                # checking algorithm should think this one was successfully restored
                self.check_restore_complete(pv_name, PvStatus.ok)
        return(ActionStatus.ok)


    def check_restore_complete(self, pv_name, status, **kw):
        self.restored_pvs_list.append((pv_name, status))
        if len(self.restored_pvs_list) == len(self.pvs) and self.restore_callback:
            self.restore_started = False
            self.restore_callback(status=dict(self.restored_pvs_list), forced=self.current_restore_forced)
            self.restore_callback = None

    def restore_pvs_blocking(self, save_file_path=None, force=False, timeout=10):
        self.restore_blocking_done = False
        status =  self.restore_pvs(save_file_path, force, self.set_restore_blocking_done)
        if status == ActionStatus.ok:
            end_time = time.time() + timeout
            while not self.restore_blocking_done and time.time() < end_time:
                time.sleep(0.2)

            if self.restore_blocking_done:
                return ActionStatus.ok
            else:
                return ActionStatus.timeout
        else:
            return status

    def set_restore_blocking_done(self, status, forced):
        # If this was called, then restore is done
        self.restore_blocking_done = True

    def start_continuous_compare(self, callback=None, save_file_path=None):
        self.compare_callback = callback

        # If file with saved values specified then read file. If no file
        # then just use last stored values
        if save_file_path:
            self.prepare_pvs_to_restore_from_file(save_file_path)

        for pv_name, pv_ref in self.pvs.items():
            pv_ref.compare_callback_id = pv_ref.add_callback(self.continuous_compare)
            # if pv_ref.connected:
            #     # Send first callbacks for "initial" compare of each PV if
            #      already connected.
            if pv_ref.connected:
                self.continuous_compare(pvname=pv_ref.pvname,
                                        value=pv_ref.value)
            elif self.compare_callback:
                self.compare_callback(pv_name=pv_name, pv_value=None,
                                      pv_saved=pv_ref.value_to_restore,
                                      pv_compare=None,
                                      pv_cnct_sts=not pv_ref.cnct_lost,
                                      saved_sts=self.restore_values_loaded)
        self.compare_state = True

    def stop_continuous_compare(self):
        self.compare_callback = None
        for key in self.pvs:
            pv_ref = self.pvs[key]
            if pv_ref.compare_callback_id:
                pv_ref.remove_callback(pv_ref.compare_callback_id)

        self.compare_state = False

    def continuous_compare(self, pvname=None, value=None, **kw):
        # This is callback function
        # Uses "cnct_lost" instead of "connected", because it is updated
        # earlier (to get proper value in case of connection lost)
        pv_ref = self.pvs.get(pvname, None)

        # In case of empty array pyepics does not return
        # numpy.ndarray but instance of
        # <class 'epics.dbr.c_int_Array_0'>
        # In case of array with 1 value, a native type value is returned.
        
        if pv_ref.is_array:
            if numpy.size(value) == 0:
                value = None

            elif numpy.size(value) == 1:
                value = numpy.asarray([value])

        if pv_ref:
            if not self.restore_values_loaded:
                # no old data was loaded clear compare
                pv_ref.last_compare = None
            elif pv_ref.cnct_lost:
                pv_ref.last_compare = None
                value = None
            else:
                # compare  value (different for arrays)
                pv_ref.last_compare = pv_ref.compare(value)

            if self.compare_callback:
                self.compare_callback(pv_name=pvname, pv_value=value,
                                      pv_saved=pv_ref.value_to_restore,
                                      pv_compare=pv_ref.last_compare,
                                      pv_cnct_sts=not pv_ref.cnct_lost,
                                      saved_sts=self.restore_values_loaded)

    def update_all_connected_status(self, pvname=None, **kw):
        check_all = False
        pv_ref = self.pvs.get(pvname, None)

        if pv_ref is not None:
            if self.pvs[pvname].cnct_lost:
                self.all_connected = False
            elif not self.all_connected:
                # One of the PVs was reconnected, check if all are connected now.
                check_all = True
        else:
            check_all = True

        if check_all:
            self.all_connected = self.check_pvs_connected_status()

    def check_pvs_connected_status(self, pvs=None):
        # If not specific list of pvs is given, then check all
        if pvs is None:
            pvs = self.pvs.keys()

        for pv in pvs:
            pv_ref = self.pvs.get(pv)
            if pv_ref.cnct_lost:
                return(False)

        # If here then all connected
        return(True)

    def get_pvs_names(self):
        # To access a list of all pvs that are under control of snapshot object
        return list(self.pvs.keys())

    def get_not_connected_pvs_names(self, selected=None):
        if selected is None:
            selected = list()
        if self.all_connected:
            return list()
        else:
            not_connected_list = list()
            for pv_name, pv_ref in self.pvs.items():
                if not pv_ref.connected and ((pv_name in selected) or not selected):
                    not_connected_list.append(pv_name)            # Need to check only subset (selected) of pvs?
            return(not_connected_list)

    def replace_metadata(self, save_file_path, metadata):
        # Will replace metadata in the save file with the provided one
        
        with open(save_file_path, 'r') as save_file:
            lines = save_file.readlines()
            if lines[0].startswith('#'):
                lines[0] = "#" + json.dumps(metadata) + "\n"
            else:
                lines.insert(0, "#" + json.dumps(metadata) + "\n")

            with open(save_file_path, 'w') as save_file_write:
                save_file_write.writelines(lines)

    # Parser functions

    def parse_req_file(self, req_file_path):
        # This function is called at each initialization.
        # This is a parser for a simple request file which supports macro
        # substitution. Macros are defined as dictionary.
        # {'SYS': 'MY-SYS'} will change all $(SYS) macros with MY-SYS
        req_pvs = list()
        req_file = open(req_file_path)
        for line in req_file:
            # skip comments and empty lines
            if not line.startswith(('#', "data{", "}")) and line.strip():
                pv_name = line.rstrip().split(',')[0]
                req_pvs.append(pv_name)

        req_file.close()
        return req_pvs

    def parse_to_save_file(self, save_file_path, macros=None, symlink_path=None,  **kw):
        # This function is called at each save of PV values.
        # This is a parser which generates save file from pvs
        # All parameters in **kw are packed as meta data
        # To support other format of file, override this method in subclass
        save_file_path = os.path.abspath(save_file_path)
        save_file = open(save_file_path, 'w')

        # Save meta data
        if macros:
            kw['macros'] = macros
        save_file.write("#" + json.dumps(kw) + "\n")

        # PVs
        for pv_name, pv_ref in self.pvs.items():
            if pv_ref.saved_value is not None:
                if pv_ref.is_array:
                    save_file.write(pv_ref.pvname_raw + "," + json.dumps(pv_ref.saved_value.tolist()) + "\n")
                else:
                    save_file.write(pv_ref.pvname_raw + "," + json.dumps(pv_ref.saved_value) + "\n")
            else:
                save_file.write(pv_ref.pvname_raw + "\n")
        save_file.close()

        # Create symlink _latest.snap
        if symlink_path:
            try:
                os.remove(symlink_path)
            except:
                pass
            os.symlink(save_file_path, symlink_path)

    def parse_from_save_file(self, save_file_path):
        # This function is called in compare function.
        # This is a parser which has a desired value for each PV.
        # To support other format of file, override this method in subclass
        # Note: This function does not detect if we have a valid save file,
        # or just something that was successfuly parsed

        saved_pvs = dict()
        meta_data = dict()  # If macros were used they will be saved in meta_data
        err = list()
        saved_file = open(save_file_path)
        meta_loaded = False

        for line in saved_file:
            # first line with # is metadata (as json dump of dict)
            if line.startswith('#') and not meta_loaded:
                line = line[1:]
                try:
                    meta_data = json.loads(line)
                except json.decoder.JSONDecodeError:
                    # Problem reading metadata
                    err.append('Meta data could not be decoded. Must be in JSON format.')
                meta_loaded = True
            # skip empty lines and all rest with #
            elif line.strip() and not line.startswith('#'):
                split_line = line.strip().split(',', 1)
                pv_name = split_line[0]
                if len(split_line) > 1:
                    pv_value_str = split_line[1]
                    # In case of array it will return a list, otherwise value
                    # of proper type
                    try:
                        pv_value = json.loads(pv_value_str)
                    except json.decoder.JSONDecodeError:
                        pv_value = None
                        err.append('Value of \'{}\' cannot be decoded. Will be ignored.'.format(pv_name))

                    if isinstance(pv_value, list):
                        # arrays as numpy array, because pyepics returns
                        # as numpy array
                        pv_value = numpy.asarray(pv_value)
                else:
                    pv_value = None

                saved_pvs[pv_name] = dict()
                saved_pvs[pv_name]['pv_value'] = pv_value

        if not meta_loaded:
            err.insert(0, 'No meta data in the file.')

        saved_file.close()
        return(saved_pvs, meta_data, err)

# Helper functions functions to support macros parsing for users of this lib
def parse_macros(macros_str):
    """ Converting comma separated macros string to dictionary. """

    macros = dict()
    if macros_str:
        macros_list = macros_str.split(',')
        for macro in macros_list:
            split_macro = macro.split('=')
            if len(split_macro) == 2:
                macros[split_macro[0]] = split_macro[1]
    return(macros)

def parse_dict_macros_to_text(macros):
    """ Converting dict() separated macros string to comma separated. """
    macros_str = ""
    for macro, subs in macros.items():
        macros_str += macro + "=" + subs + ","

    if macros_str:
        # Clear last comma
        macros_str = macros_str[0:-1]

    return(macros_str)
