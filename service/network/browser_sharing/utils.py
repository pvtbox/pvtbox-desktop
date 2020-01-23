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
from io import StringIO
from .message import Message


# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def make_response_message(obj_id, block_offset, block_length, block_data):
    '''
    Makes response message containing given data of the object with given ID

    @param obj_id Object ID [string]
    @param block_offset Offset of data block given from the object
        beginning [int]
    @param block_length Length of data block given [int]
    @param block_data Data for
    @return Serialized message data [string] or None
    '''

    # Check actual data length
    if len(block_data) != block_length:
        logger.error(
            "Obtained %s of %s bytes of data for object ID '%s' (offset %s)",
            len(block_data), block_length, obj_id, block_offset)
        return

    # Create response message
    try:
        block = Message().response(
            obj_id, block_offset, block_length, block_data)
    except Exception as e:
        logger.error(
            "Failed to make proto response for block length %s offset %s "
            "of object ID '%s' (%s)", block_length, block_offset, obj_id, e)
        return

    return block


def make_response(obj_id, chunk_offset, chunk_length, chunk_data, block_len):
    '''
    Creates a series of protocol response messages as the response to the
    browser node exchange protocol request for object with given ID chunk.

    @param obj_id Object ID [string]
    @param chunk_offset Offset of chunk requested from the
        object beginning [int]
    @param chunk_length Length of chunk requested [int]
    @param chunk_data Data for requested object chunk [StringIO]
    @param block_len Length of separate block (encoded Message) [int]
    @return Response data [string] or None
    '''

    response_data = StringIO()
    input_data = StringIO(chunk_data)

    # Init counters
    byte_count = 0
    block_count = 1
    block_offset = chunk_offset

    while byte_count < chunk_length:
        # Make blocks to fit into WebRTC chunks
        block_data_length = \
            block_len - Message.header_len(obj_id, block_offset, block_len)
        # It is the last block shorter than others
        if chunk_length - byte_count < block_data_length:
            # Reduce block length to prevent extra data sending
            block_data_length = chunk_length - byte_count
        # Read data for the block
        block_data = input_data.read(block_data_length)
        # Create next block
        block_msg = make_response_message(
            obj_id, block_offset, block_data_length, block_data)
        if not block_msg:
            logger.error(
                "Failed to format block #%s (offset %s) of object %s",
                block_count, block_offset, obj_id)
            return None
        response_data.write(block_msg)
        byte_count += block_data_length
        block_offset += block_data_length
        block_count += 1

    logger.info("Created chunk of length %s for object ID '%s' (offset %s)",
                byte_count, obj_id, chunk_offset)

    result = response_data.getvalue()
    response_data.close()
    input_data.close()
    return result
