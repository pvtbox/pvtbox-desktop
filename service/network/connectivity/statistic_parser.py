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
from collections import defaultdict
import logging
import json


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class StatisticParser(object):
    @classmethod
    def parse_statistic(cls, statistic):
        logger.debug("Parsing statistic...")
        try:
            statistic = json.loads(statistic)
        except Exception as e:
            logger.warning("Can't load statistic. Reason: %s", e)
            return None

        statistic_dict = defaultdict(list)
        for stat in statistic:
            try:
                key = stat['type']
                statistic_dict[key].append(stat)
            except KeyError:
                logger.warning("Unexpected statistics item without type: %s",
                               stat)

        return statistic_dict if len(statistic_dict) > 0 else None

    @classmethod
    def get_nominated_candidates(cls, statistic):
        '''
        Returns information on candidates of nominated pair
        @param statistic Connection statistics (parsed) [dict]
        return Info on local and remote candidate in the form (local, remote)
        '''
        
        candidate_pair_id = cls._get_active_candidate_pair(statistic)
        if candidate_pair_id is None:
            return None, None

        local_candidate_id, remote_candidate_id = \
            cls._get_local_and_remote_candidates(statistic, candidate_pair_id)
        if local_candidate_id is None or remote_candidate_id is None:
            return None, None
        local_candidate = cls._get_local_candidate(
            statistic, local_candidate_id)
        if local_candidate is None:
            return None, None
        remote_candidate = cls._get_remote_candidate(
            statistic, remote_candidate_id)
        if remote_candidate is None:
            return None, None
        return local_candidate, remote_candidate

    @classmethod
    def determine_if_connection_relayed(cls, statistic):
        local_candidate, remote_candidate = \
            cls.get_nominated_candidates(statistic)
        if local_candidate is None or remote_candidate is None:
            return None
        return cls._is_candidate_relayed(local_candidate) or \
               cls._is_candidate_relayed(remote_candidate)

    @classmethod
    def _get_active_candidate_pair(cls, statistic):
        transport = cls._get_active_transport(statistic)
        if transport is None:
            return None
        return transport.get('selectedCandidatePairId', None)

    @staticmethod
    def _get_active_transport(statistic):
        transports = statistic.get('transport', None)
        if transports is None:
            return None
        for transport in transports:
            if transport.get('dtlsState', None) == 'connected' or \
                    transport.get('activeConnection', None) is True:
                return transport
        return None

    @staticmethod
    def _get_local_and_remote_candidates(statistic, candidate_pair_id):
        candidate_pairs = statistic.get('candidate-pair', None)
        if candidate_pairs is None:
            return None, None
        for candidate_pair in candidate_pairs:
            if candidate_pair['id'] == candidate_pair_id:
                return candidate_pair['localCandidateId'], \
                       candidate_pair['remoteCandidateId']
        return None, None

    @staticmethod
    def _get_local_candidate(statistic, candidate_id):
        local_candidates = statistic.get('local-candidate', None)
        if local_candidates is None:
            return None

        for local_candidate in local_candidates:
            if local_candidate['id'] == candidate_id:
                return local_candidate
        return None

    @staticmethod
    def _get_remote_candidate(statistic, candidate_id):
        remote_candidates = statistic.get('remote-candidate', None)
        if remote_candidates is None:
            return None

        for remote_candidate in remote_candidates:
            if remote_candidate['id'] == candidate_id:
                return remote_candidate
        return None

    @staticmethod
    def _is_candidate_relayed(candidate):
        return candidate['candidateType'] == 'relay'
