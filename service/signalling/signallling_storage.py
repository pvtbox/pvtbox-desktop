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

import logging


# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class Storage(object):
    """
    Stores info on nodes, shared files and folders
    """

    def __init__(self, parent):
        """
        Constructor

        @param parent
            Signal server client
        """

        self._parent = parent

        # List of known node info in the form {node_id: node_info}
        self.node_info = {}

        # File/folder sharing info (in the form {uuid: info})
        self.sharing_info = {}

    def get_known_node_ids(self, allowed_types=None, online_only=True):
        """
        Returns list of known node IDs

        @param allowed_types List of node types to return [iterable]
        @param online_only
            Enables returning IDs of nodes with 'is_online' flag set [bool]
        @return known node IDs [tuple]
        """

        nodes_info = self.node_info.items()

        if online_only:
            nodes_info = [ni for ni in nodes_info if ni[1].get('is_online', None)]

        if allowed_types is not None:
            nodes_info = [ni for ni in nodes_info if ni[1].get('type', None) in allowed_types]

        return tuple([ni[0] for ni in nodes_info])

    def set_node_info(self, node_info):
        self.node_info = node_info
        logger.debug(
            "Nodes known: %s", len(self.node_info))
        self._parent.emit_signal('node_list_obtained', dict(self.node_info))

    def add_node(self, n_info):
        """
        Add node ID to the list of known IDs

        @param n_info Info on node connected [dict]
        """

        node_id = n_info['id']
        if node_id not in self.node_info:
            self.node_info[node_id] = dict(n_info)
        else:
            self.node_info[node_id].update(n_info)

        logger.debug(
            "Nodes known: %s", len(self.node_info))
        logger.info(
            "Online node IDs: %s",
            self.get_known_node_ids(online_only=True))
        # Emit signals
        self._parent.emit_signal('node_connect', dict(n_info))
        self._parent.emit_signal('nodes_changed', dict(self.node_info))

    def remove_node(self, node_id):
        """
        Remove node ID from the list of known IDs

        @param node_id ID of node being removed [string]
        """

        if node_id in self.node_info:
            if "type" in self.node_info[node_id] \
                    and self.node_info[node_id]["type"] != "node":
                del self.node_info[node_id]
            else:
                if "is_online" in self.node_info[node_id]:
                    self.node_info[node_id]['is_online'] = False
            logger.debug(
                "Nodes known: %s", len(self.node_info))
            logger.info(
                "Online node IDs: %s",
                self.get_known_node_ids(online_only=True))
            # Emit signals
            self._parent.emit_signal('node_disconnect', node_id)
            self._parent.emit_signal('nodes_changed', dict(self.node_info))

    def get_node_info(self, node_id):
        """
        Returns node info fo node ID specified

        @param node_id ID of node [string]
        @return Node info [dict] or None
        """

        return self.node_info.get(node_id, None)

    def get_sharing_info(self):
        """
        Returns known shared files/folders info

        @return Shared files info in the form {share_hash: filename}
        """

        return dict(self.sharing_info)

    def sharing_enable(
            self, uuid, share_hash, share_link, emit_signals=True):
        """
        Stores information on shared file/folder

        @param uuid Unique shared file/folder ID [str]
        @param share_hash Unique sharing operation ID [string]
        @param share_link URL of download page of the shared file/folder [str]
        @param emit_signals
            Flag indicating whether signals should be emitted [bool]
        """

        data_changed = False

        if uuid not in self.sharing_info:
            data_changed = True
        else:
            if self.sharing_info[uuid]['share_hash'] != share_hash:
                data_changed = True

        if data_changed:
            logger.debug(
                "Add info on shared file/folder UUID=%s (share_hash=%s)...",
                uuid, share_hash)
            self.sharing_info[uuid] = dict(
                uuid=uuid, share_hash=share_hash, share_link=share_link)

            # Emit signals if necessary
            if emit_signals:
                self._parent.emit_signal(
                    'sharing_changed', dict(self.sharing_info))

    def sharing_disable(self, uuid, emit_signals=True):
        """
        Removes information on shared file

        @param uuid Unique shared file/folder ID [str]
        @param emit_signals
            Flag indicating whether signals should be emitted [bool]
        """

        logger.debug(
            "Remove info on shared file/folder UUID=%s...", uuid)

        sharing_info = self.sharing_info.pop(uuid, None)
        # Emit signals if necessary
        if sharing_info and emit_signals:
            self._parent.emit_signal(
                'sharing_disable', sharing_info)
            self._parent.emit_signal(
                'sharing_changed', dict(self.sharing_info))

    def clear_sharing_info(self):
        self.sharing_info.clear()

    def update_node_status(self, node_id, data):
        """
        Updates node information with data given

        @param node_id ID of node [string]
        @param data Node info update data [dict]
        """

        if node_id in self.node_info:
            self.node_info[node_id].update(data)
            # Emit signals
            self._parent.emit_signal('nodes_changed', dict(self.node_info))
