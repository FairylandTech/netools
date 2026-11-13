local state, leases = KEYS[1], KEYS[2]
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local token, reported = ARGV[1], tonumber(ARGV[2])
redis.call('ZREM', leases, token)
redis.call('ZREMRANGEBYSCORE', leases, '-inf', now)
if reported >= 0 then
    -- Expired/missing state is never recreated from a potentially stale response.
    local remaining = tonumber(redis.call('HGET', state, 'remaining') or '0')
    local conservative = math.max(0, reported - redis.call('ZCARD', leases))
    redis.call('HSET', state, 'remaining', math.min(remaining, conservative),
        'reported_remaining', reported, 'observed_at', now)
end
if ARGV[3] ~= '' then
    local blocked = tonumber(redis.call('HGET', state, 'blocked_until') or '0')
    redis.call('HSET', state, 'last_error', ARGV[3],
        'blocked_until', math.max(blocked, now + tonumber(ARGV[4])))
    if ARGV[3] == 'exhausted' then redis.call('HSET', state, 'remaining', 0) end
end
-- No refund on error/cancellation and no TTL on the balance ledger.
return 1
