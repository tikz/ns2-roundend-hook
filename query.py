MAP_EXTENT = 'SELECT minimapExtents FROM RoundInfo where mapName = "{}" limit 1'

ALL_KILLFEED = 'select killerPosition from KillFeed kf inner join RoundInfo ri on ri.roundId = kf.roundId where doerLocation is null and mapName = "{}" and killerTeamNumber = {} and killerLocation is not null'

ROUND_KILLFEED = 'select killerPosition from KillFeed kf inner join RoundInfo ri on ri.roundId = kf.roundId where doerLocation is null and mapName = "{}" and killerTeamNumber = {} and killerLocation is not null and kf.roundId = {}'

ROUND_PLAYERS = 'select * from PlayerRoundStats where roundId = {}'

ROUND_PLAYER_LIFEFORMS = 'select class from PlayerClassStats where roundId = {} and steamId = {} and (class = "Onos" or class = "Fade" or class = "Lerk" or class = "Gorge")'

ROUND_INFO = 'select * from RoundInfo where roundId = {}'

LAST_ROUND = 'select roundId from RoundInfo order by roundId desc limit 1'


ROUNDS_GREATER = 'select roundId from RoundInfo where roundId > {} order by roundId asc'