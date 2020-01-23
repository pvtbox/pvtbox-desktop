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
import tarfile
from hashlib import md5
import os
from os import path as op

import shutil

# import time
from os.path import join, exists

from sortedcontainers import SortedDict
SortedDict.iteritems = SortedDict.items

from common.utils import remove_file, make_dirs, \
    get_patches_dir, get_copies_dir, copy_file, generate_uuid
from common.constants import SIGNATURE_BLOCK_SIZE

# Setup logging
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class Rsync:

    class AlreadyPatched(Exception):
        pass

    @staticmethod
    def hash_from_block_checksum(block_checksum):
        hasher = md5()
        if not isinstance(block_checksum, SortedDict):
            block_checksum = SortedDict(block_checksum)
        if block_checksum:
            for checksum in block_checksum.values():
                hasher.update(checksum.encode('utf-8'))
        return hasher.hexdigest()

    @staticmethod
    def block_checksum(filepath, blocksize=SIGNATURE_BLOCK_SIZE):
        with open(filepath, 'rb') as f:
            data = f.read(blocksize)
            hashes = SortedDict()
            offset = 0
            data_size = len(data)
            while data_size:
                try:
                    hashes[offset] = md5(data).hexdigest()
                    offset += data_size
                    data = f.read(blocksize)
                    data_size = len(data)
                except:
                    raise KeyError("Inorrect hash table")
            return hashes

    @staticmethod
    def getfileinfo(filepath):
        handle_file = os.open(filepath, os.O_RDONLY)
        info = os.fstat(handle_file)
        os.close(handle_file)
        return info

    @staticmethod
    def get_data(handle, offset, size=-1):
        if offset < 0:
            raise IOError("Offset is negative number")
        handle.seek(offset)
        return handle.read(size)

    @classmethod
    def create_patch(
            cls, modify_file, root,
            old_blocks_hashes=None, new_blocks_hashes=None,
            old_file_hash=None,
            new_file_hash=None,
            uuid=None,
            blocksize=SIGNATURE_BLOCK_SIZE):

        def get_patch_filename(suffix):
            return os.path.join(
                get_patches_dir(root),
                'patches',
                str(old_file_hash) +
                str(new_file_hash) +
                suffix)

        patch_data_file = get_patch_filename('.patch_data')

        # Create directory structure to store patch file
        make_dirs(patch_data_file)

        with open(modify_file, 'rb') as handle_file, \
                open(patch_data_file, 'wb') as data_file:
            blocks = SortedDict()
            patch = dict()
            new_blocks_hashes_search = dict()
            if old_blocks_hashes:
                old_blocks_hashes_search = \
                    dict((value, key) for key, value in
                         old_blocks_hashes.items())
            else:
                old_blocks_hashes_search = dict()
            if new_blocks_hashes is None:
                new_blocks_hashes = cls.block_checksum(
                    filepath=modify_file, blocksize=blocksize)
            for new_offset, new_hash in new_blocks_hashes.items():
                clone_block_offset = new_blocks_hashes_search.get(
                    new_hash, None)
                from_patch = clone_block_offset is not None
                clone_block_offset = clone_block_offset if from_patch \
                    else old_blocks_hashes_search.get(new_hash, None)
                if clone_block_offset is None:
                    data_file_offset = data_file.tell()
                    data = cls.get_data(handle=handle_file,
                                        size=blocksize,
                                        offset=new_offset)
                    data_file.write(data)
                    data_size = data_file.tell() - data_file_offset
                    blocks[new_offset] = dict(
                        new=True,
                        hash=new_hash,
                        offset=data_file_offset,
                        data_size=data_size,
                    )
                    new_blocks_hashes_search[new_hash] = new_offset
                else:
                    blocks[new_offset] = dict(new=False,
                                              hash=new_hash,
                                              offset=clone_block_offset,
                                              from_patch=from_patch)

        patch['old_hash'] = old_file_hash
        if new_file_hash is None:
            new_file_hash = Rsync.hash_from_block_checksum(new_blocks_hashes)
        patch['new_hash'] = new_file_hash

        info = cls.getfileinfo(modify_file)
        patch['blocks'] = blocks
        patch['time_modify'] = info.st_mtime
        patch['size'] = info.st_size
        patch['blocksize'] = blocksize

        patch_info_file = get_patch_filename('.patch_info')

        with open(patch_info_file, 'w') as info_file:
            json.dump(patch, info_file)

        if uuid is not None:
            patch_archive_file = op.join(
                get_patches_dir(root, create=True), uuid)
        else:
            patch_archive_file = get_patch_filename('.patch')

        with tarfile.open(patch_archive_file, 'w') as archive:
            archive.add(patch_info_file, arcname='info')
            archive.add(patch_data_file, arcname='data')
        remove_file(patch_info_file)
        remove_file(patch_data_file)

        patch['archive_file'] = patch_archive_file
        patch['archive_size'] = os.stat(patch_archive_file).st_size
        return patch

    @classmethod
    def accept_patch(cls,
                     patch_archive,
                     unpatched_file,
                     root,
                     known_old_hash=None):
        """
        Accepts patch
        Args:
            patch_archive:
            unpatched_file:

        Returns:
            object: (file_hash, file_blocks_hashes)
        """

        try:
            logger.info('accepting patch')
            with tarfile.open(patch_archive, 'r') as archive:
                patch_info = patch_data = None
                for member in archive.getmembers():
                    if member.name == 'info':
                        logger.debug('extracting patch info')
                        patch_info = archive.extractfile(member)
                        patch_info = str(patch_info.read(), encoding='utf-8')
                        patch_info = json.loads(patch_info)
                        logger.debug('extracted patch info')
                    elif member.name == 'data':
                        logger.debug('extracting patch data')
                        patch_data = archive.extractfile(member)
                        logger.debug('extracted patch data')
                if patch_info is None or patch_data is None:
                    raise IOError('Invalid patch archive')
                if patch_info.get('new_hash', None) == known_old_hash:
                    raise cls.AlreadyPatched()
                if patch_info.get('old_hash', None) != known_old_hash:
                    raise IOError('Trying to apply patch for wrong file, '
                                  'expected file hash: {}, actual: {}'
                                  .format(patch_info.get('old_hash', None),
                                          known_old_hash))
                return cls._accept_patch(
                    patch_info, patch_data, unpatched_file, root)
        except tarfile.TarError as e:
            logger.error(
                "Failed to accept patch from '%s' archive (%s)",
                patch_archive, e)
            raise IOError('Invalid patch archive')

    @staticmethod
    def _accept_patch(patch_info, patch_data, unpatched_file, root):
        file_blocks_hashes = SortedDict()
        blocksize = patch_info['blocksize']
        temp_name = os.path.join(
            get_patches_dir(root), '.patching_' + generate_uuid())

        blocks = SortedDict(
            (int(k), v) for k, v in patch_info['blocks'].items())

        source_file = None
        if op.exists(unpatched_file):
            source_file = open(unpatched_file, "rb")
        with open(temp_name, "wb") as temp_file:
            # count = 0
            # min = 999999999.0
            # max = 0.0
            # avg = 0.0
            # sum = 0.0
            for offset, block in blocks.items():
                # count += 1
                # start_time = time.time()
                block_offset = int(block['offset'])
                if block['new']:
                    patch_data.seek(block_offset)
                    data_size = block['data_size']
                    data = patch_data.read(data_size)
                else:
                    if block['from_patch']:
                        patch_offset = blocks[block_offset]['offset']
                        data_size = blocks[block_offset].get('data_size',
                                                             blocksize)
                        patch_data.seek(patch_offset)
                        data = patch_data.read(data_size)
                    else:
                        if source_file is None:
                            raise IOError("Source file not found")
                        source_file.seek(block_offset)
                        data = source_file.read(blocksize)
                temp_file.seek(offset)
                temp_file.write(data)
                file_blocks_hashes[offset] = block['hash']
                # diff = time.time() - start_time
                # min = diff if diff < min else min
                # max = diff if diff > max else max
                # avg = diff if avg == 0 else (avg + diff) / 2
                # sum += diff
                # logger.debug(
                #     'processed block %s:%s in %s', count, len(blocks), diff)
        # logger.debug(
        #     'processing blocks time:%s, min:%s, max:%s, avg:%s',
        #     sum, min, max, avg)
        if source_file:
            source_file.close()
        logger.debug('calculating patched file signature')
        file_signature = Rsync.block_checksum(temp_name, blocksize=blocksize)
        logger.debug('calculated patched file signature')
        if file_signature != file_blocks_hashes:
            remove_file(temp_name)
            raise IOError(
                "Invalid patch result, expected signature: {}, actual: {}"
                .format(file_blocks_hashes, file_signature))

        new_hash = patch_info['new_hash']
        logger.debug('moving patched file')
        copy = join(get_copies_dir(root), new_hash)
        if not exists(copy):
            copy_file(temp_name, copy)
        shutil.move(temp_name, unpatched_file)
        logger.debug('moved patched file')

        return new_hash, file_blocks_hashes, patch_info['old_hash']

    @staticmethod
    def find_data_block(hash, blocks):
        for block_offset, block in blocks.items():
            if block['hash'] == hash and block['new']:
                return block_offset, block

        return None, None
