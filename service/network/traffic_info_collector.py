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
from PySide2.QtCore import QTimer, QObject
from time import time, sleep
import logging

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

DROP_INFO_INTERVAL = 30


class TrafficInfoCollector(QObject):

    def __init__(self, ss_client):
        QObject.__init__(self, parent=None)

        """
        info collection example:
        example:
        {"xxxyyy":                          #obj_id
            {                               # [tx_wd, tx_wr, rx_wd, rx_wr]
                True: [0, 25, 33, 0],       #info for is_share=True
                False: [11, 22, 33, 44],    #info for is_share=False
                "ts": 1546973095            #starting timestamp
            }
        }
        """
        self._info_collection = {}
        self._ss_client = ss_client
        self._timer = QTimer(self)
        self._timer.setInterval(1000 * DROP_INFO_INTERVAL)
        self._timer.timeout.connect(self._drop_info)

    def add_info_tx(self, info_tx):
        # logger.debug("info_tx: %s", info_tx)
        obj_id, tx_wd, tx_wr, is_share = info_tx
        self._add_info(obj_id, [tx_wd, tx_wr, 0, 0], is_share)

    def add_info_rx(self, info_rx):
        # logger.debug("info_rx: %s", info_rx)
        obj_id, rx_wd, rx_wr, is_share = info_rx
        self._add_info(obj_id, [0, 0, rx_wd, rx_wr], is_share)

    def _add_info(self, obj_id, info, is_share):
        info_id = self._info_collection.get(obj_id, None)
        if info_id is None:
            self._info_collection[obj_id] = {is_share: info, "ts": int(time())}
        else:
            info_share = info_id.get(is_share, None)
            if info_share is None:
                self._info_collection[obj_id][is_share] = info
            else:
                self._info_collection[obj_id][is_share] = \
                    [sum(x) for x in zip(info_share, info)]

    def start(self):
        self._info_collection = {}
        if not self._ss_client:
            return
        self._timer.start()

    def stop(self):
        if self._timer.isActive():
            self._timer.stop()
        self._drop_info()
        sleep(0.5)

    def _drop_info(self):
        """
        drop info to signalling server
        """

        if not self._ss_client:
            return

        if not self._ss_client.is_connected():
            return

        if not self._info_collection:
            return

        logger.debug("drop traffic info to signalling server ..")
        # logger.debug(self._info_collection)

        info_list = []
        for obj_id in self._info_collection.keys():
            ts = self._info_collection[obj_id]["ts"]
            interval = int(time()) - ts
            for is_share in (True, False):
                info_share = self._info_collection[obj_id].get(is_share, None)
                if info_share is None:
                    continue
                info = {
                    "event_uuid": obj_id,
                    "interval": str(interval),
                    "is_share": str(int(is_share)),
                    "tx_wd": str(info_share[0]),
                    "tx_wr": str(info_share[1]),
                    "rx_wd": str(info_share[2]),
                    "rx_wr": str(info_share[3]),
                }
                info_list.append(info)

        if not info_list:
            return

        status = self._ss_client.send_traffic_info(info_list)
        if status is not True:
            logger.warning("Message '%s' has not been sent")
            return

        self._info_collection = {}


if __name__ == "__main__":

    info_collector = TrafficInfoCollector(ss_client=None)
    info_collector.start()
    # info_tx tuple example: (obj_id, tx_wd, tx_wr, is_share)
    info_collector.add_info_rx(("xx-yy", 11, 22, True),)
    assert info_collector._info_collection["xx-yy"][True] == [0, 0, 11, 22]
    ts = info_collector._info_collection["xx-yy"]["ts"]
    assert ts <= int(time())
    info_collector.add_info_rx(("xx-yy", 11, 22, True),)
    assert info_collector._info_collection["xx-yy"][True] == [0, 0, 22, 44]
    assert info_collector._info_collection["xx-yy"]["ts"] == ts
    info_collector.add_info_rx(("xx-yy", 55, 0, False),)
    assert info_collector._info_collection["xx-yy"][False] == [0, 0, 55, 0]
    info_collector.add_info_tx(("xx-yy", 7, 8, False),)
    assert info_collector._info_collection["xx-yy"][False] == [7, 8, 55, 0]
    info_collector.add_info_tx(("xx-yy", 7, 8, False),)
    assert info_collector._info_collection["xx-yy"][False] == [14, 16, 55, 0]
    info_collector.add_info_tx(("xx-yy", 7, 8, False),)
    info_collector.stop()
