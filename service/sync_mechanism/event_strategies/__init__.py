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
from .event_parser import create_strategy_from_database_event
from .event_parser import create_strategy_from_local_event
from .event_parser import create_strategy_from_remote_event
from .event_parser import create_local_stategy_from_event
from .event_parser import splt_move_to_create_delete
from .event_strategy import EventStrategy

__all__ = [
    create_strategy_from_database_event,
    create_strategy_from_local_event,
    create_strategy_from_remote_event,
    create_local_stategy_from_event,
    splt_move_to_create_delete,
    EventStrategy,
]
