# -*- coding: utf-8 -*-

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
from .proto.proto_pb2 import _MESSAGE, _MESSAGES
from .proto.proto_pb2 import Message
from google.protobuf import reflection as _reflection
import types


_Message_Events = {
    Message.PATCH: {
        Message.AVAILABILITY_INFO_REQUEST:
            "on_patch_availability_info_request",
        Message.AVAILABILITY_INFO_RESPONSE:
            "on_patch_availability_info_response",
        Message.AVAILABILITY_INFO_ABORT:
            "on_patch_availability_info_abort",
        Message.AVAILABILITY_INFO_FAILURE:
            "on_patch_availability_info_failure",
        Message.DATA_REQUEST:
            "on_patch_data_request",
        Message.DATA_RESPONSE:
            "on_patch_data_response",
        Message.DATA_ABORT:
            "on_patch_data_abort",
        Message.DATA_FAILURE:
            "on_patch_data_failure",
    },
    Message.FILE: {
        Message.AVAILABILITY_INFO_REQUEST:
            "on_file_availability_info_request",
        Message.AVAILABILITY_INFO_RESPONSE:
            "on_file_availability_info_response",
        Message.AVAILABILITY_INFO_ABORT:
            "on_file_availability_info_abort",
        Message.AVAILABILITY_INFO_FAILURE:
            "on_file_availability_info_failure",
        Message.DATA_REQUEST:
            "on_file_data_request",
        Message.DATA_RESPONSE:
            "on_file_data_response",
        Message.DATA_ABORT:
            "on_file_data_abort",
        Message.DATA_FAILURE:
            "on_file_data_failure",
    },
}


_Messages_Events = {
    "on_patch_availability_info_request":
        "on_patch_availability_info_requests",
    "on_patch_availability_info_response":
        "on_patch_availability_info_responses",
    "on_patch_availability_info_failure":
        "on_patch_availability_info_responses",
    "on_file_availability_info_request":
        "on_file_availability_info_requests",
    "on_file_availability_info_response":
        "on_file_availability_info_responses",
    "on_file_availability_info_failure":
        "on_file_availability_info_responses",
}


def get_event_name(obj_type, msg_type, repeating=False):
    try:
        event_name = _Message_Events[obj_type][msg_type]
        if repeating:
            event_name = _Messages_Events[event_name]
    except KeyError:
        print(">> Error: cannot get event_name for obj_type '{}'"
              " and msg_type '{}'".format(obj_type, msg_type))
        return None
    return event_name


class MessageMeta(_reflection.GeneratedProtocolMessageType):

    def __new__(self, name, bases, dct):
        x = super().__new__(self, name, bases, dct)
        for f in self.__dict__.keys():
            if isinstance(self.__dict__[f], types.FunctionType):
                setattr(x, f, self.__dict__[f])
        return x

    def availability_info_request(self, obj_type, obj_id):
        self.magic_cookie = 0x7a52fa73
        self.mtype = self.AVAILABILITY_INFO_REQUEST
        self.obj_id = obj_id
        self.obj_type = obj_type
        return self.SerializeToString()

    def availability_info_response(self, obj_type, obj_id, info):
        self.magic_cookie = 0x7a52fa73
        self.mtype = self.AVAILABILITY_INFO_RESPONSE
        self.obj_id = obj_id
        self.obj_type = obj_type
        for offset, length in info:
            info = self.info.add()
            info.offset = offset
            info.length = length
        return self.SerializeToString()

    def availability_info_abort(self, obj_type, obj_id):
        self.magic_cookie = 0x7a52fa73
        self.mtype = self.AVAILABILITY_INFO_ABORT
        self.obj_id = obj_id
        self.obj_type = obj_type
        return self.SerializeToString()

    def availability_info_failure(self, obj_type, obj_id, error):
        self.magic_cookie = 0x7a52fa73
        self.mtype = self.AVAILABILITY_INFO_FAILURE
        self.obj_id = obj_id
        self.obj_type = obj_type
        self.error = error
        return self.SerializeToString()

    def data_request(self, obj_type, obj_id, offset, length):
        self.magic_cookie = 0x7a52fa73
        self.mtype = self.DATA_REQUEST
        self.obj_id = obj_id
        self.obj_type = obj_type
        info = self.info.add()
        info.offset = offset
        info.length = length
        return self.SerializeToString()

    def data_response(self, obj_type, obj_id, offset, length, data):
        self.magic_cookie = 0x7a52fa73
        self.mtype = self.DATA_RESPONSE
        self.obj_id = obj_id
        self.obj_type = obj_type
        info = self.info.add()
        info.offset = offset
        info.length = length
        self.data = data
        return self.SerializeToString()

    def data_abort(self, obj_type, obj_id, offset):
        self.magic_cookie = 0x7a52fa73
        self.mtype = self.DATA_ABORT
        self.obj_id = obj_id
        self.obj_type = obj_type
        if offset is not None:
            info = self.info.add()
            info.offset = offset
        return self.SerializeToString()

    def data_failure(self, obj_type, obj_id, offset, error):
        self.magic_cookie = 0x7a52fa73
        self.mtype = self.DATA_FAILURE
        self.obj_id = obj_id
        self.obj_type = obj_type
        info = self.info.add()
        info.offset = offset
        self.error = error
        return self.SerializeToString()

    def decode(self, _buffer, remote_peer_id=None):
        self.ParseFromString(_buffer)
        return self


class Message(metaclass=MessageMeta):
    DESCRIPTOR = _MESSAGE


class MessagesMeta(_reflection.GeneratedProtocolMessageType):

    def __new__(self, name, bases, dct):
        x = super().__new__(self, name, bases, dct)
        for f in self.__dict__.keys():
            if isinstance(self.__dict__[f], types.FunctionType):
                setattr(x, f, self.__dict__[f])
        return x

    def messages(self, msg):
        for item in msg:
            self.msg.add().MergeFromString(item)
        return self.SerializeToString()

    def decode(self, _buffer, remote_peer_id=None):
        self.ParseFromString(_buffer)
        return self


class Messages(metaclass=MessagesMeta):
    DESCRIPTOR = _MESSAGES
