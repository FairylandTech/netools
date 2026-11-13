-- Persistent account budget: no calendar reset for prepaid/subscription balances.
local state, leases = KEYS[1], KEYS[2]
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local token, lease_ms = ARGV[1], tonumber(ARGV[2])
local concurrency, rps = tonumber(ARGV[3]), tonumber(ARGV[4])
local margin, initial = tonumber(ARGV[5]), tonumber(ARGV[6])
redis.call('ZREMRANGEBYSCORE', leases, '-inf', now)
local remaining = redis.call('HGET', state, 'remaining')
if not remaining then
    remaining = initial
    redis.call('HSET', state, 'remaining', initial)
end
if tonumber(redis.call('HGET', state, 'blocked_until') or '0') > now then
    return 'cooldown'
end
if tonumber(remaining) <= margin then return 'exhausted' end
if redis.call('ZCARD', leases) >= concurrency then return 'busy' end
local capacity = math.max(1, rps)
local last = tonumber(redis.call('HGET', state, 'token_at') or now)
local tokens = tonumber(redis.call('HGET', state, 'tokens') or tostring(capacity))
tokens = math.min(capacity, tokens + math.max(0, now - last) * rps / 1000)
if tokens < 1 then return 'busy' end
redis.call('HSET', state, 'tokens', tokens - 1, 'token_at', now)
redis.call('HINCRBY', state, 'remaining', -1)
redis.call('ZADD', leases, now + lease_ms, token)
redis.call('PEXPIRE', leases, lease_ms * 2)
return 'ok'
