# -*- coding: utf-8 -*-

# Setup logging
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

from requests.adapters import HTTPAdapter, DEFAULT_POOLBLOCK
from urllib3 import PoolManager

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class SslPinningAdapter(HTTPAdapter):
    fingerprint = ''

    def __init__(self, *args, **kwargs):
        logger.debug("init")
        self.poolmanager = None
        self.fingerprint = kwargs.pop(
            "fingerprint", "86025017022f6dcf9022d6fb867c3bb3bdc621103ddd8e9ed2c891a46d8dd856")
        HTTPAdapter.__init__(self, *args, **kwargs)

    def init_poolmanager(self, connections, maxsize, block=DEFAULT_POOLBLOCK, **pool_kwargs):
        """Initializes a urllib3 PoolManager.

        This method should not be called from user code, and is only
        exposed for use when subclassing the
        :class:`HTTPAdapter <requests.adapters.HTTPAdapter>`.

        :param connections: The number of urllib3 connection pools to cache.
        :param maxsize: The maximum number of connections to save in the pool.
        :param block: Block when no free connections are available.
        :param pool_kwargs: Extra keyword arguments used to initialize the Pool Manager.
        """
        # save these values for pickling
        self._pool_connections = connections
        self._pool_maxsize = maxsize
        self._pool_block = block

        self.poolmanager = PoolManager(
            num_pools=connections, maxsize=maxsize,
            block=block, strict=True,
            assert_fingerprint=self.fingerprint,
            **pool_kwargs)
