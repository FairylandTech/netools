local state, leases, probe_lock = KEYS[1], KEYS[2], KEYS[3]
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local token, reported = ARGV[1], tonumber(ARGV[2])
redis.call('ZREM', leases, token)
redis.call('ZREMRANGEBYSCORE', leases, '-inf', now)
if redis.call('GET', probe_lock) == token then redis.call('DEL', probe_lock) end
if reported >= 0 then
    -- Never replenish from a lagging or out-of-order report within one month.
    local conservative = math.max(0, reported - redis.call('ZCARD', leases))
    local remaining = redis.call('HGET', state, 'remaining')
    if remaining then conservative = math.min(conservative, tonumber(remaining)) end
    redis.call('HSET', state,
        'remaining', conservative,
        'reported_remaining', reported,
        'observed_at', now,
        'last_sync_timestamp_utc', ARGV[3],
        'next_probe_at', now + tonumber(ARGV[4]))
    -- A successful older request must not clear a newer error/cooldown.
elseif ARGV[5] ~= '' then
    local blocked = tonumber(redis.call('HGET', state, 'blocked_until') or '0')
    redis.call('HSET', state, 'last_error', ARGV[5],
        'blocked_until', math.max(blocked, now + tonumber(ARGV[6])))
    if ARGV[5] == 'exhausted' then redis.call('HSET', state, 'remaining', 0) end
end
redis.call('EXPIRE', state, 5356800)
return 1
