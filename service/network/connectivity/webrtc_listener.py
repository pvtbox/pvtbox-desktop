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
import webrtc


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class WebRtcListener(webrtc.WebRtcListener):
    def __init__(self, connectivity_service):
        self._connectivity_service = connectivity_service
        webrtc.WebRtcListener.__init__(self)

    def on_connected(self, connection_id):
        logger.debug("on_connected %s", connection_id)
        self._connectivity_service.connected.emit(connection_id)

    def on_disconnected(self, connection_id):
        logger.debug("on_disconnected %s", connection_id)
        self._connectivity_service.disconnected.emit(connection_id)

    def on_message(self, connection_id, msg):
        self._connectivity_service.message.emit((connection_id, msg))

    def on_buffered_amount_change(self, connection_id, amount):
        self._connectivity_service.buffered_amount_changed.emit(
            connection_id, amount)

    def on_local_description(self, connection_id, type, sdp):
        logger.debug("on_local_description from %s, type: %s, sdp: %s",
                     connection_id, type, sdp)
        self._connectivity_service.on_local_description.emit(connection_id, type, sdp)

    def on_candidate(self, connection_id, sdp_mid, sdp_m_line_index, candidate):
        logger.debug("on_candidate from %s, sdp_mid: %s, sdp_m_line_index: %s, candidate: %s",
                     connection_id, sdp_mid, sdp_m_line_index, candidate)
        self._connectivity_service.on_candidate.emit(connection_id, sdp_mid, sdp_m_line_index, candidate)

    def on_statistic(self, connection_id, statistic):
        self._connectivity_service.statistic.emit(connection_id, statistic)