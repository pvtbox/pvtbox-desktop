###############################################################################
#   
#   Pvtbox. Fast and secure file transfer & sync directly across your devices. 
#   Copyright © 2020  Pb Private Cloud Solutions Ltd. 
#   
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 3 of the License, or
#   (at your option) any later version.
#   
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#   
#   You should have received a copy of the GNU General Public License
#   along with this program.  If not, see <https://www.gnu.org/licenses/>.
#   
###############################################################################

import json
import logging
import traceback
from hashlib import sha512
from threading import current_thread
from uuid import uuid4
import os.path as op

from PySide2.QtCore import QObject, QMutex
from PySide2.QtCore import Signal as pyqtSignal
from requests import Session, exceptions
from requests_toolbelt.multipart import encoder

from common.ssl_pinning_adapter import SslPinningAdapter
from common.utils import ensure_unicode, license_type_constant_from_string
from common.utils import get_device_name, get_platform, \
    get_os_name_and_is_server
from common.constants import API_URI, API_EVENTS_URI, API_SHARING_URI, \
    API_UPLOAD_URI, REGULAR_URI

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class Client_APIError(Exception):
    def __init__(self, message, server_response=None):
        super(Client_APIError, self).__init__(message)
        self.response = server_response
        self.message = message

    def __str__(self):
        if self.response:
            return "{} (server response: {})".format(
                self.message, self.response)

        super(Client_APIError, self).__str__()


