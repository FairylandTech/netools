-- Account budget, concurrency leases and token bucket change atomically.
local state, leases, probe_lock = KEYS[1], KEYS[2], KEYS[3]
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local mode, token = ARGV[1], ARGV[2]
local lease_ms, concurrency = tonumber(ARGV[3]), tonumber(ARGV[4])
local rps, margin, max_probes = tonumber(ARGV[5]), tonumber(ARGV[6]), tonumber(ARGV[7])
redis.call('ZREMRANGEBYSCORE', leases, '-inf', now)
if tonumber(redis.call('HGET', state, 'blocked_until') or '0') > now then
    return 'cooldown'
end
if mode == 'live' then
    local remaining = redis.call('HGET', state, 'remaining')
    if not remaining then return 'needs_probe' end
    if tonumber(remaining) <= margin then return 'exhausted' end
    if tonumber(redis.call('HGET', state, 'next_probe_at') or '0') <= now then
        return 'needs_probe'
    end
else
    -- Check again inside the lock to coalesce probes across replicas.
    if tonumber(redis.call('HGET', state, 'next_probe_at') or '0') > now then
        return 'fresh'
    end
    if redis.call('EXISTS', probe_lock) == 1 then return 'busy' end
    if tonumber(redis.call('HGET', state, 'probes') or '0') >= max_probes then
        return 'probe_limit'
    end
end
if redis.call('ZCARD', leases) >= concurrency then return 'busy' end
local last = tonumber(redis.call('HGET', state, 'token_at') or now)
local tokens = tonumber(redis.call('HGET', state, 'tokens') or tostring(rps))
local capacity = math.max(1, rps)
tokens = math.min(capacity, tokens + math.max(0, now - last) * rps / 1000)
if tokens < 1 then return 'busy' end
redis.call('HSET', state, 'tokens', tokens - 1, 'token_at', now)
if mode == 'live' then
    redis.call('HINCRBY', state, 'remaining', -1)
else
    redis.call('HINCRBY', state, 'probes', 1)
    redis.call('SET', probe_lock, token, 'PX', lease_ms)
end
redis.call('ZADD', leases, now + lease_ms, token)
redis.call('PEXPIRE', leases, lease_ms * 2)
redis.call('EXPIRE', state, 5356800)
return 'ok'