class Client_API(QObject):
    loggedIn = pyqtSignal(dict)
    login_failed = pyqtSignal(str, str)
    registered = pyqtSignal()
    registration_failed = pyqtSignal(str, str)
    timeout_error = pyqtSignal()

    request_timeout = 10
    read_timeout = 60

    def __init__(
            self, cfg, tracker=None,
            server_addr=None,
            events_server_addr=None,
            sharing_server_addr=None,
            upload_server_addr=None,
            parent=None):

        logger.debug("Initializing API server client...")
        QObject.__init__(self, parent=parent)

        self._sessions = dict()

        self.server_addr = server_addr if server_addr \
            else API_URI.format(cfg.host)
        self.events_server_addr = events_server_addr if events_server_addr \
            else API_EVENTS_URI.format(cfg.host)
        self.sharing_server_addr = sharing_server_addr if sharing_server_addr \
            else API_SHARING_URI.format(cfg.host)
        self.upload_server_addr = upload_server_addr if upload_server_addr \
            else API_UPLOAD_URI.format(cfg.host)
        self._tracker = tracker
        self.ip_addr = None
        self.cfg = cfg
        self.node_sign = None
        self._ip_lock = QMutex(parent=self)
        self._os_name, self._is_server = get_os_name_and_is_server()

    def emit_loggedIn(self,
                      user_email,
                      password_hash,
                      user_id,
                      node_id,
                      servers,
                      license_type,
                      remote_actions,
                      last_event_uuid):
        '''
        Prepares data and emits loggedIn signal

        @param server Info on servers as returned by API server [dict]
        '''

        data = {
            'user_email': user_email,
            'password_hash': password_hash,
            'user_hash': self.cfg.user_hash,
            'node_hash': self.cfg.node_hash,
            'user_id': user_id,
            'node_id': node_id,
            'servers': servers,
            'license_type': license_type,
            'remote_actions': remote_actions,
            'last_event_uuid': last_event_uuid,
        }

        # Emit signal
        self.loggedIn.emit(data)

    def signup(self, fullname, email, password):
        """
        Registers new user on the API server

        @param fullname Users full name [unicode]
        @param email Users email address [str]
        @param password Users password [str]
        """
        signed, res = self._signup(fullname, email, password)

        if not signed:
            if res and 'errcode' in res and \
                    res['errcode'] == 'ERROR_NODEHASH_EXIST':
                self.cfg.set_settings({
                    'node_hash': sha512(str(uuid4())).hexdigest()})
                return self.signup(fullname, email, password)

        if signed:
            self.registered.emit()

            if self._tracker:
                self._tracker.session_signup()
        else:
            error = 'Network Error' if res is None else res['errcode']
            self.registration_failed.emit(
                error,
                res.get('info', 'Unknown error') if res else 'Unknown error')

            if self._tracker:
                self._tracker.session_signup_failed(error)

        return signed, res

    def _signup(self, fullname, email, password):
        if self._tracker:
            self._tracker.session_signup_start()

        data = {
            'node_devicetype': 'desktop',
            'node_ostype': get_platform(),
            'node_osname': self._os_name,
            'node_name': get_device_name(),
            'fullname': fullname,
            'user_email': email,
            'user_password': password,
        }
        signed = False

        _res = self.create_request(action='signup', data=data)

        if _res and "success" in _res['result'] and 'user_hash' in _res:
            self.cfg.set_settings({'user_hash': _res['user_hash']})
            signed = True
            logger.info("Registered successfully")

        return signed, _res

    def login(self, login=None, password=None, user_hash=None):
        """
        Authorizes user on the API server

        @param login Users email address [str]
        @param password Users password [str]
        """
        logged_in, res = self._login(login, password, user_hash)

        if logged_in:
            license_type = res.get('license_type', None)
            license_type = license_type_constant_from_string(license_type)
            remote_actions = res.get('remote_actions', None)
            servers = list(map(self._strip_server_info,
                               res.get('servers', [])))
            try:
                logger.verbose("Servers: '%s'", servers)
            except AttributeError:
                pass
            res['servers'] = servers
            # Signalize on successful logging in
            self.emit_loggedIn(
                user_email=login if login else self.cfg.user_email,
                password_hash=password if password
                else self.cfg.user_password_hash,
                user_id=res.get('user_id', None),
                node_id=res.get('node_id', None),
                servers=servers,
                license_type=license_type,
                remote_actions=remote_actions,
                last_event_uuid=res.get('last_event_uuid', None))

            if self._tracker:
                self._tracker.session_login(license_type)
        else:
            error = 'Network Error' if res is None else res['errcode']
            self.login_failed.emit(
                error,
                res.get('info', 'Unknown error') if res else 'Unknown error')

            if self._tracker:
                self._tracker.session_login_failed(error)

        return logged_in, res

    def _login(self, login, password, user_hash):
        data = {
            'node_devicetype': 'desktop',
            'node_ostype': get_platform(),
            'node_osname': self._os_name,
            'node_name': get_device_name(),
            'user_email': login,
            'user_password': password,
            'user_hash': user_hash,
            'is_server': self._is_server,
        }

        # Signalize login attempt started (for statistics)
        if self._tracker:
            self._tracker.session_login_start()

        logged_in = False
        _res = self.create_request(action='login', data=data)
        if _res is None:
            if self._tracker:
                self._tracker.session_login_failed("response is None")
            return logged_in, _res
        if 'user_hash' not in _res:
            if self._tracker:
                self._tracker.session_login_failed("missing user_hash")
            logger.error("Server not returned user_hash")
            return logged_in, _res
        # Registered successfully
        if "success" in _res['result']:
            logged_in = True
            self.cfg.set_settings({'user_hash': _res['user_hash']})
            logger.info("Logged in successfully")

        return logged_in, _res

    def _strip_server_info(self, server_info):
        return dict(map(self._strip_info, server_info.items()))

    def _strip_info(self, item):
        key, value = item
        if isinstance(value, str):
            value = value.strip()
        return key, value

    def logout(self):
        self.create_request(action='logout')

    def generate_node_sign(self):
        """
        Returns node_sign parameter to be passed to API server

        @return Node sign value [str]
        """

        s = str(self.cfg.node_hash + str(self.ip_addr))
        s = s.encode()
        s = sha512(s)
        return s.hexdigest()

    def update_node_sign(self):
        self.update_ip()
        self.node_sign = self.generate_node_sign()

    def change_password(self, old_password, new_password):
        data = {
            'old_password': old_password,
            'new_password': new_password
        }
        return self.create_request(action='changepassword', data=data)

    def get_ip(self):
        data = {
            'get': 'candidate'
        }
        encoded = self.make_json_data(action='stun', data=data)
        response = self.make_post(url=self.server_addr, data=encoded)
        try:
            response = json.loads(response)
            if response['result'] != 'success':
                return None
        except:
            return None
        try:
            return int(response['info'])
        except Exception as e:
            logger.error("Invalid response '%s' (%s)", e, response)
            return None

    def update_ip(self):
        self._ip_lock.lock()
        try:
            self.ip_addr = self.get_ip()
        finally:
            self._ip_lock.unlock()

    @staticmethod
    def make_json_data(action, data=()):
        '''
        Retuns complete request data encoded in JSON format
        @param action Name of request action [str]
        @param data Request data [dict]
        @return JSON encoded request data [string]
        '''

        data = {} if not data else data
        try:
            encoded = json.dumps({
                'action': action,
                'data': data
            })
        except Exception as e:
            logger.error(
                "Failed to encoded request '%s' data %s into JSON format (%s)",
                action, repr(data), e)
            raise
        return encoded

    def get_request_data(self, action, data=(), force_update=False):
        """
        Add auth data to request data given and return its JSON encoded version

        @param action Request data 'action' field [string]
        @param data  Request data 'data' field [dict]
        @return JSON-encoded request data [string]
        """
        data = {} if not data else data
        if not self.node_sign or force_update:
            self.update_node_sign()

        # Add auth data
        if action not in ('signup', 'login'):
            data['user_hash'] = self.cfg.user_hash

        data['node_hash'] = self.cfg.node_hash
        data['node_sign'] = self.node_sign

        return self.make_json_data(action=action, data=data)

    def get_upload_request_data(self, fields, callback, force_update=False):
        """
        Add auth data to upload request fields given and return
        multipart/form data object

        @param fields Request data 'fields' [dict]
        @param callback Callback to be called on file chunk read [Function]
        @param force_update Instructs to update node sign [bool]
        @return multipart/form data object [MultipartEncoderMonitor]
        """

        if not self.node_sign or force_update:
            self.update_node_sign()

        # Add auth data
        fields['user_hash'] = self.cfg.user_hash
        fields['node_hash'] = self.cfg.node_hash
        fields['node_sign'] = self.node_sign

        e = encoder.MultipartEncoder(fields)
        m = encoder.MultipartEncoderMonitor(e, callback)
        return m

    def _create_request(self,
                        action,
                        server_addr,
                        data=(),
                        recurse_max=3,
                        enrich_data=True,
                        headers=()):
        data = {} if not data else data
        encoded = self.get_request_data(action, data) \
            if enrich_data else data

        try:
            response = self.make_post(url=server_addr, data=encoded,
                                      headers=headers)
            response = json.loads(response)
        except Exception as e:
            logger.error("Server response parsing failed with '%s'", e)
            if self._tracker:
                tb = traceback.format_list(traceback.extract_stack())
                self._tracker.error(tb, str(e))
            return None
        except:
            return None
        if 'result' not in response:
            if self._tracker:
                tb = traceback.format_list(traceback.extract_stack())
                self._tracker.error(tb, 'Result not in response')
            if recurse_max > 0:
                self.update_node_sign()
                return self._create_request(
                    action=action,
                    server_addr=server_addr,
                    data=data,
                    recurse_max=recurse_max - 1)
            else:
                return None
        if response['result'] == 'error':
            info = response.get('info', "")
            errcode = response.get('errcode', "")
            if self._tracker:
                tb = traceback.format_list(traceback.extract_stack())
                error = 'info: {}, debug: {}'.format(
                    info,
                    response.get('debug', ""))
                self._tracker.error(tb, error)
            if info == 'flst' and recurse_max > 0:
                self.update_node_sign()
                return self._create_request(
                    action=action,
                    server_addr=server_addr,
                    data=data,
                    recurse_max=recurse_max - 1)
            if errcode in ('SIGNATURE_INVALID', 'NODE_SIGN_NOT_FOUND') and \
                    recurse_max > 0:
                self.update_node_sign()
                return self._create_request(
                    action=action,
                    server_addr=server_addr,
                    data=data,
                    recurse_max=recurse_max - 1)
            if errcode == 'TIMEOUT':
                self.timeout_error.emit()
                return None
            else:
                return response
        return response

    def create_request(self, action, data=(), recurse_max=5):
        return self._create_request(action,
                                    self.server_addr,
                                    data,
                                    recurse_max)

    def create_event_request(self, action, data=(), recurse_max=3):
        return self._create_request(action,
                                    self.events_server_addr,
                                    data,
                                    recurse_max)

    def create_sharing_request(self, action, data=(), recurse_max=3):
        return self._create_request(action,
                                    self.sharing_server_addr,
                                    data,
                                    recurse_max)

    def create_upload_request(self, data, recurse_max=3):
        headers = {'Content-Type': data.content_type}
        return self._create_request("",
                                    self.upload_server_addr,
                                    data,
                                    recurse_max,
                                    enrich_data=False,
                                    headers=headers)

    def make_post(self, url, data, headers=()):
        try:
            logger.verbose("Sending POST request to '%s' with data '%s'",
                           url, data)
        except AttributeError:
            pass
        session = self._get_or_create_session(current_thread())
        try:
            kwargs = dict(timeout=(self.request_timeout,
                                   self.read_timeout))
            if headers:
                kwargs['headers'] = headers
            _res = session.post(url, data, **kwargs).text
            try:
                logger.verbose("Server replied: '%s'", _res)
            except AttributeError:
                pass
        except exceptions.Timeout as e:
            logger.error("Request failed due to timeout")
            _res = '{"result":"error","errcode":"TIMEOUT"}'
            if self._tracker:
                tb = traceback.format_list(traceback.extract_stack())
                self._tracker.error(tb, str(e))
        except Exception as e:
            logger.error("Request failed due to %s", e)
            _res = 'failure'
            if self._tracker:
                tb = traceback.format_list(traceback.extract_stack())
                self._tracker.error(tb, str(e))
        return _res

    def _get_or_create_session(self, thread):
        session = self._sessions.get(thread, None)
        if not session:
            session = Session()
            if self.cfg.host == REGULAR_URI:
                session.mount(self.cfg.host, SslPinningAdapter())
            self._sessions[thread] = session
        return session

    def sharing_enable(
            self, uuid, share_ttl):
        """
        'sharing_enable' request of share registration API.
        Registers file or folder sharing on API server

        @param uuid [str]
        @param share_ttl Time sharing be valid
            (in seconds) [int]
        @return (share_link [str], share_hash [str])
        @raise Client_APIError
        """

        data = {
            'uuid': uuid,
            'share_ttl': str(share_ttl),
            'share_password': None
        }
        response = self.create_sharing_request(
            action='sharing_enable', data=data)

        if response \
                and 'result' in response \
                and 'success' in response['result'] \
                and 'data' in response \
                and 'share_link' in response['data'] \
                and 'share_hash' in response['data']:
            share_link = str(response['data']['share_link'])
            share_hash = str(response['data']['share_hash'])
            return share_link, share_hash, ''
        elif response \
                and 'result' in response \
                and 'error' in response['result']:
            error_info = response.get('info', '')
            logger.error(
                "Sharing failed, server response: '%s'", response)
            return None, None, error_info
        else:
            logger.error(
                "Sharing failed, server response: '%s'", response)
            raise Client_APIError("Sharing failed", response)

    def sharing_disable(self, uuid):
        """
        'sharing_disable' request of share registration API.
        Registers file folder sharing cancelling on API server

        @param uuid [str]
        @raise Client_APIError
        """

        data = {
            'uuid': uuid,
        }
        response = self.create_sharing_request(
            action='sharing_disable', data=data)
        if not (response
                and 'result' in response
                and 'success' in response['result']):
            raise Client_APIError("Sharing failed", response)

    def file_event_create(self,
                          event_uuid,
                          file_name,
                          file_size,
                          folder_uuid,
                          diff_file_size,
                          file_hash):
        '''
        'file_event_create' request of file event registration API.
        Registers file creation on API server

        @return Server reply in the form
            {'result': status, 'info': server_message, data: useful_data}
        '''

        data = {
            "event_uuid": event_uuid,
            "file_name": ensure_unicode(file_name),
            "file_size": file_size,
            "folder_uuid": folder_uuid if folder_uuid else "",
            "diff_file_size": diff_file_size,
            "hash": file_hash,
        }

        return self.create_event_request(action='file_event_create', data=data)

    def file_event_update(self,
                          event_uuid,
                          file_uuid,
                          file_size,
                          last_event_id,
                          diff_file_size,
                          rev_diff_file_size,
                          file_hash):
        '''
        'file_event_update' request of file event registration API.
        Registers file update on API server

        @param file_uuid UUID of file assigned on event creation [string]
        @param last_event_id Maximum ID of file event on the file being
            processed known to the node
        @return Server reply in the form
            {'result': status, 'info': server_message, data: useful_data}
        '''

        data = {
            "event_uuid": event_uuid,
            "file_uuid": file_uuid,
            "file_size": file_size,
            "last_event_id": str(last_event_id),
            "diff_file_size": diff_file_size,
            "rev_diff_file_size": rev_diff_file_size,
            "hash": file_hash,
        }
        return self.create_event_request(action='file_event_update', data=data)

    def file_event_delete(self, event_uuid, file_uuid, last_event_id):
        '''
        'file_event_delete' request of file event registration API.
        Registers file deletion on API server

        @param file_uuid UUID of file assigned on event creation [string]
        @param last_event_id Maximum ID of file event on the file being
            processed known to the node
        @return Server reply in the form
            {'result': status, 'info': server_message, data: useful_data}
        '''

        data = {
            "event_uuid": event_uuid,
            "file_uuid": file_uuid,
            "last_event_id": str(last_event_id)
        }
        return self.create_event_request(action='file_event_delete', data=data)

    def file_event_move(self,
                        event_uuid,
                        file_uuid,
                        last_event_id,
                        new_file_name,
                        new_folder_uuid):
        '''
        'file_event_move' request of file event registration API.
        Registers file moving on API server

        @param file_uuid UUID of file assigned on event creation [string]
        @param last_event_id Maximum ID of file event on the file being
            processed known to the node
        @param new_file_name New name of file [unicode]
        @param new_folder_uuid uuid of a folder where file will be placed
        @return Server reply in the form
            {'result': status, 'info': server_message, data: useful_data}
        '''

        data = {
            "event_uuid": event_uuid,
            "file_uuid": file_uuid,
            "new_folder_uuid": new_folder_uuid if new_folder_uuid else "",
            "new_file_name": new_file_name,
            "last_event_id": str(last_event_id)
        }
        return self.create_event_request(action='file_event_move', data=data)

    def folder_event_create(self,
                            event_uuid,
                            folder_name,
                            parent_folder_uuid):

        if parent_folder_uuid is None:
            parent_folder_uuid = ""

        data = {
            "event_uuid": event_uuid,
            "folder_name": folder_name,
            "parent_folder_uuid": parent_folder_uuid,
        }
        return self.create_event_request(
            action='folder_event_create', data=data)

    def folder_event_move(self,
                          event_uuid,
                          folder_uuid,
                          last_event_id,
                          new_folder_name,
                          new_parent_folder_uuid):

        if new_parent_folder_uuid is None:
            new_parent_folder_uuid = ""

        data = {
            "event_uuid": event_uuid,
            "folder_uuid": folder_uuid,
            "new_folder_name": new_folder_name,
            "new_parent_folder_uuid": new_parent_folder_uuid,
            "last_event_id": str(last_event_id),
        }
        return self.create_event_request(
            action='folder_event_move', data=data)

    def folder_event_delete(self, event_uuid, folder_uuid, last_event_id):
        data = {
            "event_uuid": event_uuid,
            "folder_uuid": folder_uuid,
            "last_event_id": str(last_event_id),
        }
        return self.create_event_request(
            action='folder_event_delete', data=data)

    def file_event_get_filelist(self, last_event_id):
        '''
        'file_list' request of file event registration API.
        Obtains list of files created/updated after event with given ID

        @param last_event_id Maximum ID of file event known to the node
        @return Server reply in the form
            {'result': status, 'info': server_message, data: useful_data}
        '''

        data = {
            "last_event_id": str(last_event_id)
        }

        return self.create_event_request(action='file_list', data=data)

    def file_event_get_events(self, last_event_id):
        '''
        'file_events' request of file event registration API.
        Obtains events registered on server after event with given ID

        @param last_event_id Maximum ID of file event known to the node
        @return Server reply in the form
            {'result': status, 'info': server_message, data: useful_data}
        '''

        data = {
            "last_event_id": str(last_event_id)
        }

        return self.create_event_request(action='file_events', data=data)

    def start_http_download(self, upload_id):
        """
        'download' request for starting of uploaded file download via HTTP
        protocol. After node auth checking the server should return redirect to
        actual file URL

        @param upload_id ID of uploaded file [int]
        """

        data = {
            "upload_id": str(upload_id)
        }

        return self.create_event_request(action='download', data=data)

    def remote_action_done(self, remote_action_uuid):
        return self.create_request(action='remote_action_done',
                                   data=dict(action_uuid=remote_action_uuid))

    def patch_ready(self, patch_uuid, patch_size):
        return self.create_event_request(action='patch_ready',
                                         data=dict(diff_uuid=patch_uuid,
                                                   diff_size=patch_size))

    def get_token_login_link(self):
        return self.create_request(action='get_token_login_link')

    def node_management_action(self, action, node_id, action_type=""):
        if action_type:
            data = dict(target_node_id=node_id, action_type=action_type)
        else:
            data = dict(node_id=node_id)
        return self.create_request(action=action, data=data)

    def get_notifications(self, limit, from_id):
        data = {
            'from': from_id,
            'limit': limit
        }
        return self.create_request(action="getNotifications", data=data)

    def accept_invitation(self, colleague_id):
        data = dict(colleague_id=colleague_id)
        return self.create_sharing_request(
            action="collaboration_join", data=data)

    def set_uris(self):
        self.server_addr = API_URI.format(self.cfg.host)
        self.events_server_addr = API_EVENTS_URI.format(self.cfg.host)
        self.sharing_server_addr = API_SHARING_URI.format(self.cfg.host)
        self.upload_server_addr = API_UPLOAD_URI.format(self.cfg.host)

    # Collaboration settings requests

    def collaboration_info(self, uuid):
        data = dict(uuid=uuid)
        return self.create_sharing_request(
            action="collaboration_info", data=data)

    def colleague_delete(self, uuid, colleague_id):
        data = dict(uuid=uuid, colleague_id=colleague_id)
        return self.create_sharing_request(
            action="colleague_delete", data=data)

    def colleague_edit(self, uuid, colleague_id, access_type):
        data = dict(uuid=uuid, colleague_id=colleague_id,
                    access_type=access_type)
        return self.create_sharing_request(
            action="colleague_edit", data=data)

    def colleague_add(self, uuid, colleague_email, access_type):
        data = dict(uuid=uuid, colleague_email=colleague_email,
                    access_type=access_type)
        return self.create_sharing_request(
            action="colleague_add", data=data)

    def collaboration_cancel(self, uuid):
        data = dict(uuid=uuid)
        return self.create_sharing_request(
            action="collaboration_cancel", data=data)

    def collaboration_leave(self, uuid):
        data = dict(uuid=uuid)
        return self.create_sharing_request(
            action="collaboration_leave", data=data)

    # Support requests

    def send_support_message(self, subject, body, log_file_name):
        data = dict(subject=subject, body=body)
        if log_file_name:
            data['log_file_name'] = log_file_name
        return self.create_request(action="support", data=data)

    def upload_file(self, path, mime_type, callback=lambda m: None):
        filename = op.basename(path)
        with open(path, 'rb') as f:
            fields = {'UploadLogsForm[uploadedFile]': (
                filename, f, mime_type)}
            data = self.get_upload_request_data(fields, callback)
            return self.create_upload_request(data)
